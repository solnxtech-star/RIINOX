from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class UsersRole(TextChoices):
    """Defines roles available within an organization."""

    OWNER = "owner", _("Owner")   # Primary admin, usually the org creator
    ADMIN = "admin", _("Admin")   # Can manage org settings & members
    MEMBER = "member", _("Member")  # Regular team member with limited access


class InvoiceStatusChoices(TextChoices):
    """Represents the lifecycle status of an invoice."""

    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    OVERDUE = "overdue", _("Overdue")
    CANCELLED = "cancelled", _("Cancelled")
    SENT = "sent", _("Sent")
    DRAFT = "draft", _("Draft")


class NotificationTypeChoices(TextChoices):
    """
    Types of notifications that can be sent to users.
    """

    INVOICE_ISSUED = "invoice_issued", _("Invoice Issued")
    INVOICE_DUE = "invoice_due", _("Invoice Due")
    PAYMENT_CONFIRMATION = "payment_confirmation", _("Payment Confirmation")
    LOW_STOCK = "low_stock", _("Low Stock")
    OUT_OF_STOCK = "out_of_stock", _("Out of Stock")
    STOCK_VARIANCE = "stock_variance", _("Stock Variance")
    LARGE_ADJUSTMENT = "large_adjustment", _("Large Adjustment")
    INVOICE_CANCELLED = "invoice_cancelled", _("Invoice Cancelled")
    RECEIPT_CANCELLED = "receipt_cancelled", _("Receipt Cancelled")
    SUSPICIOUS_ACTIVITY = "suspicious_activity", _("Suspicious Activity")
    LARGE_CUSTOMER_DEBT = "large_customer_debt", _("Large Customer Debt")
    PENDING_APPROVAL = "pending_approval", _("Pending Approval")
    FAILED_LOGIN = "failed_login", _("Failed Login")
    SALE_COMPLETED = "sale_completed", _("Sale Completed")
    PRODUCT_AVAILABLE = "product_available", _("Product Available")


class NotificationStatusChoices(TextChoices):
    """Tracks the delivery status of a notification."""

    PENDING = "pending", _("Pending")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")


class ReportTypeChoice(TextChoices):
    """Defines the available categories of system reports."""

    # Financial
    FINANCIAL_SUMMARY = "financial_summary", _("Financial Summary")
    TRANSACTION_HISTORY = "transaction_history", _("Transaction History")
    REVENUE = "revenue", _("Revenue Report")
    OUTSTANDING = "outstanding", _("Outstanding Payments")
    CLIENT = "client", _("Client Invoice Summary")

    # Inventory
    INVENTORY_SUMMARY = "inventory_summary", _("Inventory Summary")
    STOCK_LEVELS = "stock_levels", _("Stock Levels")
    STOCK_MOVEMENTS = "stock_movements", _("Stock Movements")

    # Sales
    SALES_SUMMARY = "sales_summary", _("Sales Summary")
    TOP_PRODUCTS = "top_products", _("Top Products")
    CUSTOMER_PURCHASES = "customer_purchases", _("Customer Purchases")

    # Compliance & audit
    AUDIT_LOG = "audit_log", _("Audit Log Report")
    USER_ACTIVITY = "user_activity", _("User Activity Report")
    SECURITY_EVENTS = "security_events", _("Security Events")

    # Custom
    CUSTOM = "custom", _("Custom Report")

class ProductStatusChoices(TextChoices):
    """
    Represents the lifecycle status of a product in the inventory.
    """
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")
    DISCONTINUED = "discontinued", _("Discontinued")
    ARCHIVED = "archived", _("Archived")

class UnitOfMeasureChoices(TextChoices):
    """
    Represents the unit of measure for products in the inventory.
    This can be used for stock tracking, sales, and reporting.
    """
    PIECE = "pcs", _("Piece(s)")
    KILOGRAM = "kg", _("Kilogram")
    GRAM = "g", _("Gram")
    LITRE = "l", _("Litre")
    MILLILITRE = "ml", _("Millilitre")
    BOX = "box", _("Box")
    PACK = "pack", _("Pack")
    CARTON = "carton", _("Carton")
    DOZEN = "dozen", _("Dozen")
    METRE = "m", _("Metre")
    OTHER = "other", _("Other")

class VendorStatusChoices(TextChoices):
    """
    Represents the lifecycle status of a vendor/supplier in the system.
    """
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")

class PurchaseStatusChoices(TextChoices):
    """
    Represents the lifecycle status of a purchase order or procurement transaction.
    """
    DRAFT = "draft", _("Draft")
    ORDERED = "ordered", _("Ordered")
    PARTIALLY_RECEIVED = "partially_received", _("Partially Received")
    RECEIVED = "received", _("Received")
    CANCELLED = "cancelled", _("Cancelled")


class InventoryTransactionTypeChoices(TextChoices):
    """
    Represents the types of inventory transactions that can occur in the system.
    """
    PURCHASE_RECEIPT = "purchase_receipt", _("Purchase Receipt")
    SALE = "sale", _("Sale")
    ADJUSTMENT_IN = "adjustment_in", _("Adjustment (In)")
    ADJUSTMENT_OUT = "adjustment_out", _("Adjustment (Out)")
    TRANSFER_IN = "transfer_in", _("Transfer In")
    TRANSFER_OUT = "transfer_out", _("Transfer Out")
    RETURN = "return", _("Customer Return")
    DAMAGE = "damage", _("Damaged / Written Off")
    COUNT_CORRECTION = "count_correction", _("Physical Count Correction")


class ApprovalStatusChoices(TextChoices):
    """
    Represents the approval status of a document or transaction that requires managerial 
    or administrative review.
    """
    
    NOT_REQUIRED = "not_required", _("Not Required")
    PENDING = "pending", _("Pending Approval")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


class VarianceReasonChoices(TextChoices):
    """
    Represents the reasons for inventory variances or discrepancies that may arise during
    stock counts, audits, or reconciliations.
    """
    DAMAGED = "damaged", _("Damaged")
    MISSING = "missing", _("Missing")
    WRONG_ENTRY = "wrong_entry", _("Wrong Entry")
    UNRECORDED_RETURN = "unrecorded_return", _("Unrecorded Return")
    WAREHOUSE_ERROR = "warehouse_error", _("Warehouse Error")
    OTHER = "other", _("Other")

class CustomerTypeChoices(TextChoices):
    INDIVIDUAL = "individual", _("Individual")
    BUSINESS = "business", _("Business")


class CustomerStatusChoices(TextChoices):
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")
    BLOCKED = "blocked", _("Blocked")


class PhysicalCountStatusChoices(TextChoices):
    DRAFT = "draft", _("Draft")
    COMPLETED = "completed", _("Completed")


class ActionChoice(TextChoices):
    """Generic CRUD and authentication actions."""

    CREATE = "create", _("Create")
    UPDATE = "update", _("Update")
    DELETE = "delete", _("Delete")
    LOGIN = "login", _("Login")
    LOGOUT = "logout", _("Logout")


class PaymentMethod(TextChoices):
    """Available payment providers and methods."""

    STRIPE = "stripe", _("Stripe")
    PAYSTACK = "paystack", _("Paystack")
    NOWPAYMENTS = "nowpayments", _("NowPayments")
    BANK_TRANSFER = "bank_transfer", _("Bank Transfer")
    WALLET = "wallet", _("Wallet / Internal Credit")


class PaymentStatus(TextChoices):
    """Tracks the status of a payment transaction."""

    PENDING = "pending", _("Pending")
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    REFUNDED = "refunded", _("Refunded")
    CANCELLED = "cancelled", _("Cancelled")


class AuditActionChoices(TextChoices):
    """Tracks audit-related system actions."""

    CREATE = "create", _("Create")
    UPDATE = "update", _("Update")
    DELETE = "delete", _("Delete")
    LOGIN = "login", _("Login")
    LOGOUT = "logout", _("Logout")
    VOID = "void", _("Void")
    CONFIRM = "confirm", _("Confirm")
    APPROVE = "approve", _("Approve")
    REJECT = "reject", _("Reject")
    ADJUST = "adjust", _("Stock Adjustment")
    RECEIVE = "receive", _("Goods Received")
    COUNT = "count", _("Physical Count")


class ReportFormatChoices(TextChoices):
    """Supported formats for generated reports."""

    PDF = "pdf", _("PDF")
    CSV = "csv", _("CSV")
    XLSX = "xlsx", _("Excel")


class ReportStatusChoices(TextChoices):
    """Tracks the lifecycle of report generation."""

    PENDING = "pending", _("Pending")
    GENERATED = "generated", _("Generated")
    FAILED = "failed", _("Failed")


class OrganizationTypeChoices(TextChoices):
    SCHOOL = "school", _("School")
    BUSINESS = "business", _("Business")
    NGO = "ngo", _("Non-Profit / NGO")
    GOVERNMENT = "government", _("Government")
    OTHER = "other", _("Other")


class TEMPLATE_TYPES(TextChoices):
    INVOICE = "invoice", _("Invoice")
    RECEIPT = "receipt", _("Receipt")
    QUOTE = "quote", _("Quote")
    CREDIT_NOTE = "credit_note", _("Credit Note")
