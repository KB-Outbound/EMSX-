"""명시적 국가코드와 백분율 단위에 기반한 데스크 표시 규칙이다."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

ASIA_COUNTRIES = frozenset(
    {"CN", "HK", "ID", "IN", "JP", "NZ", "MY", "SG", "TH", "TW", "VN", "AU"}
)


def country_code(value: Any) -> str:
    """티커로 국가를 추정하지 않고 제공된 두 자리 ISO 코드만 정규화한다."""
    code = str(value or "").strip().upper()
    return code if len(code) == 2 and code.isascii() and code.isalpha() else ""


def asian_delay_eligible(value: Any) -> bool:
    return country_code(value) in ASIA_COUNTRIES


def percentage_points(value: Any) -> Decimal | None:
    """2를 2%로 해석한다. 단위가 불명확한 0.02를 임의로 2%로 확대하지 않는다."""
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().removesuffix("%").strip()
    try:
        result = Decimal(raw)
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def adv_check(instruction: str, sec_type: str, adv_pct: Any) -> tuple[bool, str]:
    """경계값을 포함한 두 규칙을 독립적으로 검사한다. 미제공 값은 강조하지 않는다."""
    adv = percentage_points(adv_pct)
    if adv is None:
        return False, ""
    reasons = []
    if instruction.strip().upper() == "MOC" and adv >= Decimal("2"):
        reasons.append("MOC ≥ 2%")
    if " ".join(sec_type.split()).casefold() == "common stock" and adv >= Decimal("10"):
        reasons.append("Common Stock ≥ 10%")
    return bool(reasons), " / ".join(reasons)


def route_adv_percent(quantity: Any, average_volume: Any) -> Decimal | None:
    """Route 수량을 평균 거래량으로 나눈 백분율이다. 분모 누락·0·손상은 추정하지 않는다."""
    shares = percentage_points(quantity)
    volume = percentage_points(average_volume)
    if shares is None or volume is None or volume == 0:
        return None
    return shares / volume * 100


def format_adv(value: Any) -> str:
    """강조 경계 아래 값을 반올림하여 임계값 이상으로 보이게 하지 않는다."""
    percent = percentage_points(value)
    if percent is None:
        return "—"
    if percent.adjusted() > 12:
        return f"{percent:.4E}%"
    truncated = (percent * 10000).to_integral_value(rounding=ROUND_DOWN) / 10000
    if percent > 0 and truncated == 0:
        return "<0.0001%"
    return (
        format(truncated, "f").rstrip("0").rstrip(".") + "%"
        if "." in format(truncated, "f")
        else format(truncated, "f") + "%"
    )
