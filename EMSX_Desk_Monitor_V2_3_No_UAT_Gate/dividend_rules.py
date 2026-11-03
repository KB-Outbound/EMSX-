"""미국 T+2 주문의 익영업일 배당락 확인 규칙을 표시 계층에서 판정한다."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from order_fields import sort_orders
from risk_rules import country_code

DIVIDEND_ALERT = "Dividend Ex-Date Alert"
SPLIT_ALERT = "Stock Split Alert"
DIVIDEND_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("create_time", "Create Time", 126),
    ("account", "Account", 120),
    ("ticker", "Ticker", 145),
    ("qty", "Qty", 66),
    ("iso_country", "ISO", 36),
    ("instruction", "Instruction", 96),
    ("basket_name", "Basket Name", 108),
    ("dividend_ex_date", "Dividend Ex Date", 125),
    ("split_date", "Split Date", 100),
)


def parse_ex_date(value: Any) -> date | None:
    """BLPAPI 날짜 객체·날짜 문자열만 허용하며 손상·모호한 형식은 거부한다."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    try:
        if len(raw) == 8 and raw.isascii() and raw.isdigit():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        if len(raw) == 10 and raw.isascii() and raw[4] == raw[7] == "-":
            return date.fromisoformat(raw)
    except ValueError:
        pass
    return None


def dividend_candidate(row: dict[str, Any]) -> bool:
    """명시적 미국 ISO와 대소문자를 무시한 T+2 포함 조건만 검사한다."""
    instruction = row.get("instruction")
    return (
        country_code(row.get("iso_country")) == "US"
        and isinstance(instruction, str)
        and "T+2" in instruction.upper()
    )


def dividend_risk(row: dict[str, Any], target_date: date | None) -> bool:
    """확인된 다음 NYSE 영업일과 배당락일이 정확히 같은 후보만 강조한다."""
    if target_date is None or not dividend_candidate(row):
        return False
    ex_date = parse_ex_date(row.get("dividend_ex_date"))
    return ex_date is not None and ex_date == target_date


def split_risk(row: dict[str, Any], target_date: date | None) -> bool:
    """배당 확인과 같은 미국 T+2 범위에서 익영업일 Split Date를 확인한다."""
    if target_date is None or not dividend_candidate(row):
        return False
    return parse_ex_date(row.get("split_date")) == target_date


def dividend_view(
    rows: list[dict[str, Any]],
    session_date: date,
    target_date: date | None,
    basket_first: bool = False,
    oldest_first: bool = False,
) -> list[dict[str, Any]]:
    """화면용 사본에만 위험 표시를 붙이고 익영업일 배당락을 우선 배치한다."""
    if target_date is not None and target_date <= session_date:
        target_date = None
    candidates = []
    for row in rows:
        row["dividend_risk"] = dividend_risk(row, target_date)
        row["split_risk"] = split_risk(row, target_date)
        row["corporate_action_risk"] = row["dividend_risk"] or row["split_risk"]
        row["dividend_session_date"] = session_date.isoformat()
        row["dividend_target_date"] = target_date.isoformat() if target_date else ""
        if dividend_candidate(row):
            candidates.append(row)
    ordered = sort_orders(candidates, basket_first, oldest_first)
    ordered.sort(key=lambda row: not row["corporate_action_risk"])
    return ordered
