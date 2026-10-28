"""지정 국가의 두 시간 경고와 MOC No-Fill 제외 규칙을 검증한다."""

from __future__ import annotations

from itertools import product
import string
from typing import Any
import unittest
from unittest.mock import patch

from alerting import AlertTracker
from desk_core import DeskBook
from risk_rules import (
    ASIA_COUNTRIES,
    asian_delay_eligible,
    contains_moc_instruction,
    country_code,
)


class CountryLike:
    """문자열 변환만으로 국가코드를 흉내 내는 잘못된 입력이다."""

    def __str__(self) -> str:
        return "JP"


class TestRegionalAlerts(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [0.0]

    def book(self, country: Any, instruction: str = "TWAP") -> DeskBook:
        self.clock[0] = 0.0
        book = DeskBook(now=lambda: self.clock[0])
        for feed in ("O", "R"):
            book.ingest(feed, 11, {})
        book.update_reference("TEST Equity", {"iso_country": country})
        book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_TICKER": "TEST Equity",
                "EMSX_STATUS": "NEW",
                "EMSX_AMOUNT": 100,
                "EMSX_FILLED": 0,
                "EMSX_NOTES": instruction,
            },
        )
        return book

    def route(self, book: DeskBook) -> None:
        book.ingest(
            "R",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "WORKING",
                "EMSX_AMOUNT": 100,
                "EMSX_FILLED": 0,
            },
        )

    def test_only_exact_12_country_codes_pass_both_delay_checks(self) -> None:
        expected = {
            "CN",
            "HK",
            "ID",
            "IN",
            "JP",
            "NZ",
            "MY",
            "SG",
            "TH",
            "TW",
            "VN",
            "AU",
        }
        self.assertEqual(ASIA_COUNTRIES, expected)
        for letters in product(string.ascii_uppercase, repeat=2):
            code = "".join(letters)
            with self.subTest(country=code):
                book = self.book(code)
                self.clock[0] = 600.0
                before = book.full_view(1, 1)[0]
                self.assertEqual(
                    "Unrouted Warning" in before["alert_types"], code in expected
                )
                self.route(book)
                self.clock[0] = 1200.0
                after = book.full_view(1, 1)[0]
                self.assertEqual(
                    "No-Fill Alert" in after["alert_types"], code in expected
                )
                self.assertNotIn("Unrouted Warning", after["alert_types"])

    def test_country_validation_precedes_unicode_uppercase_conversion(self) -> None:
        malformed: tuple[Any, ...] = (
            None,
            "",
            "JPN",
            "Japan",
            "JP/US",
            "JP,US",
            "JP\x00",
            "J P",
            "ın",
            "ıd",
            "ｊｐ",
            "ＨＫ",
            "JΡ",
            "J\u200bP",
            "JP\nUS",
            12,
            True,
            b"JP",
            ["JP"],
            {"iso_country": "JP"},
            CountryLike(),
        )
        for code in malformed:
            with self.subTest(country=repr(code)):
                self.assertEqual(country_code(code), "")
                self.assertFalse(asian_delay_eligible(code))
                book = self.book(code)
                self.clock[0] = 1000.0
                self.assertFalse(book.full_view(1, 1)[0]["alert_types"])
                self.route(book)
                self.clock[0] = 2000.0
                self.assertFalse(book.full_view(1, 1)[0]["alert_types"])

    def test_valid_ascii_case_and_whitespace_share_display_and_filter(self) -> None:
        for code in sorted(ASIA_COUNTRIES):
            with self.subTest(country=code):
                book = self.book(" " + code.lower() + " ")
                self.clock[0] = 60.0
                row = book.full_view(1, 1)[0]
                self.assertEqual(row["iso_country"], code)
                self.assertIn("Unrouted Warning", row["alert_types"])

    def test_moc_contains_match_excludes_no_fill_in_every_allowed_country(self) -> None:
        instructions = (
            "MOC",
            " moc ",
            "TWAP; MOC",
            "MOC / LIMIT 100",
            "client=mOc",
            "고객 지시: MOC 종가 집행",
            "VWAP+MOC",
            "MOC123",
        )
        for code in sorted(ASIA_COUNTRIES):
            for instruction in instructions:
                with self.subTest(country=code, instruction=instruction):
                    book = self.book(code, instruction)
                    self.route(book)
                    self.clock[0] = 86400.0
                    with patch("desk_core.known_zero") as fill_check:
                        row = book.full_view(1, 1)[0]
                    fill_check.assert_not_called()
                    self.assertFalse(row["alert_types"])
                    self.assertNotIn("No-Fill Alert", row["warning"])

    def test_moc_unrouted_orders_still_receive_route_delay(self) -> None:
        for code in sorted(ASIA_COUNTRIES):
            with self.subTest(country=code):
                book = self.book(code, "TWAP + MOC")
                self.clock[0] = 60.0
                self.assertIn(
                    "Unrouted Warning", book.full_view(1, 1)[0]["alert_types"]
                )

    def test_non_moc_instructions_remain_subject_to_no_fill(self) -> None:
        for instruction in ("", "TWAP", "VWAP", "MOO", "LOC", "MAN", "AUTO"):
            with self.subTest(instruction=instruction):
                book = self.book("HK", instruction)
                self.route(book)
                self.clock[0] = 59.0
                self.assertNotIn(
                    "No-Fill Alert", book.full_view(1, 1)[0]["alert_types"]
                )
                self.clock[0] = 60.0
                self.assertIn("No-Fill Alert", book.full_view(1, 1)[0]["alert_types"])

    def test_country_correction_or_invalidation_clears_existing_no_fill(self) -> None:
        for corrected in ("US", "GB", "FR", "DE", "CA", "BR", "KR", "", None, "ın"):
            with self.subTest(country=corrected):
                book = self.book("JP")
                self.route(book)
                self.clock[0] = 600.0
                self.assertIn("No-Fill Alert", book.full_view(1, 1)[0]["alert_types"])
                book.update_reference("TEST Equity", {"iso_country": corrected})
                self.assertFalse(book.full_view(1, 1)[0]["alert_types"])
        book = self.book("JP")
        self.route(book)
        self.clock[0] = 600.0
        book.invalidate_reference()
        self.assertFalse(book.full_view(1, 1)[0]["alert_types"])

    def test_ticker_change_cannot_reuse_previous_security_country(self) -> None:
        book = self.book("JP")
        self.route(book)
        self.clock[0] = 600.0
        self.assertIn("No-Fill Alert", book.full_view(1, 1)[0]["alert_types"])
        book.update_reference("OTHER Equity", {"iso_country": "US"})
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_TICKER": "OTHER Equity"})
        row = book.full_view(1, 1)[0]
        self.assertEqual(row["iso_country"], "US")
        self.assertFalse(row["alert_types"])

    def test_moc_update_clears_visual_and_audio_no_fill_and_survives_partial_update(
        self,
    ) -> None:
        book = self.book("JP")
        self.route(book)
        self.clock[0] = 600.0
        tracker = AlertTracker(now=lambda: self.clock[0])
        self.assertTrue(tracker.update(book.full_view(1, 1)))
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_NOTES": "TWAP; MOC"})
        self.assertFalse(tracker.update(book.full_view(1, 1)))
        self.assertFalse(tracker.active)
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_AVG_PRICE": 123.0})
        self.clock[0] = 1200.0
        self.assertFalse(tracker.update(book.full_view(1, 1)))
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_NOTES": "TWAP"})
        self.assertTrue(tracker.update(book.full_view(1, 1)))

    def test_non_asian_order_never_enters_no_fill_calculation(self) -> None:
        for code in ("US", "GB", "FR", "DE", "CA", "BR", "KR", None):
            with self.subTest(country=code):
                book = self.book(code)
                self.route(book)
                self.clock[0] = 86400.0
                with patch("desk_core.known_zero") as fill_check:
                    self.assertFalse(book.full_view(1, 1)[0]["alert_types"])
                fill_check.assert_not_called()

    def test_route_closed_is_a_separate_existing_alert(self) -> None:
        book = self.book("US", "MOC")
        self.route(book)
        book.ingest(
            "R",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "CANCEL",
            },
        )
        self.clock[0] = 600.0
        row = book.full_view(1, 1)[0]
        self.assertEqual(row["alert_types"], ("Route Closed Alert",))

    def test_moc_rule_uses_contains_match_and_rejects_nontext(self) -> None:
        for value in ("MOC", "moc", "hello MOC world", "TWAP+MOC", "MOC123"):
            self.assertTrue(contains_moc_instruction(value))
        for value in (None, True, 1, "", "MOO", "LOC", "TWAP"):
            self.assertFalse(contains_moc_instruction(value))


if __name__ == "__main__":
    unittest.main()
