"""
Give every organization created before default locations existed exactly one.

Per organization without a default:
  * it has an active location -> the oldest active one becomes the default
    (already counted in the location usage counter, so usage is unchanged);
  * otherwise -> a "Main Branch" location is created and counted (+1 usage).

Built for large tables: organizations are walked by primary key in batches
(keyset pagination, no OFFSET), each batch does a fixed number of queries and
commits on its own, so the migration holds no long transaction and is safe to
re-run after an interruption (organizations that already have a default are
skipped).

Constants are copied rather than imported: migrations must not change meaning
when application code changes later.
"""

import datetime

from django.db import migrations
from django.db import transaction
from django.db.models import Exists
from django.db.models import F
from django.db.models import OuterRef

BATCH_SIZE = 1000
LIFETIME_PERIOD = datetime.date(1970, 1, 1)
LOCATIONS_METRIC = "locations"
DEFAULT_NAME = "Main Branch"
DEFAULT_CODE = "MAIN"
DEFAULT_TYPE = "branch"
TYPE_BY_BUSINESS = {
    "wholesale_distribution": "warehouse",
    "retail": "store",
    "supermarket": "store",
    "pharmacy": "store",
    "services": "office",
}


def _free_code(taken: set[str]) -> str:
    if DEFAULT_CODE not in taken:
        return DEFAULT_CODE
    suffix = 2
    while f"{DEFAULT_CODE}-{suffix}" in taken:
        suffix += 1
    return f"{DEFAULT_CODE}-{suffix}"


def _backfill_batch(apps, org_ids):
    Organization = apps.get_model("users", "Organization")
    OrganizationBusinessType = apps.get_model("users", "OrganizationBusinessType")
    Warehouse = apps.get_model("warehouse", "Warehouse")
    UsageCounter = apps.get_model("subscriptions", "UsageCounter")

    # 1. Promote the oldest active location of each organization that has one.
    promote: dict = {}
    for pk, org_id in (
        Warehouse.objects.filter(organization_id__in=org_ids, is_active=True)
        .order_by("organization_id", "created_at", "pk")
        .values_list("pk", "organization_id")
    ):
        promote.setdefault(org_id, pk)
    if promote:
        Warehouse.objects.filter(pk__in=promote.values()).update(is_default=True)

    # 2. Create a Main Branch for the rest.
    missing = [org_id for org_id in org_ids if org_id not in promote]
    if not missing:
        return

    business_codes = dict(
        OrganizationBusinessType.objects.filter(organization_id__in=missing, is_primary=True)
        .values_list("organization_id", "business_type__code")
    )
    taken_codes: dict = {}
    for org_id, code in Warehouse.objects.filter(
        organization_id__in=missing, code__istartswith=DEFAULT_CODE
    ).values_list("organization_id", "code"):
        taken_codes.setdefault(org_id, set()).add(code.upper())
    addresses = dict(Organization.objects.filter(pk__in=missing).values_list("pk", "address"))

    Warehouse.objects.bulk_create(
        [
            Warehouse(
                organization_id=org_id,
                name=DEFAULT_NAME,
                code=_free_code(taken_codes.get(org_id, set())),
                location_type=TYPE_BY_BUSINESS.get(business_codes.get(org_id), DEFAULT_TYPE),
                address=addresses.get(org_id) or None,
                is_default=True,
                is_active=True,
            )
            for org_id in missing
        ],
        batch_size=BATCH_SIZE,
    )

    # 3. Count the new locations: bump existing counters, create the absent ones.
    counters = UsageCounter.objects.filter(
        organization_id__in=missing, metric=LOCATIONS_METRIC, period_start=LIFETIME_PERIOD
    )
    have_counter = set(counters.values_list("organization_id", flat=True))
    counters.update(value=F("value") + 1)
    UsageCounter.objects.bulk_create(
        [
            UsageCounter(
                organization_id=org_id,
                metric=LOCATIONS_METRIC,
                period_start=LIFETIME_PERIOD,
                value=1,
            )
            for org_id in missing
            if org_id not in have_counter
        ],
        batch_size=BATCH_SIZE,
    )


def backfill_default_locations(apps, schema_editor):
    Organization = apps.get_model("users", "Organization")
    Warehouse = apps.get_model("warehouse", "Warehouse")

    has_default = Warehouse.objects.filter(organization_id=OuterRef("pk"), is_default=True)
    last_pk = None
    while True:
        queryset = Organization.objects.filter(~Exists(has_default)).order_by("pk")
        if last_pk is not None:
            queryset = queryset.filter(pk__gt=last_pk)
        org_ids = list(queryset.values_list("pk", flat=True)[:BATCH_SIZE])
        if not org_ids:
            break
        with transaction.atomic():
            _backfill_batch(apps, org_ids)
        last_pk = org_ids[-1]


class Migration(migrations.Migration):
    # Each batch commits on its own (see module docstring).
    atomic = False

    dependencies = [
        ("subscriptions", "0002_initial"),
        ("users", "0003_alter_invitation_name"),
        ("warehouse", "0002_warehouse_is_default_warehouse_location_type_and_more"),
    ]

    operations = [
        # Reversing is a no-op: the defaults are valid data and are kept.
        migrations.RunPython(backfill_default_locations, migrations.RunPython.noop),
    ]
