"""배포용 거래소 일정·시차를 생성한다. 앱 실행 및 Windows 빌드에는 필요 없다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as calendars
import pandas as pd
import tzdata

MARKETS = [
    ("US", "XNYS", "NYSE", "America/New_York"),
    ("HK", "XHKG", "HKEX", "Asia/Hong_Kong"),
    ("JP", "XTKS", "도쿄", "Asia/Tokyo"),
    ("CN", "XSHG", "상하이", "Asia/Shanghai"),
    ("SG", "XSES", "SGX", "Asia/Singapore"),
    ("AU", "XASX", "ASX", "Australia/Sydney"),
    ("DE", "XETR", "Xetra", "Europe/Berlin"),
    ("CH", "XSWX", "SIX", "Europe/Zurich"),
    ("ES", "XMAD", "마드리드", "Europe/Madrid"),
    ("FR", "XPAR", "파리", "Europe/Paris"),
    ("FI", "XHEL", "헬싱키", "Europe/Helsinki"),
    ("GB", "XLON", "런던", "Europe/London"),
    ("ID", "XIDX", "IDX", "Asia/Jakarta"),
    ("IE", "XDUB", "더블린", "Europe/Dublin"),
    ("IL", "XTAE", "텔아비브", "Asia/Tel_Aviv"),
    ("IN", "XBOM", "BSE", "Asia/Kolkata"),
    ("IT", "XMIL", "밀라노", "Europe/Rome"),
    ("MY", "XKLS", "말레이시아", "Asia/Kuala_Lumpur"),
    ("NL", "XAMS", "암스테르담", "Europe/Amsterdam"),
    ("NZ", "XNZE", "NZX", "Pacific/Auckland"),
    ("PH", "XPHS", "필리핀", "Asia/Manila"),
    ("PT", "XLIS", "리스본", "Europe/Lisbon"),
    ("SE", "XSTO", "스톡홀름", "Europe/Stockholm"),
    ("TH", "XBKK", "태국", "Asia/Bangkok"),
    ("TW", "XTAI", "대만", "Asia/Taipei"),
    ("VN", "XHCM", "호찌민", "Asia/Ho_Chi_Minh"),
]
START, END = date(2025, 1, 1), date(2027, 12, 31)


def zone(name: str) -> ZoneInfo:
    """생성에 사용한 tzdata 버전과 일치하는 시간대 파일을 읽는다."""
    source = Path(tzdata.__file__).parent / "zoneinfo" / name
    with source.open("rb") as stream:
        return ZoneInfo.from_file(stream, key=name)


def offsets(tz: ZoneInfo) -> list[list[int]]:
    """시간대 변경 순간을 초 단위로 보존하여 Windows 시간대 설치를 생략한다."""
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    stop = int(datetime(2029, 1, 1, tzinfo=timezone.utc).timestamp())

    def value(stamp: int) -> int:
        return int(
            datetime.fromtimestamp(stamp, timezone.utc)
            .astimezone(tz)
            .utcoffset()
            .total_seconds()
        )

    result = [[start, value(start)]]
    previous = start
    for instant in range(start + 21600, stop + 1, 21600):
        if value(instant) != result[-1][1]:
            lo, hi = previous, instant
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if value(mid) == result[-1][1]:
                    lo = mid
                else:
                    hi = mid
            result.append([hi, value(hi)])
        previous = instant
    return result


def local_stamp(day: date, clock: str, tz: ZoneInfo) -> int:
    return int(datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=tz).timestamp())


def generate() -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": f"exchange_calendars {calendars.__version__}; IANA tzdata {tzdata.__version__}",
        "source_url": "https://github.com/gerrymanoim/exchange_calendars",
        "offset_end": "2029-01-01T00:00:00+00:00",
        "markets": {},
    }
    for code, mic, name, timezone_name in MARKETS:
        tz = zone(timezone_name)
        result: dict[str, Any] = {
            "exchange": mic,
            "name": name,
            "timezone": timezone_name,
            "offsets": offsets(tz),
            "days": {},
            "pre_minutes": 60,
            "note": "대표 거래소 정규장 참고 일정 · 장전은 개장 전 60분 준비 구간",
        }
        end = END
        if mic not in calendars.get_calendar_names():
            result["note"] = "휴장 일정 미확인 · 로컬 날짜별 일정 지정 필요"
        else:
            try:
                cal = calendars.get_calendar(mic, start=str(START), end=str(end))
            except ValueError:
                # 미발표 연도를 거래일로 추정하지 않는다.
                end = date(2026, 12, 31)
                cal = calendars.get_calendar(mic, start=str(START), end=str(end))
            current = START
            while current <= end:
                mask = cal.weekmask
                for since, until, special_mask in getattr(cal, "special_weekmasks", []):
                    if (since is None or current >= since.date()) and (
                        until is None or current <= until.date()
                    ):
                        mask = special_mask
                result["days"][str(current)] = {
                    "segments": [],
                    "reason": "휴장" if mask[current.weekday()] == "1" else "주말",
                }
                current += timedelta(days=1)
            for index, row in cal.schedule.iterrows():
                day = index.date()
                opening, closing = (
                    int(row["open"].timestamp()),
                    int(row["close"].timestamp()),
                )
                lunch = None
                if not pd.isna(row["break_start"]):
                    lunch = (
                        int(row["break_start"].timestamp()),
                        int(row["break_end"].timestamp()),
                    )
                # 라이브러리에서 생략한 정규장 점심 구간을 현지 시각으로 보완한다.
                breaks = {
                    "CN": ("11:30", "13:00"),
                    "SG": ("12:00", "13:00"),
                    "MY": ("12:30", "14:30"),
                    "PH": ("12:00", "13:00"),
                    "TH": ("12:30", "14:00"),
                    "ID": ("11:30", "14:00")
                    if day.weekday() == 4
                    else ("12:00", "13:30"),
                }
                if lunch is None and code in breaks:
                    lunch = tuple(local_stamp(day, clock, tz) for clock in breaks[code])
                segments = [[opening, closing]]
                if lunch and opening < lunch[0] < lunch[1] < closing:
                    segments = [[opening, lunch[0]], [lunch[1], closing]]
                result["days"][str(day)] = {"segments": segments, "reason": "정규장"}
        output["markets"][code] = result
        print(code, mic, len(result["days"]))
    return output


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "assets" / "market_sessions.json"
    path.write_text(
        json.dumps(generate(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print("생성 완료:", path)
