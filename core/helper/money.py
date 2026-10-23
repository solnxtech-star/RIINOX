from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Final, Sequence

CENT: Final = Decimal("0.01")
RATE_STEP: Final = Decimal("0.01")
MAX_MONEY: Final = Decimal("9999999999.99")


def quantize_money(
    value: Any,
    *,
    field: str = "amount",
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    """
    Quantize `value` to whole cents (2 decimal places).
    Raises ValueError with a helpful message if `value` is not a valid number.
    """
    try:
        dec = Decimal(str(value))
        if dec.is_nan() or dec.is_infinite():
            raise ValueError(f"Invalid {field}: cannot be NaN or infinite.")
        return dec.quantize(CENT, rounding=rounding)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def allocate_amount(amount: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """
    Split `amount` across `weights` in whole cents using the largest-remainder
    method, so the parts always add up to exactly `amount`. Zero weights get
    nothing unless every weight is zero, in which case the split is even.
    """
    count = len(weights)
    if not count:
        return []
    zero = Decimal("0.00")
    if amount == 0:
        return [zero] * count

    total_weight = sum(weights, Decimal(0))
    if total_weight <= 0:
        weights = [Decimal(1)] * count
        total_weight = Decimal(count)

    cents = int((amount / CENT).to_integral_value())
    shares = [Decimal(cents) * w / total_weight for w in weights]
    floors = [int(s.to_integral_value(rounding=ROUND_FLOOR)) for s in shares]
    leftover = cents - sum(floors)
    # Largest fractional part first; earlier index wins tie-breakers.
    order = sorted(range(count), key=lambda i: (shares[i] - floors[i], -i), reverse=True)
    for i in order[:leftover]:
        floors[i] += 1
    return [Decimal(f) * CENT for f in floors]
