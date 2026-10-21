from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from core.applications.customers.models import Customer, CustomerAddress


class CustomerAddressInline(TabularInline):
    model = CustomerAddress
    extra = 0
    fields = ("address_type", "street", "city", "state", "country", "is_default")


@admin.register(Customer)
class CustomerAdmin(ModelAdmin):
    list_display = (
        "id",
        "name",
        "company_name",
        "organization",
        "customer_type",
        "credit_limit",
        "balance",
        "status",
        "created_at",
    )
    list_filter = ("organization", "customer_type", "status")
    search_fields = ("name", "company_name", "email", "phone", "tax_id")
    list_select_related = ("organization", "created_by")
    readonly_fields = ("balance", "created_at", "updated_at")
    inlines = [CustomerAddressInline]


@admin.register(CustomerAddress)
class CustomerAddressAdmin(ModelAdmin):
    list_display = ("id", "customer", "address_type", "street", "city", "country", "is_default")
    list_filter = ("address_type", "is_default")
    search_fields = ("street", "city", "customer__name")
    list_select_related = ("customer",)
