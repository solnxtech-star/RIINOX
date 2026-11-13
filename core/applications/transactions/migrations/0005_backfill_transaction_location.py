"""
Fill Transaction.location for transactions created before the field existed.

Sources, in order (the first that applies wins):
  1. the transaction's inventory ledger entries (every stock movement has a warehouse);
  2. the document it points at: a sale, sale return, invoice, purchase, purchase
     return, or a payment linked to an invoice or purchase.
Transactions none of these resolve stay NULL (organization-level events).

Built for large tables: walks transactions by primary key in batches (keyset
pagination), a fixed number of queries per batch, each batch committed on its own,
and safe to re-run (only rows still NULL are touched). Reversing is a no-op.
"""

from django.db import migrations
from django.db import transaction

BATCH_SIZE = 2000


def _content_type_id(apps, app_label, model):
    ContentType = apps.get_model("contenttypes", "ContentType")
    row = ContentType.objects.filter(app_label=app_label, model=model).values_list("pk", flat=True).first()
    return row


def _sale_locations(Sale, sale_ids):
    """sale id -> warehouse id (its warehouse, else its storage area's warehouse)."""
    result = {}
    for pk, warehouse_id, area_warehouse_id in Sale.objects.filter(pk__in=sale_ids).values_list(
        "pk", "warehouse_id", "location__warehouse_id"
    ):
        result[str(pk)] = warehouse_id or area_warehouse_id
    return result


def _backfill_batch(apps, rows, ct):
    Transaction = apps.get_model("transactions", "Transaction")
    LedgerEntry = apps.get_model("inventory", "InventoryLedgerEntry")
    Sale = apps.get_model("sales", "Sale")
    SaleReturn = apps.get_model("sales", "SaleReturn")
    Invoice = apps.get_model("invoice", "Invoice")
    Purchase = apps.get_model("purchase", "Purchase")
    PurchaseReturn = apps.get_model("purchase", "PurchaseReturn")
    InvoicePayment = apps.get_model("payment", "InvoicePayment")
    PurchasePayment = apps.get_model("payment", "PurchasePayment")

    resolved = {}

    # 1. Inventory ledger entries.
    trx_ids = [pk for pk, _, _ in rows]
    for trx_id, warehouse_id in (
        LedgerEntry.objects.filter(transaction_id__in=trx_ids)
        .order_by("transaction_id", "created_at")
        .values_list("transaction_id", "warehouse_id")
    ):
        resolved.setdefault(trx_id, warehouse_id)

    # 2. The originating document, grouped by type: one query per type per batch.
    by_type = {}
    for pk, ct_id, object_id in rows:
        if pk not in resolved and ct_id and object_id:
            by_type.setdefault(ct_id, []).append((pk, object_id))

    def assign(pairs, lookup):
        for pk, object_id in pairs:
            location = lookup.get(str(object_id).replace("-", "")) or lookup.get(str(object_id))
            if location:
                resolved[pk] = location

    def normalized(mapping):
        return {str(k).replace("-", ""): v for k, v in mapping.items()}

    for ct_id, pairs in by_type.items():
        ids = [object_id for _, object_id in pairs]
        if ct_id == ct.get("sale"):
            assign(pairs, normalized(_sale_locations(Sale, ids)))
        elif ct_id == ct.get("salereturn"):
            returns = dict(SaleReturn.objects.filter(pk__in=ids).values_list("pk", "original_sale_id"))
            sales = _sale_locations(Sale, list(returns.values()))
            assign(pairs, normalized({k: sales.get(str(v)) for k, v in returns.items()}))
        elif ct_id == ct.get("invoice"):
            assign(pairs, normalized(dict(Invoice.objects.filter(pk__in=ids).values_list("pk", "warehouse_id"))))
        elif ct_id == ct.get("purchase"):
            assign(pairs, normalized(dict(Purchase.objects.filter(pk__in=ids).values_list("pk", "warehouse_id"))))
        elif ct_id == ct.get("purchasereturn"):
            assign(
                pairs,
                normalized(
                    dict(PurchaseReturn.objects.filter(pk__in=ids).values_list("pk", "purchase__warehouse_id"))
                ),
            )
        elif ct_id == ct.get("payment"):
            invoice_side = dict(
                InvoicePayment.objects.filter(payment_id__in=ids).values_list("payment_id", "invoice__warehouse_id")
            )
            purchase_side = dict(
                PurchasePayment.objects.filter(payment_id__in=ids).values_list("payment_id", "purchase__warehouse_id")
            )
            assign(pairs, normalized({**purchase_side, **invoice_side}))

    # Write: one UPDATE per distinct location (few per batch).
    by_location = {}
    for pk, location in resolved.items():
        by_location.setdefault(location, []).append(pk)
    for location, pks in by_location.items():
        Transaction.objects.filter(pk__in=pks, location__isnull=True).update(location_id=location)


def backfill_transaction_location(apps, schema_editor):
    Transaction = apps.get_model("transactions", "Transaction")
    ct = {
        "sale": _content_type_id(apps, "sales", "sale"),
        "salereturn": _content_type_id(apps, "sales", "salereturn"),
        "invoice": _content_type_id(apps, "invoice", "invoice"),
        "purchase": _content_type_id(apps, "purchase", "purchase"),
        "purchasereturn": _content_type_id(apps, "purchase", "purchasereturn"),
        "payment": _content_type_id(apps, "payment", "payment"),
    }
    last_pk = None
    while True:
        queryset = Transaction.objects.filter(location__isnull=True).order_by("pk")
        if last_pk is not None:
            queryset = queryset.filter(pk__gt=last_pk)
        rows = list(queryset.values_list("pk", "content_type_id", "object_id")[:BATCH_SIZE])
        if not rows:
            break
        with transaction.atomic():
            _backfill_batch(apps, rows, ct)
        last_pk = rows[-1][0]


class Migration(migrations.Migration):
    atomic = False  # each batch commits on its own

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("transactions", "0004_transaction_location"),
        ("inventory", "0003_initial"),
        ("sales", "0008_alter_sale_currency"),
        ("invoice", "0010_alter_invoice_currency_purchaseinvoice_and_more"),
        ("purchase", "0003_alter_purchaseitem_options_purchase_amount_paid_and_more"),
        ("payment", "0004_alter_purchasepayment_options_purchasepayment_notes_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_transaction_location, migrations.RunPython.noop),
    ]
