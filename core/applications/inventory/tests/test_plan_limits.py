import uuid
from decimal import Decimal
from django.test import TestCase

from core.applications.inventory.models import Inventory
from core.applications.inventory.services import (
    create_opening_stock,
    request_stock_adjustment,
    single_transfer_stock,
)
from core.applications.products.models import Product
from core.applications.products.services import create_product
from core.applications.subscriptions.models import Plan, Subscription
from core.applications.subscriptions.services import start_subscription
from core.applications.warehouse.models import Warehouse
from core.applications.warehouse.services import create_warehouse
from core.helper.custom_exceptions import BusinessRuleError
from core.helper.enums import ProductStatusChoices, SubscriptionStatus
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory


class PlanLimitEnforcementTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.user.is_superuser = True
        self.user.save()

        # Create custom capped test plan
        self.capped_plan = Plan.objects.create(
            name="Capped Test Plan",
            price=Decimal("10.00"),
            billing_period_days=30,
            max_products=2,
            max_locations=1,
            max_transactions_per_month=2,
            is_active=True,
        )

        self.unlimited_plan = Plan.objects.create(
            name="Unlimited Test Plan",
            price=Decimal("100.00"),
            billing_period_days=30,
            max_products=None,
            max_locations=None,
            max_transactions_per_month=None,
            is_active=True,
        )

        self.org = OrganizationFactory()
        # Override subscription to use capped plan
        self.org.subscription.plan = self.capped_plan
        self.org.subscription.status = SubscriptionStatus.ACTIVE
        self.org.subscription.save()

    def test_max_products_limit_enforced_and_archived_excluded(self):
        # 1st product: success
        p1 = create_product(
            organization=self.org,
            created_by=self.user,
            name="Prod 1",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            customer_sale_price=10.0,
            purchase_cost=5.0,
        )
        self.assertIsNotNone(p1.id)

        # 2nd product: success (reaches limit of 2)
        p2 = create_product(
            organization=self.org,
            created_by=self.user,
            name="Prod 2",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            customer_sale_price=10.0,
            purchase_cost=5.0,
        )
        self.assertIsNotNone(p2.id)

        # 3rd product: must raise PLAN_LIMIT_REACHED
        with self.assertRaises(BusinessRuleError) as ctx:
            create_product(
                organization=self.org,
                created_by=self.user,
                name="Prod 3",
                sku=f"SKU-{uuid.uuid4().hex[:6]}",
                customer_sale_price=10.0,
                purchase_cost=5.0,
            )
        self.assertEqual(ctx.exception.code, "PLAN_LIMIT_REACHED")

        # Archive p1: frees up 1 slot
        p1.status = ProductStatusChoices.ARCHIVED
        p1.save(update_fields=["status"])

        # Now creating p3 succeeds
        p3 = create_product(
            organization=self.org,
            created_by=self.user,
            name="Prod 3",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            customer_sale_price=10.0,
            purchase_cost=5.0,
        )
        self.assertIsNotNone(p3.id)

    def test_max_products_unlimited_plan_allows_many(self):
        unlimited_org = OrganizationFactory()
        unlimited_org.subscription.plan = self.unlimited_plan
        unlimited_org.subscription.save()

        for i in range(5):
            create_product(
                organization=unlimited_org,
                created_by=self.user,
                name=f"Unlimited Prod {i}",
                sku=f"SKU-UNLTD-{i}-{uuid.uuid4().hex[:4]}",
                customer_sale_price=10.0,
                purchase_cost=5.0,
            )
        self.assertEqual(Product.objects.filter(organization=unlimited_org).count(), 5)

    def test_max_locations_limit_enforced_and_inactive_excluded(self):
        # 1st warehouse: success (reaches limit of 1)
        wh1 = create_warehouse(
            organization=self.org,
            name="Warehouse 1",
            code=f"WH-1-{uuid.uuid4().hex[:4]}",
        )
        self.assertIsNotNone(wh1.id)

        # 2nd warehouse: must raise PLAN_LIMIT_REACHED
        with self.assertRaises(BusinessRuleError) as ctx:
            create_warehouse(
                organization=self.org,
                name="Warehouse 2",
                code=f"WH-2-{uuid.uuid4().hex[:4]}",
            )
        self.assertEqual(ctx.exception.code, "PLAN_LIMIT_REACHED")

        # Deactivate wh1: frees up location slot
        wh1.is_active = False
        wh1.save(update_fields=["is_active"])

        # Creating wh2 now succeeds
        wh2 = create_warehouse(
            organization=self.org,
            name="Warehouse 2",
            code=f"WH-2-{uuid.uuid4().hex[:4]}",
        )
        self.assertIsNotNone(wh2.id)

    def test_max_transactions_per_month_enforced_on_inventory_movements(self):
        # Wh1 created
        wh1 = create_warehouse(
            organization=self.org,
            name="Warehouse 1",
            code=f"WH-TRX-1-{uuid.uuid4().hex[:4]}",
        )
        p1 = create_product(
            organization=self.org,
            created_by=self.user,
            name="Prod 1",
            sku=f"SKU-TRX-{uuid.uuid4().hex[:4]}",
            customer_sale_price=10.0,
            purchase_cost=5.0,
        )

        # Transaction 1: Opening stock (uses 1 transaction)
        create_opening_stock(p1, None, None, wh1, 50, self.user)

        # Deactivate wh1 to allow creating wh2
        wh1.is_active = False
        wh1.save(update_fields=["is_active"])
        wh2 = create_warehouse(
            organization=self.org,
            name="Warehouse 2",
            code=f"WH-TRX-2-{uuid.uuid4().hex[:4]}",
        )
        wh1.is_active = True
        wh1.save(update_fields=["is_active"])

        # Transaction 2: Transfer stock (uses 2nd transaction -> limit of 2 reached)
        single_transfer_stock(wh1, wh2, p1, None, None, 10, self.user)

        # Transaction 3: Attempting another transfer or adjustment must fail with PLAN_LIMIT_REACHED
        with self.assertRaises(BusinessRuleError) as ctx:
            single_transfer_stock(wh1, wh2, p1, None, None, 5, self.user)
        self.assertEqual(ctx.exception.code, "PLAN_LIMIT_REACHED")

    def test_inactive_subscription_blocks_inventory_operations(self):
        # Expire subscription
        self.org.subscription.status = SubscriptionStatus.CANCELED
        self.org.subscription.save()

        # Product creation blocked
        with self.assertRaises(BusinessRuleError) as ctx:
            create_product(
                organization=self.org,
                created_by=self.user,
                name="Blocked Product",
                sku=f"SKU-BLOCK-{uuid.uuid4().hex[:4]}",
                customer_sale_price=10.0,
                purchase_cost=5.0,
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION_REQUIRED")

        # Warehouse creation blocked
        with self.assertRaises(BusinessRuleError) as ctx:
            create_warehouse(
                organization=self.org,
                name="Blocked WH",
                code=f"WH-BLOCK-{uuid.uuid4().hex[:4]}",
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION_REQUIRED")
