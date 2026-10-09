import factory
from factory.django import DjangoModelFactory
from core.applications.users.models import Organization, User
from core.applications.products.models import Product, ProductVariant, ProductBatch
from core.applications.warehouse.models import Warehouse, StockLocation
from core.applications.inventory.models import Inventory, InventoryLedgerEntry, StockAdjustmentRequest
from core.applications.users.tests.factories import UserFactory

class OrganizationFactory(DjangoModelFactory):
    class Meta:
        model = Organization
    name = factory.Faker('company')
    email = factory.Faker('email')

    @factory.post_generation
    def subscription(self, create, extracted, **kwargs):
        if not create:
            return
        from core.applications.subscriptions.models import Subscription
        if not Subscription.objects.filter(organization=self).exists():
            from core.applications.subscriptions.services import start_subscription
            start_subscription(self)


class WarehouseFactory(DjangoModelFactory):
    class Meta:
        model = Warehouse
    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Faker('company')
    code = factory.Sequence(lambda n: f"WH-{n}")

class StockLocationFactory(DjangoModelFactory):
    class Meta:
        model = StockLocation
    warehouse = factory.SubFactory(WarehouseFactory)
    name = factory.Faker('word')

class ProductFactory(DjangoModelFactory):
    class Meta:
        model = Product
    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Faker('word')
    sku = factory.Sequence(lambda n: f"SKU-{n}")
    purchase_cost = 10.00
    customer_sale_price = 15.00
    track_inventory = True

class ProductVariantFactory(DjangoModelFactory):
    class Meta:
        model = ProductVariant
    product = factory.SubFactory(ProductFactory)
    name = factory.Faker('word')
    sku = factory.Sequence(lambda n: f"VSKU-{n}")

class ProductBatchFactory(DjangoModelFactory):
    class Meta:
        model = ProductBatch
    product = factory.SubFactory(ProductFactory)
    batch_number = factory.Sequence(lambda n: f"BATCH-{n}")

class InventoryFactory(DjangoModelFactory):
    class Meta:
        model = Inventory
    product = factory.SubFactory(ProductFactory)
    warehouse = factory.SubFactory(WarehouseFactory)
    quantity = 100

class StockAdjustmentRequestFactory(DjangoModelFactory):
    class Meta:
        model = StockAdjustmentRequest
    product = factory.SubFactory(ProductFactory)
    warehouse = factory.SubFactory(WarehouseFactory)
    requested_quantity_change = 10
    requested_by = factory.SubFactory(UserFactory)
