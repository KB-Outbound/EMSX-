"""외부 접속 없이 배포된 거래소 일정과 로컬 지정 일정을 판정한다."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from desk_core import TERMINAL
from order_fields import DESK_TIMEZONE
from resource_paths import asset_path

STATE_LABELS = {
    "Open": "개장",
    "Pre-Market": "장전",
    "Lunch": "점심",
    "Closed": "장 종료",
    "Holiday": "휴장",
    "Unknown": "확인 필요",
}
STATE_COLORS = {
    "Open": "#50D890",
    "Pre-Market": "#FFCC00",
    "Lunch": "#FFCC00",
    "Closed": "#E57878",
    "Holiday": "#E57878",
    "Unknown": "#B6B6B6",
}


@dataclass(frozen=True)
class MarketStatus:
    """현재 장 구간과 달력에서 확인된 휴장 여부를 별도로 보존한다."""

    code: str
    exchange: str
    state: str
    local_time: str
    kst_time: str
    detail: str
    upcoming_holiday: str = ""
    is_non_trading_day: bool = False


def market_warning_eligible(row: dict[str, Any]) -> bool:
    """종료 주문만 제외하며 체결 경고를 시장 상태의 근거로 사용하지 않는다."""
    for key in ("status", "line_status"):
        state = str(row.get(key) or "").strip().upper()
        state = state.replace(" ", "").replace("-", "").replace("_", "")
        if state in TERMINAL:
            return False
    qty = row.get("qty")
    return not (isinstance(qty, (int, float)) and qty > 0 and row.get("remain") == 0)


class MarketCalendar:
    def __init__(
        self, data: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> None:
        if (
            type(data.get("schema")) is not int
            or data.get("schema") != 1
            or not isinstance(data.get("markets"), dict)
        ):
            raise ValueError("시장 캘린더 형식이 올바르지 않습니다")
        self.source = str(data.get("source", "출처 미제공"))
        self.generated = str(data.get("generated", ""))
        end = datetime.fromisoformat(data["offset_end"])
        if end.tzinfo is None:
            raise ValueError("시차 지원 끝 시각의 시간대가 없습니다")
        self.offset_end = end.timestamp()
        self.markets = data["markets"]
        self.security_markets: dict[str, str] = {}
        self._offset_starts: dict[str, list[int]] = {}
        for code, market in self.markets.items():
            if (
                len(code) != 2
                or not code.isascii()
                or not code.isalpha()
                or not code.isupper()
            ):
                raise ValueError("시장 코드 형식 오류")
            offsets = market["offsets"]
            if not offsets or any(
                len(pair) != 2
                or any(type(value) is not int for value in pair)
                or abs(pair[1]) > 50400
                for pair in offsets
            ):
                raise ValueError(f"{code} 시차 데이터 오류")
            starts = [pair[0] for pair in offsets]
            if starts != sorted(set(starts)) or starts[-1] >= self.offset_end:
                raise ValueError(f"{code} 시차 구간 오류")
            self._offset_starts[code] = starts
            for day, row in market["days"].items():
                self._validate_day(day, row)
        if overrides is not None:
            self._apply_overrides(overrides)

    @classmethod
    def load(cls, override_path: Path | None = None) -> MarketCalendar:
        data = json.loads(
            asset_path("market_sessions.json").read_text(encoding="utf-8")
        )
        overrides = None
        if override_path and override_path.exists():
            overrides = json.loads(override_path.read_text(encoding="utf-8-sig"))
        return cls(data, overrides)

    @staticmethod
    def _validate_day(day: str, row: dict[str, Any]) -> None:
        if len(day) != 10 or str(date.fromisoformat(day)) != day:
            raise ValueError("시장 일정 날짜 오류")
        segments = row["segments"]
        if not isinstance(segments, list) or len(segments) > 6:
            raise ValueError("시장 일정 구간 오류")
        previous = None
        for segment in segments:
            if (
                len(segment) != 2
                or any(type(value) is not int for value in segment)
                or segment[0] >= segment[1]
                or (previous is not None and segment[0] < previous)
            ):
                raise ValueError("시장 일정의 시각 또는 순서 오류")
            previous = segment[1]

    def local_time(self, code: str, instant: datetime) -> datetime | None:
        if instant.tzinfo is None:
            raise ValueError("시간대가 있는 시각이 필요합니다")
        stamp = instant.timestamp()
        index = bisect_right(self._offset_starts[code], stamp) - 1
        if index < 0 or stamp >= self.offset_end:
            return None
        seconds = self.markets[code]["offsets"][index][1]
        return instant.astimezone(timezone(timedelta(seconds=seconds)))

    def _local_stamp(self, code: str, day: str, clock: str) -> int:
        target = datetime.strptime(day + " " + clock, "%Y-%m-%d %H:%M")
        candidates = set()
        for _, offset in self.markets[code]["offsets"]:
            value = int(target.replace(tzinfo=timezone.utc).timestamp()) - offset
            converted = self.local_time(
                code, datetime.fromtimestamp(value, timezone.utc)
            )
            if converted and converted.replace(tzinfo=None) == target:
                candidates.add(value)
        if len(candidates) != 1:
            raise ValueError("현지 시각이 중복되거나 지원 기간 밖입니다")
        return candidates.pop()

    def _apply_overrides(self, data: dict[str, Any]) -> None:
        if (
            type(data.get("schema")) is not int
            or data.get("schema") != 1
            or set(data) - {"schema", "security_markets", "days"}
        ):
            raise ValueError("로컬 캘린더 지정 형식 오류")
        if not isinstance(data.get("security_markets", {}), dict) or not isinstance(
            data.get("days", {}), dict
        ):
            raise ValueError("종목·날짜별 지정은 객체 형식이어야 합니다")
        for symbol, code in data.get("security_markets", {}).items():
            if (
                not isinstance(symbol, str)
                or not symbol.strip()
                or code not in self.markets
            ):
                raise ValueError("종목별 시장 지정 오류")
            self.security_markets[symbol.strip().casefold()] = code
        for code, days in data.get("days", {}).items():
            if code not in self.markets:
                raise ValueError(f"지원하지 않는 지정 시장: {code}")
            if not isinstance(days, dict):
                raise ValueError("시장별 날짜 지정 형식 오류")
            for day, row in days.items():
                if not isinstance(row, dict) or set(row) - {
                    "closed",
                    "sessions",
                    "reason",
                }:
                    raise ValueError("날짜별 지정 항목 오류")
                closed = row.get("closed")
                if type(closed) is not bool:
                    raise ValueError(
                        "휴장 지정은 closed에 true 또는 false가 필요합니다"
                    )
                if closed and row.get("sessions"):
                    raise ValueError("휴장일에 거래 세션을 함께 지정할 수 없습니다")
                segments = (
                    []
                    if closed
                    else [
                        [
                            self._local_stamp(code, day, begin),
                            self._local_stamp(code, day, end),
                        ]
                        for begin, end in row["sessions"]
                    ]
                )
                if not closed and not segments:
                    raise ValueError("거래일 지정에는 세션 시각이 필요합니다")
                converted = {
                    "segments": segments,
                    "reason": str(row.get("reason", "로컬 지정")),
                }
                self._validate_day(day, converted)
                self.markets[code]["days"][day] = converted
        day_count = sum(len(days) for days in data.get("days", {}).values())
        self.source += (
            f" · 로컬 지정 {day_count}일 · 종목 지정 {len(self.security_markets)}개"
        )

    def status(self, code: str, instant: datetime | None = None) -> MarketStatus:
        now = instant or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("시간대가 있는 시각이 필요합니다")
        kst = now.astimezone(DESK_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S KST")
        if code not in self.markets:
            return MarketStatus(code, "", "Unknown", "", kst, "대표시장 정보 없음")
        market = self.markets[code]
        local = self.local_time(code, now)
        if local is None:
            return MarketStatus(
                code, market["exchange"], "Unknown", "", kst, "시차 지원 기간 밖"
            )
        day = str(local.date())
        row = market["days"].get(day)
        state = "Unknown"
        days = market["days"]
        detail = (
            f"휴장 일정 확인 필요 · 제공 범위 {min(days)}–{max(days)}"
            if days
            else market.get("note", "휴장 일정 확인 필요")
        )
        upcoming = ""
        for shift in range(1, 8):
            future = str(local.date() + timedelta(days=shift))
            item = market["days"].get(future)
            if item and not item["segments"] and item["reason"] != "주말":
                upcoming = future
                break
        if row is not None:
            periods = row["segments"]
            stamp = now.timestamp()
            if not periods:
                state = "Closed" if row["reason"] == "주말" else "Holiday"
                detail = f"{day} {row['reason']}"
            else:
                clocks = []
                for begin, end in periods:
                    opening = datetime.fromtimestamp(begin, timezone.utc)
                    closing = datetime.fromtimestamp(end, timezone.utc)
                    clocks.append(
                        self.local_time(code, opening).strftime("%H:%M")
                        + "–"
                        + self.local_time(code, closing).strftime("%H:%M")
                        + " (KST "
                        + opening.astimezone(DESK_TIMEZONE).strftime("%m-%d %H:%M")
                        + "–"
                        + closing.astimezone(DESK_TIMEZONE).strftime("%m-%d %H:%M")
                        + ")"
                    )
                detail = " / ".join(clocks)
                if any(begin <= stamp < end for begin, end in periods):
                    state = "Open"
                elif periods[0][0] <= stamp < periods[-1][1]:
                    state = "Lunch"
                elif (
                    periods[0][0] - market.get("pre_minutes", 60) * 60
                    <= stamp
                    < periods[0][0]
                ):
                    state = "Pre-Market"
                    detail = "정규장 전 준비 구간 · " + detail
                else:
                    state = "Closed"
                    detail = (
                        "개장 전 · " if stamp < periods[0][0] else "정규장 종료 · "
                    ) + detail
        return MarketStatus(
            code,
            market["exchange"],
            state,
            local.strftime("%Y-%m-%d %H:%M:%S"),
            kst,
            market["name"] + " · " + detail,
            upcoming,
            is_non_trading_day=row is not None and not row["segments"],
        )

    def order_market(
        self,
        row: dict[str, Any],
        statuses: dict[str, MarketStatus],
        active: bool = True,
    ) -> dict[str, str]:
        """실행 상태를 보존하고 달력으로 확인된 휴장 안내만 추가한다."""
        code = self.security_markets.get(str(row.get("ticker", "")).strip().casefold())
        basis = "종목별 지정" if code else "발행국 ISO"
        code = code or str(row.get("iso_country", ""))
        status = statuses.get(code)
        state = status.state if status else "Unknown"
        warning = ""
        # 장 종료·장전·점심·일정 누락은 휴장 근거가 아니다.
        # 수량·체결·기존 경고와 별도로 달력에서 확인한 휴장일만 안내한다.
        if (
            active
            and market_warning_eligible(row)
            and status is not None
            and status.is_non_trading_day
            and state in {"Holiday", "Closed"}
        ):
            warning = f"⚠ [{code}] 금일 휴장일 확인 필요"
        return {
            "market_code": code,
            "market_status": STATE_LABELS[state],
            "market_warning": warning,
            "market_basis": basis,
            "market_exchange": status.exchange if status else "",
        }
