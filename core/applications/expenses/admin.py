from django.contrib import admin

from core.applications.expenses.models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    """Read-only: expenses are recorded and voided through the API so the ledger stays in step."""

    list_display = ("expense_number", "organization", "location", "category", "title", "amount", "expense_date", "status")
    list_filter = ("status", "category")
    search_fields = ("expense_number", "title", "paid_to", "reference")
    list_select_related = ("organization", "location")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
