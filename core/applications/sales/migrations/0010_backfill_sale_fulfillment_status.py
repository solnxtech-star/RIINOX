"""
Sales deduct their stock when they are created, but older code never set
`fulfillment_status`, so they still say "unfulfilled". Mark those that were
neither cancelled nor returned as fulfilled. Cancelled and returned sales keep
their status.
"""

from django.db import migrations


def mark_fulfilled(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")
    Sale.objects.filter(fulfillment_status="unfulfilled", cancelled_at__isnull=True).update(
        fulfillment_status="fulfilled"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0009_sale_prices_include_tax"),
    ]

    operations = [
        migrations.RunPython(mark_fulfilled, migrations.RunPython.noop),
    ]
