from django.apps import AppConfig


class InvoiceConfig(AppConfig):
    name = "core.applications.invoice"

    def ready(self) -> None:
        # Imported here, not at module level, so Django's app registry is
        # fully loaded and we avoid circular imports with sales/inventory/etc.
        from core.applications.invoice.adapters import DefaultInvoicePorts
        from core.applications.invoice.services.ports import register_invoice_ports

        register_invoice_ports(DefaultInvoicePorts())
