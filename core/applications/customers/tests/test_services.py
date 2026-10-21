from __future__ import annotations

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.customers.services import (
    adjust_customer_credit,
    create_customer,
    delete_customer,
    update_customer,
)
from core.applications.customers.tests.factories import CustomerFactory
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.sales.models import Sale
from core.applications.warehouse.models import StockLocation, Warehouse
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.services.entitlements import invalidate_entitlements
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.helper.custom_exceptions import BusinessRuleError
from core.helper.enums import CustomerStatus, CustomerType, PermissionCode, SubscriptionStatus


class CustomerServiceTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        # Owner has full permissions including MANAGE_CUSTOMER_CREDIT
        self.owner = UserFactory()
        Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        # Sales Rep has MANAGE_CUSTOMERS but lacks MANAGE_CUSTOMER_CREDIT
        self.sales_rep = UserFactory()
        Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # Staff has no customer permissions
        self.staff_user = UserFactory()
        Membership.objects.create(
            user=self.staff_user,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )

        # Other organization for tenant tests
        self.other_org = OrganizationFactory()
        self.other_roles = seed_default_roles(self.other_org)
        self.other_owner = UserFactory()
        Membership.objects.create(
            user=self.other_owner,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

    # -------------------------------------------------------------------------
    # create_customer tests
    # -------------------------------------------------------------------------
    def test_create_customer_success(self):
        customer = create_customer(
            organization=self.org,
            user=self.sales_rep,
            name="John Doe",
            customer_type=CustomerType.INDIVIDUAL,
            email="johndoe@example.com",
            phone="+2348011223344",
            metadata={"loyalty_tier": "gold"},
            addresses=[
                {
                    "address_type": CustomerAddress.AddressType.BILLING,
                    "street": "12 Marina Road",
                    "city": "Lagos",
                    "state": "Lagos",
                    "country": "Nigeria",
                    "is_default": True,
                }
            ],
        )

        self.assertIsInstance(customer, Customer)
        self.assertEqual(customer.name, "John Doe")
        self.assertEqual(customer.organization_id, self.org.id)
        self.assertEqual(customer.created_by_id, self.sales_rep.id)
        self.assertEqual(customer.email, "johndoe@example.com")
        self.assertEqual(customer.credit_limit, Decimal("0.00"))
        self.assertEqual(customer.balance, Decimal("0.00"))
        self.assertEqual(customer.addresses.count(), 1)
        self.assertEqual(customer.addresses.first().city, "Lagos")
        self.assertIsNotNone(customer.primary_address)
        self.assertEqual(customer.primary_address.city, "Lagos")
        self.assertIn("12 Marina Road", customer.formatted_address)

        # Verify audit record
        audit = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.CUSTOMER_CREATED,
            resource_id=str(customer.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.sales_rep)

    def test_create_customer_without_manage_permission_raises(self):
        with self.assertRaises(PermissionDenied):
            create_customer(
                organization=self.org,
                user=self.staff_user,
                name="Denied Customer",
            )

    def test_create_customer_inactive_subscription_raises(self):
        sub = Subscription.objects.get(organization=self.org)
        sub.status = SubscriptionStatus.CANCELED
        sub.save(update_fields=["status"])
        invalidate_entitlements(self.org)

        with self.assertRaises(BusinessRuleError):
            create_customer(
                organization=self.org,
                user=self.owner,
                name="Subscription Blocked",
            )

    def test_create_customer_credit_without_permission_raises(self):
        # Sales Rep lacks MANAGE_CUSTOMER_CREDIT
        with self.assertRaises(PermissionDenied):
            create_customer(
                organization=self.org,
                user=self.sales_rep,
                name="Credit Seeker",
                credit_limit=Decimal("50000.00"),
            )

        with self.assertRaises(PermissionDenied):
            create_customer(
                organization=self.org,
                user=self.sales_rep,
                name="Terms Seeker",
                payment_terms_days=30,
            )

    def test_create_customer_credit_with_permission_succeeds(self):
        # Owner has MANAGE_CUSTOMER_CREDIT
        customer = create_customer(
            organization=self.org,
            user=self.owner,
            name="Approved Credit Customer",
            credit_limit=Decimal("100000.00"),
            payment_terms_days=30,
        )
        self.assertEqual(customer.credit_limit, Decimal("100000.00"))
        self.assertEqual(customer.payment_terms_days, 30)

    def test_create_customer_negative_credit_raises(self):
        with self.assertRaises(ValidationError):
            create_customer(
                organization=self.org,
                user=self.owner,
                name="Negative Limit",
                credit_limit=Decimal("-500.00"),
            )

    def test_create_customer_duplicate_email_or_phone_in_same_org_raises(self):
        create_customer(
            organization=self.org,
            user=self.sales_rep,
            name="Existing Cust",
            email="repeat@example.com",
            phone="+2348000000001",
        )

        with self.assertRaises(ValidationError) as ctx_email:
            create_customer(
                organization=self.org,
                user=self.sales_rep,
                name="Another Cust",
                email="repeat@example.com",
            )
        self.assertIn("email", ctx_email.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx_phone:
            create_customer(
                organization=self.org,
                user=self.sales_rep,
                name="Another Cust 2",
                phone="+2348000000001",
            )
        self.assertIn("phone", ctx_phone.exception.message_dict)

    def test_create_customer_same_email_in_different_org_allowed(self):
        create_customer(
            organization=self.org,
            user=self.sales_rep,
            name="Org A Customer",
            email="shared@example.com",
        )

        other_customer = create_customer(
            organization=self.other_org,
            user=self.other_owner,
            name="Org B Customer",
            email="shared@example.com",
        )
        self.assertEqual(other_customer.organization_id, self.other_org.id)

    # -------------------------------------------------------------------------
    # update_customer tests
    # -------------------------------------------------------------------------
    def test_update_customer_basic_fields(self):
        customer = CustomerFactory(organization=self.org, name="Original Name")

        updated = update_customer(
            customer=customer,
            user=self.sales_rep,
            name="Updated Name",
            notes="Added special delivery notes",
        )
        self.assertEqual(updated.name, "Updated Name")
        self.assertEqual(updated.notes, "Added special delivery notes")

        audit = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.CUSTOMER_UPDATED,
            resource_id=str(customer.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.sales_rep)

    def test_update_customer_credit_limit_requires_permission(self):
        customer = CustomerFactory(organization=self.org, credit_limit=Decimal("0.00"))

        with self.assertRaises(PermissionDenied):
            update_customer(
                customer=customer,
                user=self.sales_rep,
                credit_limit=Decimal("50000.00"),
            )

        updated = update_customer(
            customer=customer,
            user=self.owner,
            credit_limit=Decimal("50000.00"),
        )
        self.assertEqual(updated.credit_limit, Decimal("50000.00"))

    def test_update_customer_balance_is_immutable(self):
        customer = CustomerFactory(organization=self.org, balance=Decimal("0.00"))
        # Attempt to forge balance
        updated = update_customer(
            customer=customer,
            user=self.owner,
            balance=Decimal("99999.00"),
        )
        self.assertEqual(updated.balance, Decimal("0.00"))

    # -------------------------------------------------------------------------
    # delete_customer tests (PRD §44 Graceful Fallback)
    # -------------------------------------------------------------------------
    def test_delete_unreferenced_customer_hard_deletes(self):
        customer = CustomerFactory(organization=self.org)
        customer_id = customer.id

        deleted, result = delete_customer(customer=customer, user=self.owner)
        self.assertTrue(deleted)
        self.assertFalse(Customer.objects.filter(id=customer_id).exists())

        audit = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.CUSTOMER_DELETED,
        ).first()
        self.assertIsNotNone(audit)

    def test_delete_referenced_customer_soft_deactivates(self):
        customer = CustomerFactory(organization=self.org)
        warehouse = Warehouse.objects.create(organization=self.org, name="Main WH", code="WH-01")
        loc = StockLocation.objects.create(warehouse=warehouse, name="Bay 1")

        # Create a sale referencing customer
        Sale.objects.create(
            sale_id="SALE-TEST-001",
            organization=self.org,
            customer=customer,
            location=loc,
            sales_rep=self.owner,
        )

        deleted, result = delete_customer(customer=customer, user=self.owner)
        self.assertFalse(deleted)
        customer.refresh_from_db()
        self.assertEqual(customer.status, CustomerStatus.INACTIVE)

        audit = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.CUSTOMER_DEACTIVATED,
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn(audit.resource_id, [str(customer.id), str(customer.id).replace("-", "")])

    # -------------------------------------------------------------------------
    # adjust_customer_credit tests (PRD §26)
    # -------------------------------------------------------------------------
    def test_adjust_customer_credit_success(self):
        customer = CustomerFactory(organization=self.org, credit_limit=Decimal("10000.00"))

        adjusted = adjust_customer_credit(
            customer=customer,
            user=self.owner,
            credit_limit=Decimal("50000.00"),
            payment_terms_days=45,
            reason="Approved after credit assessment",
        )
        self.assertEqual(adjusted.credit_limit, Decimal("50000.00"))
        self.assertEqual(adjusted.payment_terms_days, 45)

        audit = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.CUSTOMER_CREDIT_ADJUSTED,
            resource_id=str(customer.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.previous_values["credit_limit"], "10000.00")
        self.assertEqual(audit.new_values["credit_limit"], "50000.00")
        self.assertEqual(audit.reason, "Approved after credit assessment")

    def test_adjust_customer_credit_denied_without_permission(self):
        customer = CustomerFactory(organization=self.org)

        with self.assertRaises(PermissionDenied):
            adjust_customer_credit(
                customer=customer,
                user=self.sales_rep,
                credit_limit=Decimal("50000.00"),
            )
