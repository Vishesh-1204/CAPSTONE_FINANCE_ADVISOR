# app/core/money.py
#
# Purpose: one helper for turning any amount into an exact 2-decimal Decimal.
#
# Why not float? Floats are binary fractions: 0.1 + 0.2 == 0.30000000000000004.
# Summing thousands of rupee amounts as floats drifts by paise, and two equal
# amounts can compare as "different". Decimal stores 99.10 exactly.

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_PAISE = Decimal("0.01")


def to_money(value: Decimal | float | int | str) -> Decimal:
    """
    Converts a number (or numeric string) to a Decimal rounded to 2 places.
    Going through str() avoids importing a float's binary error
    (Decimal(0.1) is 0.1000000000000000055..., Decimal("0.1") is exactly 0.1).
    Raises ValueError for anything that is not a finite number.
    """
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return amount.quantize(_PAISE, rounding=ROUND_HALF_UP)
