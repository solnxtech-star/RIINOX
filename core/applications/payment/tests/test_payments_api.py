"""Customer payments API: recording against invoices, retries, rules, listing, permissions, locations."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.invoice.models import Invoice
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.payment.models import InvoicePayment
from core.applications.payment.models import Payment
from core.applications.sales.models import Sale
from core.applications.transactions.models import FinancialLedgerEntry
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import AccountNameChoices
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import SalePaymentStatusChoices


def dashed(pk) -> str:
    return str(uuid.UUID(str(pk)))


class PaymentAPITestBase(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        roles = seed_default_roles(self.org)

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK", is_default=True)
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")
        self.customer = Customer.objects.create(organization=self.org, name="Adaeze Stores")

        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])
        self.front_desk = UserFactory()  # RECORD_PAYMENT + VIEW_RECEIPTS, no VIEW_PAYMENTS
        Membership.objects.create(user=self.front_desk, organization=self.org, role=roles["front-desk"])
        self.accountant = UserFactory()  # can view, can't record
        Membership.objects.create(user=self.accountant, organization=self.org, role=roles["accountant"])
        self.inventory_staff = UserFactory()  # no payment permissions at all
        Membership.objects.create(user=self.inventory_staff, organization=self.org, role=roles["inventory-staff"])
        self.lekki_rep = UserFactory()
        membership = Membership.objects.create(
            user=self.lekki_rep, organization=self.org, role=roles["sales-representative"], all_locations=False
        )
        MembershipLocation.objects.create(membership=membership, location=self.lekki)

        self.invoice = self._invoice(total="50000.00")
        self.url = f"/api/organizations/{dashed(self.org.pk)}/payments/"
        self.client = APIClient()

    def _invoice(self, *, total, warehouse=None, status=InvoiceStatusChoices.CONFIRMED, sale=None, org=None,
                 customer=None):
        return Invoice.objects.create(
            organization=org or self.org,
            customer=customer or self.customer,
            warehouse=warehouse or self.lekki,
            sale=sale,
            invoice_number=f"INV-{uuid.uuid4().hex[:6].upper()}",
            due_date=timezone.localdate(),
            status=status,
            subtotal=Decimal(total),
            total=Decimal(total),
            currency="NGN",
        )

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _pay(self, user=None, invoice=None, **body):
        body.setdefault("invoice_id", dashed((invoice or self.invoice).pk))
        body.setdefault("method", "bank_transfer")
        return self._as(user or self.owner).post(self.url, body, format="json")


class RecordPaymentTests(PaymentAPITestBase):
    def test_part_payments_then_full(self):
        first = self._pay(amount="20000.00", reference="TRF-1", note="First instalment")
        self.assertEqual(first.status_code, 201, first.content)
        body = first.json()
        self.assertTrue(body["receipt_number"].startswith("R-"))
        self.assertEqual(body["amount"], "20000.00")
        self.assertEqual(body["balance_after"], "30000.00")
        self.assertEqual(body["reference"], "TRF-1")
        self.assertEqual(body["note"], "First instalment")
        self.assertEqual(body["invoice"]["status"], InvoiceStatusChoices.PARTIALLY_PAID)
        self.assertEqual(body["invoice"]["balance_due"], "30000.00")
        self.assertFalse(body["replayed"])

        second = self._pay(method="pos")  # amount left out: the whole balance
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(second.json()["amount"], "30000.00")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("50000.00"))
        self.assertEqual(self.invoice.status, InvoiceStatusChoices.PAID)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))

    def test_ledger_and_audit(self):
        receipt_id = self._pay(amount="20000.00").json()["id"]
        payment = InvoicePayment.objects.get(pk=receipt_id).payment
        entries = FinancialLedgerEntry.objects.filter(transaction__object_id__in=[str(payment.pk), payment.pk.hex])
        self.assertEqual(
            {(e.account_name, e.debit, e.credit) for e in entries},
            {
                (AccountNameChoices.CASH_BANK, Decimal("20000.00"), Decimal("0.00")),
                (AccountNameChoices.ACCOUNTS_RECEIVABLE, Decimal("0.00"), Decimal("20000.00")),
            },
        )
        log = AuditLog.objects.get(action=AuditAction.INVOICE_PAYMENT_RECORDED)
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.new_values["amount_paid"], "20000.00")

    def test_cash_overpayment_gives_change_other_methods_refused(self):
        response = self._pay(method="transfer_typo", amount="1.00")
        self.assertEqual(response.status_code, 400)

        response = self._pay(method="card", amount="60000.00")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "PAYMENT_EXCEEDS_BALANCE")

        response = self._pay(method="cash", amount="60000.00")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["amount"], "50000.00")
        self.assertEqual(response.json()["change_due"], "10000.00")

    def test_not_payable_invoices(self):
        for invoice_status in (InvoiceStatusChoices.DRAFT,):
            invoice = self._invoice(total="100.00", status=invoice_status)
            response = self._pay(invoice=invoice)
            self.assertEqual(response.status_code, 409, invoice_status)
            self.assertEqual(response.json()["code"], "INVOICE_NOT_PAYABLE")

        self._pay()
        response = self._pay()  # already fully paid
        self.assertEqual(response.json()["code"], "INVOICE_NOT_PAYABLE")

    def test_same_reference_is_a_safe_retry(self):
        first = self._pay(amount="20000.00", reference="TRF-9")
        retry = self._pay(amount="20000.00", reference="TRF-9")
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()["replayed"])
        self.assertEqual(retry.json()["id"], first.json()["id"])
        self.assertEqual(InvoicePayment.objects.filter(invoice=self.invoice).count(), 1)

        other_invoice = self._invoice(total="100.00")
        clash = self._pay(invoice=other_invoice, reference="TRF-9")
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(clash.json()["code"], "DUPLICATE_PAYMENT_REFERENCE")
        clash = self._pay(method="cash", amount="1.00", reference="TRF-9")  # same invoice, other method
        self.assertEqual(clash.json()["code"], "DUPLICATE_PAYMENT_REFERENCE")
        self.assertEqual(Payment.objects.filter(reference="TRF-9").count(), 1)

    def test_unknown_fields_and_bad_amount_are_400(self):
        response = self._pay(amount="0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.json()["errors"])
        response = self._pay(amount_paid="10.00")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount_paid", response.json()["errors"])

    def test_sale_payment_status_follows_the_invoice(self):
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            customer=self.customer,
            warehouse=self.lekki,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            payment_status=SalePaymentStatusChoices.UNPAID,
        )
        invoice = self._invoice(total="100.00", sale=sale)
        self._pay(invoice=invoice, amount="40.00")
        sale.refresh_from_db()
        self.assertEqual(sale.payment_status, SalePaymentStatusChoices.PARTIALLY_PAID)
        self._pay(invoice=invoice)
        sale.refresh_from_db()
        self.assertEqual(sale.payment_status, SalePaymentStatusChoices.PAID)


class AccessTests(PaymentAPITestBase):
    def test_permissions(self):
        self.assertEqual(self._pay(user=self.accountant).status_code, 403)
        self.assertEqual(self._pay(user=self.inventory_staff).status_code, 403)
        self.assertEqual(self._as(self.inventory_staff).get(self.url).status_code, 403)

        self.assertEqual(self._pay(user=self.front_desk, amount="10.00").status_code, 201)
        self.assertEqual(self._as(self.front_desk).get(self.url).status_code, 200)  # VIEW_RECEIPTS
        self.assertEqual(self._as(self.accountant).get(self.url).json()["count"], 1)  # VIEW_PAYMENTS

    def test_other_organizations_invoice_is_not_found(self):
        other_org = OrganizationFactory()
        other_warehouse = Warehouse.objects.create(organization=other_org, name="X", code="X")
        other_customer = Customer.objects.create(organization=other_org, name="Other")
        foreign = self._invoice(total="100.00", org=other_org, warehouse=other_warehouse, customer=other_customer)
        response = self._pay(invoice=foreign)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "INVOICE_NOT_FOUND")

        outsider = UserFactory()
        self.assertEqual(self._as(outsider).get(self.url).status_code, 404)

    def test_location_scope(self):
        ikeja_invoice = self._invoice(total="100.00", warehouse=self.ikeja)
        self.assertEqual(self._pay(user=self.lekki_rep, invoice=ikeja_invoice).status_code, 404)
        self.assertEqual(self._pay(user=self.lekki_rep, amount="10.00").status_code, 201)
        ikeja_receipt = self._pay(invoice=ikeja_invoice).json()["id"]  # owner

        listed = self._as(self.lekki_rep).get(self.url).json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(self._as(self.lekki_rep).get(f"{self.url}{ikeja_receipt}/").status_code, 404)
        self.assertEqual(self._as(self.owner).get(self.url).json()["count"], 2)


class ListTests(PaymentAPITestBase):
    def test_filters_search_and_detail(self):
        second_invoice = self._invoice(total="300.00")
        self._pay(amount="100.00", reference="TRF-A")
        self._pay(invoice=second_invoice, method="cash", amount="300.00")
        client = self._as(self.owner)

        self.assertEqual(client.get(self.url).json()["count"], 2)
        by_invoice = client.get(self.url, {"invoice": dashed(self.invoice.pk)}).json()
        self.assertEqual([row["reference"] for row in by_invoice["results"]], ["TRF-A"])
        self.assertEqual(client.get(self.url, {"method": "cash"}).json()["count"], 1)
        self.assertEqual(client.get(self.url, {"search": second_invoice.invoice_number}).json()["count"], 1)
        self.assertEqual(client.get(self.url, {"customer": dashed(self.customer.pk)}).json()["count"], 2)
        today = timezone.localdate().isoformat()
        self.assertEqual(client.get(self.url, {"date_from": today, "date_to": today}).json()["count"], 2)

        self.assertEqual(client.get(self.url, {"method": "bitcoin"}).status_code, 400)
        self.assertEqual(client.get(self.url, {"invoice": "nope"}).status_code, 400)

        row = by_invoice["results"][0]
        detail = client.get(f"{self.url}{row['id']}/").json()
        self.assertEqual(detail["customer"]["name"], "Adaeze Stores")
        self.assertEqual(detail["location"]["name"], "Lekki")

    def test_no_edit_or_delete(self):
        receipt_id = self._pay(amount="10.00").json()["id"]
        client = self._as(self.owner)
        self.assertEqual(client.patch(f"{self.url}{receipt_id}/", {"amount": "1"}, format="json").status_code, 405)
        self.assertEqual(client.delete(f"{self.url}{receipt_id}/").status_code, 405)
