"""Broker에 배정된 체결·잔여 수량의 금액을 통화별로 계산한다."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any
from feed_parsing import integer


def positive_decimal(value: Any) -> Decimal | None:
    """미확인 가격을 0으로 바꾸지 않고 유한한 양수만 허용한다."""
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def currency_unit(value: Any) -> tuple[str, Decimal]:
    """명시된 소액 호가 단위를 기준 통화로 변환하며 다른 표기는 추정하지 않는다."""
    raw = str(value or "").strip()
    if raw in {"GBp", "GBX"}:
        return "GBP", Decimal("0.01")
    return (raw if re.fullmatch(r"[A-Z]{3}", raw, flags=re.ASCII) else "", Decimal(1))


def route_notional(
    fields: dict[str, Any],
    parent: dict[str, Any],
    qty: int,
    filled: int,
    terminal: bool,
) -> tuple[str, Decimal | None]:
    """체결은 Avg Price, 잔여는 Limit Price로 평가하며 통화 단위를 맞춘다."""
    currency, factor = currency_unit(
        fields.get("EMSX_CURRENCY") or parent.get("EMSX_CURRENCY")
    )
    if not currency:
        return "", None
    try:
        amount = integer(fields.get("EMSX_AMOUNT"))
        executed = integer(fields.get("EMSX_FILLED"))
    except (ValueError, TypeError, OverflowError):
        return currency, None
    if amount <= 0 or (amount, executed) != (qty, filled) or not 0 <= filled <= qty:
        return currency, None
    avg = positive_decimal(fields.get("EMSX_AVG_PRICE"))
    limit = positive_decimal(fields.get("EMSX_LIMIT_PRICE"))
    # Route에 가격이 명시되어 있으면 0·손상 값도 상위 가격으로 덮지 않는다.
    if "EMSX_LIMIT_PRICE" not in fields:
        limit = positive_decimal(parent.get("EMSX_LIMIT_PRICE"))
        parent_currency, parent_factor = currency_unit(parent.get("EMSX_CURRENCY"))
        if parent_currency != currency:
            limit = None
        elif limit is not None:
            limit = limit * parent_factor / factor
    remaining = 0 if terminal else max(0, qty - filled)
    if (filled and avg is None) or (remaining and limit is None):
        return currency, None
    value = Decimal(filled) * (avg or 0) + Decimal(remaining) * (limit or 0)
    return currency, value * factor
