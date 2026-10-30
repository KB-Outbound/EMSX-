"""배당락일 형식·미국 T+2 조건·다음 NYSE 영업일·우선 정렬을 검증한다."""

from __future__ import annotations

from datetime import date, datetime, timezone
from itertools import product
from typing import Any
import unittest
from unittest.mock import patch
import queue

from desk_core import DeskBook
from dividend_rules import (
    parse_ex_date,
    dividend_candidate,
    dividend_risk,
    dividend_view,
)
from market_sessions import MarketCalendar


class TestDividendRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = MarketCalendar.load()

    def test_date_formats_and_invalid_values_are_strict(self) -> None:
        expected = date(2026, 11, 2)
        for value in (
            expected,
            datetime(2026, 11, 2, tzinfo=timezone.utc),
            20261102,
            "20261102",
            " 2026-11-02 ",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_ex_date(value), expected)
        for value in (
            None,
            "",
            True,
            False,
            20261102.0,
            "2026-02-30",
            "11/02/2026",
            "2026-11-02T00:00:00",
            "２０２６１１０２",
            b"20261102",
            [],
            {},
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_ex_date(value))

    def test_exact_country_instruction_and_target_date_cross_product(self) -> None:
        target = date(2026, 11, 2)
        countries = ("US", "JP", "HK", None, "US/JP", "ＵＳ")
        instructions = ("T+2", "TWAP; t+2 / MOC", "TWAP", "T+1", None)
        dates = ("2026-11-02", "2026-11-01", "2026-11-03", None, "2026-11-00")
        for code, instruction, ex_date in product(countries, instructions, dates):
            with self.subTest(code=code, instruction=instruction, date=ex_date):
                expected = (
                    code == "US"
                    and instruction in instructions[:2]
                    and ex_date == dates[0]
                )
                row = {
                    "iso_country": code,
                    "instruction": instruction,
                    "dividend_ex_date": ex_date,
                }
                self.assertEqual(dividend_risk(row, target), expected)
        self.assertFalse(
            dividend_candidate({"iso_country": "US", "instruction": "T + 2"})
        )

    def test_friday_weekends_nyse_holidays_and_early_close(self) -> None:
        cases = (
            (date(2026, 10, 30), date(2026, 11, 2)),
            (date(2026, 11, 25), date(2026, 11, 27)),
            (date(2026, 12, 24), date(2026, 12, 28)),
            (date(2026, 12, 31), date(2027, 1, 4)),
            (date(2026, 11, 26), date(2026, 11, 27)),
        )
        for session, expected in cases:
            with self.subTest(session=session):
                self.assertEqual(
                    self.calendar.next_business_date("US", session), expected
                )

    def test_missing_calendar_and_unknown_target_do_not_become_risk(self) -> None:
        self.assertIsNone(self.calendar.next_business_date("US", date(2027, 12, 31)))
        self.assertIsNone(self.calendar.next_business_date("XX", date(2026, 10, 30)))
        self.assertIsNone(self.calendar.next_business_date("US", date.max))
        row = {
            "iso_country": "US",
            "instruction": "T+2",
            "dividend_ex_date": "2026-11-02",
        }
        self.assertFalse(dividend_risk(row, None))

    def test_local_holiday_override_is_used_for_business_date(self) -> None:
        import json
        from resource_paths import asset_path

        data = json.loads(
            asset_path("market_sessions.json").read_text(encoding="utf-8")
        )
        calendar = MarketCalendar(
            data, {"schema": 1, "days": {"US": {"2026-11-02": {"closed": True}}}}
        )
        self.assertEqual(
            calendar.next_business_date("US", date(2026, 10, 30)), date(2026, 11, 3)
        )

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "seq": seq,
                "iso_country": country,
                "instruction": instruction,
                "dividend_ex_date": ex_date,
                "create_sort": f"2026-10-30 {hour:02}:00:00",
                "basket_name": basket,
                "alert_types": (),
                "status": "WORKING",
            }
            for seq, country, instruction, ex_date, hour, basket in (
                (1, "US", "T+2", "2026-11-02", 9, "Z"),
                (2, "US", "T+2", "2026-11-03", 12, "A"),
                (3, "US", "TWAP/T+2", "2026-11-02", 8, "A"),
                (4, "JP", "T+2", "2026-11-02", 14, "B"),
                (5, "US", "TWAP", "2026-11-02", 15, "B"),
                (6, "US", "T+2", None, 16, "B"),
            )
        ]

    def test_risk_priority_is_kept_for_every_sort_mode(self) -> None:
        for basket_first, oldest_first in product((False, True), repeat=2):
            rows = self.rows()
            ordered = dividend_view(
                rows, date(2026, 10, 30), date(2026, 11, 2), basket_first, oldest_first
            )
            self.assertEqual({row["seq"] for row in ordered[:2]}, {1, 3})
            self.assertEqual({row["seq"] for row in ordered}, {1, 2, 3, 6})
            self.assertTrue(all(not row["dividend_risk"] for row in ordered[2:]))
            self.assertTrue(all(row["alert_types"] == () for row in rows))
            self.assertTrue(all(row["status"] == "WORKING" for row in rows))

    def test_session_rollover_uses_the_session_day_and_preserves_candidates(
        self,
    ) -> None:
        rows = self.rows()
        first = dividend_view(rows, date(2026, 10, 30), date(2026, 11, 2))
        self.assertTrue(any(row["dividend_risk"] for row in first))
        second = dividend_view(rows, date(2026, 11, 2), date(2026, 11, 3))
        self.assertEqual([row["seq"] for row in second if row["dividend_risk"]], [2])
        self.assertEqual({row["seq"] for row in first}, {row["seq"] for row in second})
        invalid = dividend_view(rows, date(2026, 11, 2), date(2026, 11, 2))
        self.assertFalse(any(row["dividend_risk"] for row in invalid))

    def test_book_reference_updates_and_invalidation_clear_the_date(self) -> None:
        book = DeskBook()
        book.ingest(
            "O",
            6,
            {"EMSX_SEQUENCE": 1, "EMSX_TICKER": "TEST US Equity", "EMSX_NOTES": "T+2"},
        )
        book.update_reference(
            "TEST US Equity",
            {"iso_country": "US", "dividend_ex_date": date(2026, 11, 2)},
        )
        self.assertEqual(book.full_view()[0]["dividend_ex_date"], "2026-11-02")
        book.update_reference("TEST US Equity", {"sec_type": "Common Stock"})
        self.assertTrue(dividend_risk(book.full_view()[0], date(2026, 11, 2)))
        book.update_reference("TEST US Equity", {"dividend_ex_date": "손상된 날짜"})
        self.assertEqual(book.full_view()[0]["dividend_ex_date"], "")
        book.update_reference("TEST US Equity", {"dividend_ex_date": "20261102"})
        book.invalidate_reference()
        self.assertFalse(dividend_risk(book.full_view()[0], date(2026, 11, 2)))

    def test_large_candidate_burst_is_not_dropped_or_duplicated(self) -> None:
        rows = [
            {
                "seq": index,
                "iso_country": "US",
                "instruction": "T+2",
                "dividend_ex_date": "2026-11-02" if index % 3 == 0 else "2026-11-03",
                "create_sort": "2026-10-30 09:00:00",
            }
            for index in range(3000)
        ]
        ordered = dividend_view(rows, date(2026, 10, 30), date(2026, 11, 2))
        self.assertEqual(len({row["seq"] for row in ordered}), 3000)
        self.assertTrue(all(row["dividend_risk"] for row in ordered[:1000]))
        self.assertTrue(all(not row["dividend_risk"] for row in ordered[1000:]))

    def test_demo_provides_risk_and_normal_candidates_without_bloomberg(self) -> None:
        from demo_feed import DemoWorker, SAMPLE

        messages = queue.Queue()
        worker = DemoWorker(messages, include_dividends=True)
        worker.stop()
        with patch("demo_feed.desk_today", return_value=date(2026, 10, 30)):
            worker.run()
        book = DeskBook()
        while not messages.empty():
            message = messages.get_nowait()
            if message["type"] == "reference":
                book.update_reference(message["security"], message["fields"])
            elif message["type"] == "data":
                book.ingest(message["feed"], message["status"], message["fields"])
            elif message["type"] == "subscription":
                book.subscriptions[message["feed"]] = message["status"]
        candidates = dividend_view(
            book.full_view(), date(2026, 10, 30), date(2026, 11, 2)
        )
        self.assertEqual(len(SAMPLE), 5)
        self.assertTrue(book.stream_healthy())
        self.assertEqual({row["seq"] for row in candidates}, {820006, 820007})
        self.assertEqual(
            [row["seq"] for row in candidates if row["dividend_risk"]], [820006]
        )
