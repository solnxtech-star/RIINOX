from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model

from core.seeding.registry import SeedResult

CREATED, UPDATED, UNCHANGED = "created", "updated", "unchanged"


def upsert(
        model: type[Model],
        *, lookup: Mapping[str, Any],
        defaults: Mapping[str, Any],
        overwrite: bool = True,
    ) -> str:
    """
    Create the row if missing. If it exists:
      overwrite=True   ->
      code-owned data: refresh the fields in `defaults` (only if they differ)
      overwrite=False  ->
      business-owned data: leave it exactly as it is
    """
    obj, created = model.objects.get_or_create(**lookup, defaults=dict(defaults))
    if created:
        return CREATED
    if not overwrite:
        return UNCHANGED

    changed = [name for name, value in defaults.items() if getattr(obj, name) != value]
    if not changed:
        return UNCHANGED
    for name in changed:
        setattr(obj, name, defaults[name])
    obj.save(update_fields=[*changed, *_timestamp_fields(model)])
    return UPDATED


def _timestamp_fields(model: type[Model]) -> list[str]:
    try:
        model._meta.get_field("updated_at")
    except FieldDoesNotExist:
        return []
    return ["updated_at"]


def tally(statuses: Iterable[str]) -> SeedResult:
    counts = Counter(statuses)
    return SeedResult(
        created=counts[CREATED],
        updated=counts[UPDATED],
        unchanged=counts[UNCHANGED],
    )
