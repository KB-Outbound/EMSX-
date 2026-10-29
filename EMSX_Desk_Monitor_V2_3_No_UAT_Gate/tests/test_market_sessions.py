"""로컬 거래소 일정의 서머타임·점심·휴장·범위 밖·종목 지정을 검증한다."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from desk_utilities import MARKET_CODES
from market_sessions import MarketCalendar


class TestMarketSessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = MarketCalendar.load()

    def state(self, code: str, value: str) -> str:
        return self.calendar.status(code, datetime.fromisoformat(value)).state

    def test_all_requested_markets_are_present(self) -> None:
        self.assertEqual(set(self.calendar.markets), set(MARKET_CODES))
        self.assertEqual(len(MARKET_CODES), 26)
        self.assertEqual(self.calendar.markets["US"]["exchange"], "XNYS")
        self.assertEqual(self.calendar.markets["IN"]["exchange"], "XBOM")

    def test_us_daylight_saving_preserves_local_opening_time(self) -> None:
        for instant in ("2026-01-05T14:30:00+00:00", "2026-07-06T13:30:00+00:00"):
            status = self.calendar.status("US", datetime.fromisoformat(instant))
            self.assertEqual(status.state, "Open")
            self.assertIn("09:30:00", status.local_time)
        self.assertEqual(self.state("US", "2026-07-06T12:50:00+00:00"), "Pre-Market")
        self.assertEqual(self.state("US", "2026-07-06T11:00:00+00:00"), "Closed")

    def test_country_holidays_are_exchange_specific(self) -> None:
        self.assertEqual(self.state("US", "2026-11-26T15:00:00+00:00"), "Holiday")
        self.assertEqual(self.state("US", "2026-12-25T15:00:00+00:00"), "Holiday")
        self.assertEqual(self.state("JP", "2026-12-25T01:00:00+00:00"), "Open")
        self.assertEqual(self.state("US", "2026-10-31T15:00:00+00:00"), "Closed")

    def test_us_early_close_boundary(self) -> None:
        self.assertEqual(self.state("US", "2026-11-27T17:59:59+00:00"), "Open")
        self.assertEqual(self.state("US", "2026-11-27T18:00:00+00:00"), "Closed")

    def test_asian_lunch_breaks_and_japan_1530_close(self) -> None:
        for code, instant in (
            ("JP", "2026-10-29T03:00:00+00:00"),
            ("SG", "2026-10-29T04:30:00+00:00"),
            ("MY", "2026-10-29T05:30:00+00:00"),
            ("CN", "2026-10-29T04:00:00+00:00"),
        ):
            with self.subTest(code=code):
                self.assertEqual(self.state(code, instant), "Lunch")
        self.assertEqual(self.state("JP", "2026-10-29T03:30:00+00:00"), "Open")
        self.assertEqual(self.state("JP", "2026-10-29T06:30:00+00:00"), "Closed")

    def test_local_date_and_kst_are_not_assumed_identical(self) -> None:
        instant = datetime.fromisoformat("2026-10-29T00:00:00+00:00")
        us = self.calendar.status("US", instant)
        nz = self.calendar.status("NZ", instant)
        self.assertTrue(us.local_time.startswith("2026-10-28"))
        self.assertTrue(nz.local_time.startswith("2026-10-29"))
        self.assertTrue(us.kst_time.startswith("2026-10-29 09:00"))

    def test_israel_2026_weekend_change(self) -> None:
        self.assertEqual(self.state("IL", "2026-10-30T09:00:00+00:00"), "Open")
        self.assertEqual(self.state("IL", "2026-11-01T09:00:00+00:00"), "Closed")

    def test_unpublished_and_unsupported_calendars_are_unknown(self) -> None:
        for code in ("CN", "SG", "IN"):
            self.assertEqual(self.state(code, "2027-05-04T02:00:00+00:00"), "Unknown")
        self.assertEqual(self.state("VN", "2026-10-29T02:00:00+00:00"), "Unknown")
        self.assertEqual(self.state("US", "2029-01-01T15:00:00+00:00"), "Unknown")
        with self.assertRaises(ValueError):
            self.calendar.status("US", datetime(2026, 10, 29))

    def load_overrides(self, data: dict) -> MarketCalendar:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "시장.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return MarketCalendar.load(path)

    def test_local_holiday_override_and_security_market_override(self) -> None:
        calendar = self.load_overrides(
            {
                "schema": 1,
                "security_markets": {"ADR US Equity": "US"},
                "days": {"JP": {"2026-10-29": {"closed": True, "reason": "임시 휴장"}}},
            }
        )
        now = datetime.fromisoformat("2026-10-29T14:00:00+00:00")
        statuses = {code: calendar.status(code, now) for code in MARKET_CODES}
        self.assertEqual(statuses["JP"].state, "Holiday")
        row = calendar.order_market(
            {"ticker": "ADR US Equity", "iso_country": "JP"}, statuses
        )
        self.assertEqual(row["market_code"], "US")
        self.assertEqual(row["market_basis"], "종목별 지정")
        self.assertEqual(row["market_warning"], "")

    def test_local_sessions_can_confirm_a_missing_vietnam_day(self) -> None:
        calendar = self.load_overrides(
            {
                "schema": 1,
                "days": {
                    "VN": {
                        "2026-10-29": {
                            "closed": False,
                            "sessions": [["09:00", "11:30"], ["13:00", "14:45"]],
                        }
                    }
                },
            }
        )
        self.assertEqual(
            calendar.status(
                "VN", datetime.fromisoformat("2026-10-29T03:00:00+00:00")
            ).state,
            "Open",
        )
        self.assertEqual(
            calendar.status(
                "VN", datetime.fromisoformat("2026-10-29T05:00:00+00:00")
            ).state,
            "Lunch",
        )
        self.assertEqual(
            calendar.status(
                "VN", datetime.fromisoformat("2026-10-30T03:00:00+00:00")
            ).state,
            "Unknown",
        )

    def test_malformed_overrides_do_not_become_open_or_holiday(self) -> None:
        invalid = [
            {"schema": 2},
            {"schema": True},
            {"schema": 1, "day": {}},
            {"schema": 1, "days": []},
            {"schema": 1, "security_markets": {"TEST": "XX"}},
            {"schema": 1, "days": {"JP": {"2026-10-29": {"closed": "false"}}}},
            {
                "schema": 1,
                "days": {"JP": {"2026-10-29": {"closed": False, "sessions": []}}},
            },
            {
                "schema": 1,
                "days": {
                    "JP": {
                        "2026-10-29": {
                            "closed": False,
                            "sessions": [["15:00", "09:00"]],
                        }
                    }
                },
            },
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load_overrides(data)

    def test_holiday_badges_are_advisory_and_terminal_rows_are_suppressed(self) -> None:
        statuses = {
            "JP": self.calendar.status(
                "JP", datetime.fromisoformat("2026-01-01T01:00:00+00:00")
            )
        }
        row = {"ticker": "TEST JP Equity", "iso_country": "JP", "alert_types": ()}
        market = self.calendar.order_market(row, statuses)
        self.assertIn("금일 휴장일 확인 필요", market["market_warning"])
        self.assertEqual(row["alert_types"], ())
        self.assertEqual(
            self.calendar.order_market(row, statuses, active=False)["market_warning"],
            "",
        )

    def test_clock_closure_and_calendar_holiday_are_distinct(self) -> None:
        cases = (
            ("US", "2026-10-29T00:00:00+00:00", "Closed", False),
            ("NL", "2026-10-29T00:00:00+00:00", "Closed", False),
            ("JP", "2026-10-29T03:00:00+00:00", "Lunch", False),
            ("US", "2026-07-06T12:50:00+00:00", "Pre-Market", False),
            ("US", "2026-11-26T15:00:00+00:00", "Holiday", True),
            ("US", "2026-10-31T15:00:00+00:00", "Closed", True),
            ("VN", "2026-10-29T02:00:00+00:00", "Unknown", False),
        )
        for code, instant, state, closed_day in cases:
            with self.subTest(code=code, instant=instant):
                status = self.calendar.status(code, datetime.fromisoformat(instant))
                self.assertEqual(status.state, state)
                self.assertEqual(status.is_non_trading_day, closed_day)
                market = self.calendar.order_market(
                    {"iso_country": code, "status": "NEW"}, {code: status}
                )
                self.assertEqual(bool(market["market_warning"]), closed_day)
                if state == "Closed":
                    self.assertEqual(market["market_status"], "장 종료")

    def test_execution_states_do_not_create_session_warning_or_change_fields(
        self,
    ) -> None:
        cases = (
            ("US", "2026-10-29T00:00:00+00:00"),
            ("NL", "2026-10-29T00:00:00+00:00"),
            ("JP", "2026-10-29T03:00:00+00:00"),
            ("VN", "2026-10-29T02:00:00+00:00"),
        )
        for code, instant in cases:
            status = self.calendar.status(code, datetime.fromisoformat(instant))
            for execution in (
                "NEW",
                "WORKING",
                "PARTFILL",
                "Partial fill",
                "No fill",
                "Cancel",
            ):
                for alerts in ((), ("No-Fill Alert",), ("Unrouted Warning",)):
                    with self.subTest(code=code, execution=execution, alerts=alerts):
                        row = {
                            "iso_country": code,
                            "status": execution,
                            "alert_types": alerts,
                            "warning": " / ".join(alerts),
                            "qty": 100,
                            "filled": 40 if execution == "PARTFILL" else 0,
                            "remain": 60 if execution == "PARTFILL" else 100,
                        }
                        before = row.copy()
                        market = self.calendar.order_market(row, {code: status})
                        self.assertEqual(market["market_warning"], "")
                        self.assertEqual(row, before)

    def test_cancel_variants_and_completed_quantities_suppress_holiday_badge(
        self,
    ) -> None:
        status = self.calendar.status(
            "JP", datetime.fromisoformat("2026-01-01T01:00:00+00:00")
        )
        base: dict[str, Any] = {
            "iso_country": "JP",
            "qty": 100,
            "remain": 100,
            "status": "NEW",
        }
        cases: list[dict[str, Any]] = [
            {"status": state}
            for state in (
                "CANCEL",
                " Cancel ",
                "cancelled",
                "Canceled",
                "FILLED",
                "COMPLETED",
                "EXPIRED",
                "Rejected",
                "DELETED",
            )
        ]
        cases.extend(
            [
                {"status": "", "line_status": "Cancel"},
                {"status": None, "line_status": "Completed"},
                {"status": "NEW", "remain": 0},
            ]
        )
        for fields in cases:
            with self.subTest(fields=fields):
                market = self.calendar.order_market({**base, **fields}, {"JP": status})
                self.assertEqual(market["market_warning"], "")

    def test_confirmed_holiday_warning_is_short_and_has_no_basis_suffix(self) -> None:
        status = self.calendar.status(
            "JP", datetime.fromisoformat("2026-01-01T01:00:00+00:00")
        )
        row = {"ticker": "TEST JP Equity", "iso_country": "JP", "status": "NEW"}
        self.assertEqual(
            self.calendar.order_market(row, {"JP": status})["market_warning"],
            "⚠ [JP] 금일 휴장일 확인 필요",
        )

    def test_asset_is_included_in_standalone_build(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertIn(
            "market_sessions.json",
            (root / "EMSX_Monitor.spec").read_text(encoding="utf-8"),
        )
