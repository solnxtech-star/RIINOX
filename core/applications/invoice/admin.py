from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    Client,
    DocumentSequence,
    DocumentTemplate,
    Invoice,
    InvoiceItem,
)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    show_change_link = True

    fields = (
        "product",
        "description",
        "quantity",
        "unit_price",
        "total_display",
    )

    readonly_fields = ("total_display",)

    @admin.display(description=_("Line Total"))
    def total_display(self, obj):
        if not obj.pk:
            return "-"
        return obj.total


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "organization",
        "client_type",
        "credit_limit",
        "current_balance",
        "status",
        "is_active",
    )

    list_filter = (
        "status",
        "client_type",
        "is_active",
        "organization",
    )

    search_fields = (
        "name",
        "email",
        "phone",
        "organization__name",
    )

    readonly_fields = (
        "current_balance",
        "created_at",
        "updated_at",
    )

    autocomplete_fields = (
        "organization",
    )

    ordering = (
        "name",
    )

    list_select_related = (
        "organization",
    )

    fieldsets = (
        (
            _("Client Information"),
            {
                "fields": (
                    "organization",
                    "name",
                    "email",
                    "phone",
                    "address",
                    "client_type",
                )
            },
        ),
        (
            _("Credit & Balance"),
            {
                "fields": (
                    "credit_limit",
                    "current_balance",
                )
            },
        ),
        (
            _("Status"),
            {
                "fields": (
                    "status",
                    "is_active",
                )
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "client",
        "organization",
        "warehouse",
        "sales_rep",
        "status",
        "total",
        "due_date",
        "is_voided",
        "created_at",
    )

    list_filter = (
        "status",
        "is_voided",
        "organization",
        "warehouse",
        "due_date",
        "created_at",
    )

    search_fields = (
        "invoice_number",
        "client__name",
        "client__email",
        "sales_rep__email",
        "confirmed_by__email",
    )

    date_hierarchy = "created_at"

    ordering = (
        "-created_at",
    )

    autocomplete_fields = (
        "client",
        "organization",
        "warehouse",
        "sales_rep",
        "confirmed_by",
        "voided_by",
        "replaces",
    )

    readonly_fields = (
        "subtotal",
        "discount_amount",
        "tax_amount",
        "total",
        "created_at",
        "updated_at",
        "computed_subtotal",
    )

    list_select_related = (
        "client",
        "organization",
        "warehouse",
        "sales_rep",
        "confirmed_by",
        "voided_by",
        "replaces",
    )

    inlines = (
        InvoiceItemInline,
    )

    fieldsets = (
        (
            _("Invoice Information"),
            {
                "fields": (
                    "invoice_number",
                    "client",
                    "organization",
                    "warehouse",
                    "due_date",
                    "status",
                )
            },
        ),
        (
            _("Sales & Authorization"),
            {
                "fields": (
                    "sales_rep",
                    "confirmed_by",
                )
            },
        ),
        (
            _("Financial Summary"),
            {
                "fields": (
                    "subtotal",
                    "discount_amount",
                    "tax_amount",
                    "total",
                    "computed_subtotal",
                )
            },
        ),
        (
            _("Void / Correction"),
            {
                "fields": (
                    "is_voided",
                    "voided_by",
                    "voided_at",
                    "void_reason",
                    "replaces",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": (
                    "notes",
                    "extra_data",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Computed Subtotal"))
    def computed_subtotal(self, obj):
        return obj.computed_subtotal


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "product",
        "quantity",
        "unit_price",
        "total_display",
    )

    search_fields = (
        "invoice__invoice_number",
        "product__name",
        "description",
    )

    list_filter = (
        "invoice__organization",
    )

    autocomplete_fields = (
        "invoice",
        "product",
    )

    readonly_fields = (
        "total_display",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "invoice",
        "product",
    )

    @admin.display(description=_("Line Total"))
    def total_display(self, obj):
        return obj.total


@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "template_type",
        "organization",
        "is_default",
        "is_active",
        "created_at",
    )

    list_filter = (
        "template_type",
        "is_default",
        "is_active",
        "organization",
    )

    search_fields = (
        "name",
        "description",
        "organization__name",
    )

    autocomplete_fields = (
        "organization",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "organization",
    )

    ordering = (
        "name",
    )

    fieldsets = (
        (
            _("Template Information"),
            {
                "fields": (
                    "name",
                    "description",
                    "template_type",
                    "file",
                )
            },
        ),
        (
            _("Ownership"),
            {
                "fields": (
                    "organization",
                    "is_default",
                    "is_active",
                )
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "document_type",
        "prefix",
        "padding",
        "next_number",
    )

    list_filter = (
        "document_type",
        "organization",
    )

    search_fields = (
        "organization__name",
        "prefix",
    )

    autocomplete_fields = (
        "organization",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "organization",
    )

    ordering = (
        "organization",
        "document_type",
    )

    fieldsets = (
        (
            _("Sequence Configuration"),
            {
                "fields": (
                    "organization",
                    "document_type",
                    "prefix",
                    "padding",
                    "next_number",
                )
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
