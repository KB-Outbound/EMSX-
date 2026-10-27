"""생성일·고객 비고·Basket의 표시와 날짜 범위 판정을 담당한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

DESK_TIMEZONE = timezone(timedelta(hours=9))


def desk_today() -> date:
    """서울 데스크의 현재 달력 날짜를 반환한다."""
    return datetime.now(DESK_TIMEZONE).date()


def trade_date(fields: dict[str, Any]) -> date | None:
    """수신한 EMSX_DATE를 해석한다. 누락된 날짜를 수신 시각으로 대체하지 않는다."""
    value = fields.get("EMSX_DATE")
    raw = str(value or "").strip()
    try:
        if len(raw) == 8 and raw.isascii() and raw.isdigit():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return date.fromisoformat(raw)
    except ValueError:
        pass
    return None


def recent_order(
    fields: dict[str, Any], today: date | None = None, today_only: bool = False
) -> bool:
    """오늘과 전일 범위만 허용하며 미래·누락·손상 날짜는 제외한다."""
    current = today or desk_today()
    created = trade_date(fields)
    return (
        created is not None
        and current - timedelta(days=0 if today_only else 1) <= created <= current
    )


def creation_fields(fields: dict[str, Any]) -> dict[str, str]:
    """원본 거래일과 입력 시각을 표시·정렬용으로 변환한다."""
    day = trade_date(fields)
    if day is None:
        return {"trade_date": "", "create_time": "", "create_sort": ""}
    raw = str(fields.get("EMSX_TIME", "")).strip()
    clock = ""
    try:
        if ":" in raw:
            clock = datetime.strptime(raw, "%H:%M:%S").strftime("%H:%M:%S")
        elif raw:
            seconds = int(raw)
            if 0 <= seconds < 86400:
                clock = (
                    f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
                )
    except (ValueError, OverflowError):
        pass
    return {
        "trade_date": day.isoformat(),
        "create_time": day.isoformat() + (" " + clock if clock else ""),
        "create_sort": day.isoformat() + (" " + clock if clock else ""),
    }


def client_instruction(fields: dict[str, Any]) -> str:
    """고객 비고를 표시한다. 수동·자동 처리 플래그는 지시로 노출하지 않는다."""
    note = str(fields.get("EMSX_NOTES") or "").strip()
    return "" if note.upper() in {"MAN", "AUTO", "MANUAL", "AUTOMATIC"} else note


def sort_orders(
    rows: list[dict[str, Any]], basket_first: bool = False, oldest_first: bool = False
) -> list[dict[str, Any]]:
    """동일 시각에는 내부 식별자로 순서를 고정하고 선택적으로 Basket별로 묶는다."""
    ordered = sorted(
        rows,
        key=lambda row: (row.get("create_sort", ""), row["seq"]),
        reverse=not oldest_first,
    )
    if basket_first:
        ordered.sort(key=lambda row: str(row.get("basket_name", "")).casefold())
    return ordered
