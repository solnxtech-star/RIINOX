from __future__ import annotations

import logging
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from graphlib import CycleError
from graphlib import TopologicalSorter

from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db import transaction

logger = logging.getLogger(__name__)

# Arbitrary stable key: serializes concurrent runs (two containers starting at once).
ADVISORY_LOCK_KEY = 7_368_100_001


class UnknownSeedError(ValueError):
    """A seed name was requested (or required) that is not registered."""


@dataclass(frozen=True, slots=True)
class SeedResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class Seed:
    name: str
    run: Callable[[], SeedResult]
    depends_on: tuple[str, ...] = ()
    external: bool = False  # touches something outside the database (storage, network)
    description: str = ""


_REGISTRY: dict[str, Seed] = {}


def register(name: str, *, depends_on: Iterable[str] = (), external: bool = False):
    """
    Register a seed function. A seed must be idempotent, never delete, and
    return a SeedResult. Each app declares its own in `<app>/seeds.py`.
    """

    def decorator(func: Callable[[], SeedResult]):
        existing = _REGISTRY.get(name)
        if existing is not None and (existing.run.__module__, existing.run.__qualname__) != (
            func.__module__,
            func.__qualname__,
        ):
            raise ImproperlyConfigured(f"Seed '{name}' is registered twice.")
        doc = (func.__doc__ or "").strip()
        _REGISTRY[name] = Seed(
            name=name,
            run=func,
            depends_on=tuple(depends_on),
            external=external,
            description=doc.splitlines()[0] if doc else "",
        )
        return func

    return decorator


def _topological_order() -> list[str]:
    graph: dict[str, set[str]] = {}
    for name in sorted(_REGISTRY):
        deps = _REGISTRY[name].depends_on
        unknown = [dep for dep in deps if dep not in _REGISTRY]
        if unknown:
            raise ImproperlyConfigured(f"Seed '{name}' depends on unknown seed(s): {unknown}")
        graph[name] = set(deps)
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ImproperlyConfigured(f"Seed dependency cycle: {exc.args[1]}") from exc


def ordered_seeds(*, only: Iterable[str] = (), skip: Iterable[str] = ()) -> list[Seed]:
    """Seeds in dependency order. `only` pulls in its dependencies automatically."""
    only, skip = tuple(only), tuple(skip)
    for name in (*only, *skip):
        if name not in _REGISTRY:
            raise UnknownSeedError(f"Unknown seed '{name}'. Known: {', '.join(sorted(_REGISTRY))}")

    wanted: set[str] = set()
    stack = list(only) or list(_REGISTRY)
    while stack:
        name = stack.pop()
        if name not in wanted:
            wanted.add(name)
            stack.extend(_REGISTRY[name].depends_on)
    wanted -= set(skip)
    return [_REGISTRY[name] for name in _topological_order() if name in wanted]


def _take_advisory_lock() -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ADVISORY_LOCK_KEY])


def run_seeds(*, only: Iterable[str] = (), skip: Iterable[str] = ()) -> dict[str, SeedResult]:
    """Runs the selected seeds in one transaction: all of them apply, or none do."""
    seeds = ordered_seeds(only=only, skip=skip)
    results: dict[str, SeedResult] = {}
    with transaction.atomic():
        _take_advisory_lock()
        for seed in seeds:
            result = seed.run()
            results[seed.name] = result
            logger.info(
                "Seed %s: created=%d updated=%d unchanged=%d",
                seed.name, result.created, result.updated, result.unchanged,
            )
    return results
