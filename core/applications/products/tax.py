"""
Tax calculation (PRD §13 product tax configuration, §21 "Apply Discount / Tax",
§39 "the backend handles tax calculations").

One calculator shared by sales and invoices, so a sale and the invoice issued for
it can never disagree. Pure functions over Decimals: no queries, no side effects.

Rules
  * A line's rate: 0 for `exempt` / `non_taxable` products; otherwise the product's
    own `tax_rate` when set, else the organization's `default_tax_rate`.
  * Tax is computed per line on the line's amount *after* discounts, and each
    line's tax is rounded half-up to the cent. The document's tax is the sum of the
    line taxes, so line and header figures always reconcile.
  * Exclusive pricing (`prices_include_tax=False`): tax = amount x rate / 100 and is
    added on top: total = taxable + tax.
  * Inclusive pricing (`prices_include_tax=True`): the amount already contains tax:
    tax = amount x rate / (100 + rate), and the total equals the taxable amount.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from core.helper.enums import TaxConfigChoices

CENT = Decimal("0.01")
RATE_STEP = Decimal("0.01")
HUNDRED = Decimal(100)
UNTAXED_CONFIGS = frozenset({TaxConfigChoices.EXEMPT, TaxConfigChoices.NON_TAXABLE})

# Business types whose customers expect shelf prices to include tax. Everyone else
# (wholesale, distribution, services, ...) quotes prices before tax. Only the
# default for new organizations; each organization can change it.
TAX_INCLUSIVE_BUSINESS_TYPES = frozenset({"retail", "supermarket", "pharmacy"})


def prices_include_tax_by_default(business_type_code: str | None) -> bool:
    return (business_type_code or "") in TAX_INCLUSIVE_BUSINESS_TYPES


@dataclass(frozen=True, slots=True)
class TaxPolicy:
    """An organization's tax settings."""

    default_rate: Decimal = Decimal("0")
    prices_include_tax: bool = False

    @classmethod
    def for_organization(cls, organization: Any) -> TaxPolicy:
        return cls(
            default_rate=Decimal(str(getattr(organization, "default_tax_rate", 0) or 0)),
            prices_include_tax=bool(getattr(organization, "prices_include_tax", False)),
        )


@dataclass(frozen=True, slots=True)
class LineTax:
    rate: Decimal  # percentage, e.g. 7.50
    tax: Decimal   # rounded to the cent


@dataclass(frozen=True, slots=True)
class TaxResult:
    lines: tuple[LineTax, ...]
    tax: Decimal               # sum of the line taxes
    taxable: Decimal           # sum of the line amounts after discounts
    prices_include_tax: bool

    @property
    def total(self) -> Decimal:
        """What the customer pays."""
        return self.taxable if self.prices_include_tax else self.taxable + self.tax


def rate_for(product: Any, policy: TaxPolicy) -> Decimal:
    """The percentage rate that applies to `product` (see module rules)."""
    if getattr(product, "tax_config", TaxConfigChoices.TAXABLE) in UNTAXED_CONFIGS:
        return Decimal("0")
    own = getattr(product, "tax_rate", None)
    rate = Decimal(str(own)) if own is not None else policy.default_rate
    if not Decimal(0) <= rate <= HUNDRED:
        msg = f"Tax rate must be between 0 and 100, got {rate}."
        raise ValueError(msg)
    return rate.quantize(RATE_STEP)


def line_tax(amount: Decimal, rate: Decimal, *, inclusive: bool) -> Decimal:
    """Tax on one line amount (after discounts), rounded half-up to the cent."""
    if amount <= 0 or rate <= 0:
        return Decimal("0.00")
    raw = amount * rate / (HUNDRED + rate) if inclusive else amount * rate / HUNDRED
    return raw.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate(
    lines: Sequence[tuple[Any, Decimal]],
    policy: TaxPolicy,
) -> TaxResult:
    """
    `lines`: (product, amount after all discounts) per line, in document order.
    Returns the per-line rate and tax plus the document totals.
    """
    computed = []
    for product, amount in lines:
        rate = rate_for(product, policy)
        computed.append(LineTax(rate=rate, tax=line_tax(Decimal(amount), rate, inclusive=policy.prices_include_tax)))
    return TaxResult(
        lines=tuple(computed),
        tax=sum((line.tax for line in computed), Decimal("0.00")),
        taxable=sum((Decimal(amount) for _, amount in lines), Decimal("0.00")),
        prices_include_tax=policy.prices_include_tax,
    )
