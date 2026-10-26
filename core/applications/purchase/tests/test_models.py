from django.core.exceptions import ValidationError
from django.test import TestCase

from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    UserFactory,
    WarehouseFactory,
)
from core.applications.invoice.models import PurchaseInvoice
from core.applications.purchase.models import (
    Purchase,
    PurchaseItem,
    PurchaseReturn,
)
from core.applications.vendors.models import Vendor
from core.helper.enums import PurchasePaymentStatusChoices, PurchaseStatusChoices


class PurchaseModelTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory(name="Alpha Org")
        self.other_org = OrganizationFactory(name="Beta Org")
        self.user = UserFactory()
        self.warehouse = WarehouseFactory(organization=self.org)
        self.other_warehouse = WarehouseFactory(organization=self.other_org)
        self.vendor = Vendor.objects.create(organization=self.org, company_name="Alpha Supplies")
        self.other_vendor = Vendor.objects.create(organization=self.other_org, company_name="Beta Supplies")
        self.product = ProductFactory(organization=self.org)
        self.other_product = ProductFactory(organization=self.other_org)

    def test_purchase_clean_rejects_cross_tenant_vendor_or_warehouse(self):
        po = Purchase(
            organization=self.org,
            vendor=self.other_vendor,
            warehouse=self.other_warehouse,
            ordered_by=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            po.clean()
        self.assertIn("vendor", ctx.exception.message_dict)
        self.assertIn("warehouse", ctx.exception.message_dict)

    def test_purchase_item_clean_rejects_cross_tenant_product(self):
        po = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.user,
        )
        item = PurchaseItem(
            purchase=po,
            product=self.other_product,
            quantity_ordered=10,
            purchase_cost="100.00",
        )
        with self.assertRaises(ValidationError) as ctx:
            item.clean()
        self.assertIn("product", ctx.exception.message_dict)

    def test_purchase_sequential_number_and_str_includes_organization(self):
        po = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.user,
        )
        self.assertTrue(po.purchase_number.startswith("PO-"))
        self.assertEqual(po.status, PurchaseStatusChoices.DRAFT)
        self.assertEqual(po.payment_status, PurchasePaymentStatusChoices.UNPAID)
        self.assertIn(self.org.name, str(po))

    def test_purchase_return_clean_and_sequential_number(self):
        po = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.user,
        )
        ret = PurchaseReturn.objects.create(
            organization=self.org,
            purchase=po,
            returned_by=self.user,
            reason="Damaged packaging",
        )
        self.assertTrue(ret.return_number.startswith("PRET-"))
        self.assertIn(self.org.name, str(ret))

        invalid_ret = PurchaseReturn(
            organization=self.other_org,
            purchase=po,
            returned_by=self.user,
        )
        with self.assertRaises(ValidationError):
            invalid_ret.clean()

    def test_purchase_invoice_clean_rejects_cross_tenant_links(self):
        po = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.user,
        )
        inv = PurchaseInvoice(
            organization=self.other_org,
            vendor=self.vendor,
            purchase=po,
            warehouse=self.warehouse,
            invoice_number="PINV-2026-0001",
        )
        with self.assertRaises(ValidationError) as ctx:
            inv.clean()
        self.assertIn("vendor", ctx.exception.message_dict)
        self.assertIn("purchase", ctx.exception.message_dict)
        self.assertIn("warehouse", ctx.exception.message_dict)

