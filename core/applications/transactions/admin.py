from django.contrib import admin

update adminfrom core.applications.transactions.models import Customer, FinancialLedgerEntry, Transaction


class FinancialLedgerEntryInline(admin.TabularInline):
    model = FinancialLedgerEntry
    extra = 0
    fields = ("account_name", "debit", "credit", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_id",
        "organization",
        "transaction_type",
        "status",
        "created_by",
        "created_at",
    )
    list_filter = ("transaction_type", "status", "organization")
    search_fields = ("transaction_id", "notes", "organization__name")
    list_select_related = ("organization", "created_by", "content_type")
    raw_id_fields = ("organization", "created_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [FinancialLedgerEntryInline]


@admin.register(FinancialLedgerEntry)
class FinancialLedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "transaction",
        "account_name",
        "debit",
        "credit",
        "created_at",
    )
    list_filter = ("account_name", "transaction__transaction_type")
    search_fields = ("transaction__transaction_id",)
    list_select_related = ("transaction",)
    raw_id_fields = ("transaction",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "organization",
        "customer_type",
        "email",
        "phone",
        "balance",
        "status",
        "created_at",
    )
    list_filter = ("customer_type", "status", "organization")
    search_fields = ("name", "email", "phone", "organization__name")
    list_select_related = ("organization", "linked_user")
    raw_id_fields = ("organization", "linked_user")
    readonly_fields = ("created_at", "updated_at")
