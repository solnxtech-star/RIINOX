from __future__ import annotations

from decimal import Decimal
import factory
from factory.django import DjangoModelFactory

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory
from core.helper.enums import CustomerStatus, CustomerType


class CustomerFactory(DjangoModelFactory):
    class Meta:
        model = Customer

    organization = factory.SubFactory(OrganizationFactory)
    created_by = factory.SubFactory(UserFactory)
    customer_type = CustomerType.INDIVIDUAL
    name = factory.Faker("name")
    company_name = factory.Faker("company")
    email = factory.Sequence(lambda n: f"customer_{n}@example.com")
    phone = factory.Sequence(lambda n: f"+23480{n:08d}")
    tax_id = factory.Sequence(lambda n: f"TIN-{n:06d}")
    credit_limit = Decimal("0.00")
    balance = Decimal("0.00")
    payment_terms_days = 0
    status = CustomerStatus.ACTIVE
    notes = "Standard customer account"
    metadata = factory.LazyFunction(dict)


class CustomerAddressFactory(DjangoModelFactory):
    class Meta:
        model = CustomerAddress

    customer = factory.SubFactory(CustomerFactory)
    address_type = CustomerAddress.AddressType.SHIPPING
    street = factory.Faker("street_address")
    city = factory.Faker("city")
    state = factory.Faker("state")
    postal_code = factory.Faker("postcode")
    country = "Nigeria"
    is_default = False
