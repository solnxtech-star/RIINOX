from rest_framework import serializers

from core.applications.inventory.models import InventoryLedgerEntry
from core.applications.invoice.models import PurchaseInvoice, PurchaseInvoiceItem
from core.applications.payment.models import PurchasePayment
from core.applications.purchase.models import (
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    PurchaseReturnItem,
)
from core.applications.purchase.services import get_purchase_available_actions
from core.helper.enums import InvoiceTypeChoices, PaymentMethod


class PurchaseItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    variant_name = serializers.SerializerMethodField()
    unit_name = serializers.SerializerMethodField()
    unit_multiplier = serializers.SerializerMethodField()
    received_value = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    ordered_value = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseItem
        fields = [
            "id",
            "purchase",
            "product",
            "product_name",
            "product_sku",
            "variant",
            "variant_name",
            "unit",
            "unit_name",
            "unit_multiplier",
            "quantity_ordered",
            "quantity_received",
            "purchase_cost",
            "discount",
            "received_value",
            "ordered_value",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "purchase",
            "product_name",
            "product_sku",
            "variant_name",
            "unit_name",
            "unit_multiplier",
            "quantity_received",
            "created_at",
        ]

    def get_variant_name(self, obj) -> str | None:
        return obj.variant.name if obj.variant else None

    def get_unit_name(self, obj) -> str:
        if obj.unit:
            return obj.unit.unit_name
        return getattr(obj.product, "unit_of_measurement", "pcs")

    def get_unit_multiplier(self, obj) -> str:
        if obj.unit:
            return str(obj.unit.multiplier)
        return "1.000"


class PurchaseItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseItem
        fields = [
            "product",
            "variant",
            "unit",
            "quantity_ordered",
            "purchase_cost",
            "discount",
        ]
        extra_kwargs = {
            "product": {
                "help_text": "UUID of the catalog Product to order (from GET /api/products/products/).",
            },
            "variant": {
                "help_text": "UUID of the specific ProductVariant, if applicable (optional).",
            },
            "unit": {
                "help_text": "UUID of the ProductUnitConversion packaging unit, if applicable (optional).",
            },
            "quantity_ordered": {
                "help_text": "Number of units ordered from the vendor (must be >= 1).",
            },
            "purchase_cost": {
                "required": False,
                "allow_null": True,
                "help_text": (
                    "Optional snapshotted agreed unit cost for this product line. "
                    "Defaults to variant.purchase_cost or product.purchase_cost (scaled by unit.multiplier) if omitted."
                ),
            },
            "discount": {
                "required": False,
                "help_text": "Optional line-item discount applied to this product line (defaults to 0.00).",
            },
        }


class PurchaseItemUpdateSerializer(PurchaseItemCreateSerializer):
    pass


class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(
        many=True,
        read_only=True,
        help_text="Line items belonging to this purchase order.",
    )
    vendor_name = serializers.CharField(
        source="vendor.company_name",
        read_only=True,
        help_text="Company name of the supplier/vendor.",
    )
    warehouse_name = serializers.CharField(
        source="warehouse.name",
        read_only=True,
        help_text="Name of the destination warehouse.",
    )
    ordered_by_name = serializers.SerializerMethodField(
        help_text="Display name or email of the user who created the purchase order.",
    )
    received_by_name = serializers.SerializerMethodField(
        help_text="Display name or email of the user who last checked in goods.",
    )
    cancelled_by_name = serializers.SerializerMethodField(
        help_text="Display name or email of the user who cancelled the purchase order.",
    )
    subtotal = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
        help_text="Sum of ordered line values across all items: sum((quantity_ordered * purchase_cost) - line_discount).",
    )
    total_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
        help_text="Total value of physically received goods plus other_costs minus header discount: sum((quantity_received * purchase_cost) - line_discount) + other_costs - discount.",
    )
    ordered_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
        help_text="Total value of ordered goods plus other_costs minus header discount: subtotal + other_costs - discount.",
    )
    balance_due = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
        help_text=(
            "Remaining payable balance (max(payable_total - amount_paid, 0.00)). "
            "Priority for payable_total: (1) active PurchaseInvoice.total if recorded, "
            "(2) total_value if any goods have been received, (3) ordered_value if unreceived."
        ),
    )
    available_actions = serializers.SerializerMethodField(
        help_text=(
            "Backend-computed list of valid UI actions for this purchase order's current lifecycle state "
            "(view, edit, cancel, delete, receive_goods, record_purchase_invoice, record_payment, create_return). "
            "Used to populate row kebab menus (UI Pages 5-6) and detail action buttons (UI Pages 13-14)."
        ),
    )

    class Meta:
        model = Purchase
        fields = [
            "id",
            "purchase_number",
            "organization",
            "vendor",
            "vendor_name",
            "warehouse",
            "warehouse_name",
            "ordered_by",
            "ordered_by_name",
            "received_by",
            "received_by_name",
            "cancelled_by",
            "cancelled_by_name",
            "cancelled_at",
            "cancel_reason",
            "vendor_invoice_number",
            "supporting_document",
            "delivery_date",
            "order_date",
            "delivery_method",
            "other_costs",
            "discount",
            "status",
            "payment_status",
            "amount_paid",
            "subtotal",
            "total_value",
            "ordered_value",
            "balance_due",
            "available_actions",
            "notes",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "purchase_number",
            "status",
            "payment_status",
            "amount_paid",
            "subtotal",
            "total_value",
            "ordered_value",
            "balance_due",
            "available_actions",
            "received_by",
            "cancelled_by",
            "cancelled_at",
            "cancel_reason",
            "created_at",
            "updated_at",
        ]

    def get_ordered_by_name(self, obj) -> str | None:
        if not obj.ordered_by:
            return None
        return getattr(obj.ordered_by, "name", None) or obj.ordered_by.email

    def get_received_by_name(self, obj) -> str | None:
        if not obj.received_by:
            return None
        return getattr(obj.received_by, "name", None) or obj.received_by.email

    def get_cancelled_by_name(self, obj) -> str | None:
        if not obj.cancelled_by:
            return None
        return getattr(obj.cancelled_by, "name", None) or obj.cancelled_by.email

    def get_available_actions(self, obj) -> list[str]:
        return get_purchase_available_actions(obj)


class PurchaseCreateSerializer(serializers.ModelSerializer):
    items = PurchaseItemCreateSerializer(
        many=True,
        required=False,
        help_text="List of line items ordered from the vendor.",
    )

    class Meta:
        model = Purchase
        fields = [
            "id",
            "purchase_number",
            "organization",
            "vendor",
            "warehouse",
            "ordered_by",
            "vendor_invoice_number",
            "supporting_document",
            "delivery_date",
            "order_date",
            "delivery_method",
            "other_costs",
            "discount",
            "status",
            "notes",
            "items",
            "created_at",
        ]
        read_only_fields = ["id", "purchase_number", "created_at"]
        extra_kwargs = {
            "organization": {
                "required": False,
                "help_text": "Optional UUID of the tenant organization. Automatically resolved from URL path {organization_id} if omitted.",
            },
            "vendor": {
                "help_text": "Required UUID of the supplier/vendor belonging to this organization.",
            },
            "warehouse": {
                "help_text": "Required UUID of the destination warehouse belonging to this organization.",
            },
            "ordered_by": {
                "required": False,
                "help_text": "Optional UUID of the ordering user. Automatically defaults to the authenticated request.user.",
            },
            "other_costs": {
                "required": False,
                "help_text": "Optional shipping/handling/ancillary costs on the purchase order (defaults to 0.00). Also automatically synchronized when a Supplier Invoice is recorded.",
            },
            "discount": {
                "required": False,
                "help_text": "Optional global order-level discount (defaults to 0.00). Also automatically synchronized when a Supplier Invoice is recorded.",
            },
        }

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, "copy") else dict(data)
        if "organization" not in data or not data["organization"]:
            view = self.context.get("view")
            url_org = getattr(view, "organization", None) if view else None
            if url_org:
                data["organization"] = str(url_org.id)
        return super().to_internal_value(data)

    def validate(self, attrs):
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        if url_org:
            body_org = attrs.get("organization")
            if body_org and body_org != url_org:
                raise serializers.ValidationError(
                    {"organization": "Organization does not match URL path tenant."}
                )
            attrs["organization"] = url_org

            vendor = attrs.get("vendor")
            if vendor and vendor.organization_id != url_org.id:
                raise serializers.ValidationError(
                    {"vendor": "Vendor does not belong to this organization."}
                )
            warehouse = attrs.get("warehouse")
            if warehouse and warehouse.organization_id != url_org.id:
                raise serializers.ValidationError(
                    {"warehouse": "Warehouse does not belong to this organization."}
                )
            items = attrs.get("items") or []
            for idx, item in enumerate(items):
                product = item.get("product")
                if product and product.organization_id != url_org.id:
                    raise serializers.ValidationError(
                        {"items": f"Line {idx}: Product does not belong to this organization."}
                    )
                variant = item.get("variant")
                if variant and product and variant.product_id != product.id:
                    raise serializers.ValidationError(
                        {"items": f"Line {idx}: Variant does not belong to the selected product."}
                    )
                unit = item.get("unit")
                if unit and product and unit.product_id != product.id:
                    raise serializers.ValidationError(
                        {"items": f"Line {idx}: Unit conversion does not belong to the selected product."}
                    )
        return super().validate(attrs)


class PurchaseUpdateSerializer(PurchaseCreateSerializer):
    items = PurchaseItemUpdateSerializer(
        many=True,
        required=False,
        help_text="List of line items ordered from the vendor.",
    )

    class Meta(PurchaseCreateSerializer.Meta):
        fields = [
            "id",
            "purchase_number",
            "organization",
            "vendor",
            "warehouse",
            "ordered_by",
            "vendor_invoice_number",
            "supporting_document",
            "delivery_date",
            "order_date",
            "delivery_method",
            "other_costs",
            "discount",
            "status",
            "notes",
            "items",
            "created_at",
        ]


class PurchaseReceiveItemSerializer(serializers.Serializer):
    item_id = serializers.UUIDField(help_text="UUID of the PurchaseItem being received.")
    quantity_received = serializers.IntegerField(
        min_value=1,
        help_text="Number of units physically received.",
    )
    batch = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional UUID of an existing ProductBatch.",
    )
    batch_number = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100,
        help_text="Optional Batch No. entered in Receive Products modal (UI Page 19).",
    )
    expiry_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Optional Expiry Date entered in Receive Products modal (UI Page 19).",
    )


class PurchaseReceiveSerializer(serializers.Serializer):
    items_received = PurchaseReceiveItemSerializer(
        many=True,
        help_text="Array of items, quantities, and optional batch/expiry metadata received.",
    )


class PurchaseReceiveResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Operation result status (e.g. 'received').")
    purchase_status = serializers.CharField(help_text="Updated lifecycle status of the purchase order.")
    items_received_count = serializers.IntegerField(help_text="Count of distinct line items processed.")


class PurchaseCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional reason for cancelling the purchase order (UI Page 14).",
    )


class PurchaseSummarySerializer(serializers.Serializer):
    total_purchase_orders = serializers.IntegerField(help_text="Total count of purchase orders.")
    pending_delivery_count = serializers.IntegerField(help_text="Count of draft or ordered POs awaiting delivery.")
    partially_received_count = serializers.IntegerField(help_text="Count of partially received POs.")
    received_count = serializers.IntegerField(help_text="Count of fully received POs.")
    cancelled_count = serializers.IntegerField(help_text="Count of cancelled POs.")
    unpaid_orders_count = serializers.IntegerField(help_text="Count of non-cancelled unpaid POs.")
    partially_paid_orders_count = serializers.IntegerField(help_text="Count of non-cancelled partially paid POs.")
    paid_orders_count = serializers.IntegerField(help_text="Count of non-cancelled fully paid POs.")
    total_spend = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Total value of received goods.")
    total_ordered_value = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Total value of ordered goods.")
    total_paid = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Total amount paid to suppliers.")
    total_payable_balance = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Total remaining balance due.")


class PurchaseInvoiceItemCreateSerializer(serializers.Serializer):
    purchase_item_id = serializers.UUIDField(help_text="UUID of the PurchaseItem being billed.")
    invoice_quantity = serializers.IntegerField(
        min_value=1,
        required=False,
        help_text="Billed quantity on the supplier invoice (UI Page 2).",
    )
    unit_cost = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Unit cost billed on the supplier invoice (UI Page 2).",
    )
    discount_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        default=0,
        help_text="Line discount on the supplier invoice.",
    )


class PurchaseInvoiceCreateSerializer(serializers.Serializer):
    vendor_invoice_number = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        default="",
        help_text="Supplier's external invoice reference number.",
    )
    billed_to = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        default="",
        help_text="Entity billed on the supplier invoice (UI Page 1).",
    )
    invoice_type = serializers.ChoiceField(
        choices=InvoiceTypeChoices.choices,
        required=False,
        default=InvoiceTypeChoices.SUPPLIER_INVOICE,
        help_text="Invoice classification (defaults to 'supplier_invoice', UI Page 1).",
    )
    issue_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Supplier invoice issue date (UI Page 1).",
    )
    due_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Supplier invoice payment due date (UI Page 1).",
    )
    tax_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        default=0,
        help_text="Tax percentage applied to the supplier invoice (e.g. 8.5 for 8.5%, UI Page 2).",
    )
    tax_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Explicit tax amount override (optional).",
    )
    discount_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Global discount amount on the supplier invoice.",
    )
    other_costs = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Additional shipping/handling costs on the supplier invoice.",
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Additional notes or payment terms (UI Page 1).",
    )
    supporting_document = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Scanned supplier invoice document.",
    )
    items = PurchaseInvoiceItemCreateSerializer(
        many=True,
        required=False,
        help_text="Optional per-line quantity/cost overrides for the supplier invoice (UI Page 2).",
    )


class PurchaseInvoiceItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    variant_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseInvoiceItem
        fields = [
            "id",
            "purchase_item",
            "product",
            "product_name",
            "variant",
            "variant_name",
            "description",
            "unit_name",
            "unit_multiplier",
            "quantity_ordered",
            "invoice_quantity",
            "unit_cost",
            "discount_amount",
            "line_total",
            "created_at",
        ]
        read_only_fields = fields

    def get_variant_name(self, obj) -> str | None:
        return obj.variant.name if obj.variant else None


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    items = PurchaseInvoiceItemSerializer(many=True, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    purchase_number = serializers.CharField(source="purchase.purchase_number", read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            "id",
            "organization",
            "purchase",
            "purchase_number",
            "vendor",
            "warehouse",
            "recorded_by",
            "recorded_by_name",
            "billed_to",
            "invoice_type",
            "vendor_company_name",
            "vendor_contact_person",
            "vendor_email",
            "vendor_phone",
            "vendor_address",
            "invoice_number",
            "vendor_invoice_number",
            "issue_date",
            "due_date",
            "status",
            "subtotal",
            "discount_amount",
            "tax_rate",
            "tax_amount",
            "other_costs",
            "total",
            "amount_paid",
            "balance_due",
            "currency",
            "supporting_document",
            "notes",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_recorded_by_name(self, obj) -> str | None:
        if not obj.recorded_by:
            return None
        return getattr(obj.recorded_by, "name", None) or obj.recorded_by.email


class PurchasePaymentCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Amount paid to the supplier. Defaults to remaining balance due if omitted.",
    )
    method = serializers.ChoiceField(
        choices=PaymentMethod.choices,
        help_text="Payment method used (e.g. bank_transfer, cash, pos).",
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        help_text="Optional bank or gateway payment reference.",
    )
    payment_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Date the payment was made to the supplier (UI Page 15).",
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional payment notes (UI Page 15).",
    )
    supporting_document = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Optional proof of payment attachment.",
    )


class PurchasePaymentSerializer(serializers.ModelSerializer):
    method = serializers.CharField(source="payment.method", read_only=True)
    reference = serializers.CharField(source="payment.reference", read_only=True)
    currency = serializers.CharField(source="payment.currency", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchasePayment
        fields = [
            "id",
            "receipt_number",
            "purchase",
            "payment",
            "amount",
            "balance_after",
            "method",
            "reference",
            "currency",
            "payment_date",
            "notes",
            "supporting_document",
            "recorded_by",
            "recorded_by_name",
            "is_voided",
            "received_on",
            "created_at",
        ]
        read_only_fields = fields

    def get_recorded_by_name(self, obj) -> str | None:
        if not obj.recorded_by:
            return None
        return getattr(obj.recorded_by, "name", None) or obj.recorded_by.email


class PurchaseReturnItemCreateSerializer(serializers.Serializer):
    purchase_item = serializers.UUIDField(help_text="UUID of the PurchaseItem being returned.")
    quantity = serializers.IntegerField(min_value=1, help_text="Quantity of units being returned to the supplier.")
    batch = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional UUID of the ProductBatch being returned.",
    )


class PurchaseReturnCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Reason for returning the goods to the supplier.",
    )
    items = PurchaseReturnItemCreateSerializer(
        many=True,
        help_text="List of purchase items and quantities being returned.",
    )


class PurchaseReturnItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="purchase_item.product_id", read_only=True)
    product_name = serializers.CharField(source="purchase_item.product.name", read_only=True)
    variant_name = serializers.SerializerMethodField()
    batch_number = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseReturnItem
        fields = [
            "id",
            "purchase_item",
            "product_id",
            "product_name",
            "variant_name",
            "batch",
            "batch_number",
            "quantity",
            "unit_cost",
            "line_total",
            "created_at",
        ]
        read_only_fields = fields

    def get_variant_name(self, obj) -> str | None:
        return obj.purchase_item.variant.name if obj.purchase_item.variant else None

    def get_batch_number(self, obj) -> str | None:
        return obj.batch.batch_number if obj.batch else None


class PurchaseReturnSerializer(serializers.ModelSerializer):
    items = PurchaseReturnItemSerializer(many=True, read_only=True)
    returned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseReturn
        fields = [
            "id",
            "return_number",
            "organization",
            "purchase",
            "returned_by",
            "returned_by_name",
            "reason",
            "total_refund_amount",
            "items",
            "created_at",
        ]
        read_only_fields = fields

    def get_returned_by_name(self, obj) -> str | None:
        if not obj.returned_by:
            return None
        return getattr(obj.returned_by, "name", None) or obj.returned_by.email


class PurchaseStockActivitySerializer(serializers.ModelSerializer):
    transaction_id = serializers.CharField(source="transaction.transaction_id", read_only=True)
    transaction_type = serializers.CharField(source="transaction.transaction_type", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    variant_name = serializers.SerializerMethodField()
    batch_number = serializers.SerializerMethodField()
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = InventoryLedgerEntry
        fields = [
            "id",
            "transaction_id",
            "transaction_type",
            "product",
            "product_name",
            "variant",
            "variant_name",
            "batch",
            "batch_number",
            "warehouse",
            "warehouse_name",
            "quantity_moved",
            "unit_cost",
            "created_at",
        ]
        read_only_fields = fields

    def get_variant_name(self, obj) -> str | None:
        return obj.variant.name if obj.variant else None

    def get_batch_number(self, obj) -> str | None:
        return obj.batch.batch_number if obj.batch else None
