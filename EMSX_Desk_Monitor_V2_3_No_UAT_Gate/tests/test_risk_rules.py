"""ADV 경계값·지역 범위·행 강조·경보음 파일을 검증한다."""

from __future__ import annotations

import array
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import json
from ack_engine import load_config
import unittest
import wave

from desk_core import DeskBook
from kb_theme import row_tags
from risk_rules import ASIA_COUNTRIES, adv_check, format_adv, route_adv_percent


class TestRiskRules(unittest.TestCase):
    def test_moc_threshold_inclusive_without_security_type(self) -> None:
        self.assertFalse(adv_check("MOC", "", "1.99999")[0])
        self.assertTrue(adv_check(" moc ", "", "2")[0])
        self.assertTrue(adv_check("MOC", "ETF", "2.0001")[0])
        self.assertFalse(adv_check("TWAP MOC", "ETF", "3")[0])

    def test_common_stock_rule_is_independent_and_inclusive(self) -> None:
        self.assertFalse(adv_check("TWAP", "Common Stock", "9.99999")[0])
        self.assertTrue(adv_check("TWAP", "Common Stock", "10")[0])
        self.assertTrue(adv_check("", "common stock", "10.01")[0])
        self.assertFalse(adv_check("TWAP", "ETF", "15")[0])
        result, reason = adv_check("MOC", "Common Stock", 10)
        self.assertTrue(result)
        self.assertIn("MOC", reason)
        self.assertIn("Common Stock", reason)

    def test_invalid_or_zero_average_is_not_a_ratio(self) -> None:
        for value in (None, "", 0, -1, "NaN", "Infinity", True):
            self.assertIsNone(route_adv_percent(200, value))
        for value in (None, "", -1, "NaN", True):
            self.assertIsNone(route_adv_percent(value, 10000))
        self.assertEqual(route_adv_percent(200, 10000), Decimal("2"))
        self.assertEqual(route_adv_percent(0, 10000), Decimal("0"))
        self.assertFalse(adv_check("MOC", "Common Stock", None)[0])

    def test_display_does_not_round_up_across_threshold(self) -> None:
        self.assertEqual(format_adv("1.999999"), "1.9999%")
        self.assertEqual(format_adv("9.999999"), "9.9999%")
        self.assertEqual(format_adv("2.000"), "2%")
        self.assertEqual(format_adv(None), "—")
        self.assertEqual(format_adv("0.0000001"), "<0.0001%")

    def test_route_quantity_not_parent_quantity_and_period_switch(self) -> None:
        book = DeskBook()
        book.update_reference(
            "TEST HK Equity",
            {
                "iso_country": "HK",
                "sec_type": "Common Stock",
                "avg_volume_5d": 10000,
                "avg_volume_20d": 20000,
            },
        )
        book.ingest(
            "O",
            4,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_AMOUNT": 100000,
                "EMSX_TICKER": "TEST HK Equity",
                "EMSX_NOTES": "MOC",
            },
        )
        for rid, shares in ((1, 200), (2, 100)):
            book.ingest(
                "R",
                4,
                {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": rid, "EMSX_AMOUNT": shares},
            )
        self.assertEqual(book.route_view(20)[0]["adv_pct"], Decimal("1"))
        self.assertFalse(book.route_view(20)[0]["adv_check"])
        self.assertEqual(book.route_view(5)[0]["adv_pct"], Decimal("2"))
        self.assertTrue(book.route_view(5)[0]["adv_check"])
        self.assertEqual(book.route_view(5)[1]["adv_pct"], Decimal("1"))
        book.invalidate_reference()
        self.assertIsNone(book.route_view()[0]["adv_pct"])
        self.assertFalse(book.route_view()[0]["adv_check"])

    def test_only_listed_countries_receive_unrouted_and_no_fill_alerts(self) -> None:
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
        self.assertEqual(set(ASIA_COUNTRIES), expected)
        for country in [*sorted(expected), "US", "GB", "FR", "KR", "", None]:
            with self.subTest(country=country):
                clock = [0.0]
                book = DeskBook(now=lambda: clock[0])
                book.update_reference("TEST Equity", {"iso_country": country})
                for feed in ("O", "R"):
                    book.ingest(feed, 11, {})
                book.ingest(
                    "O",
                    4,
                    {
                        "EMSX_SEQUENCE": 1,
                        "EMSX_TICKER": "TEST Equity",
                        "EMSX_STATUS": "NEW",
                        "EMSX_AMOUNT": 100,
                        "EMSX_FILLED": 0,
                    },
                )
                clock[0] = 60
                self.assertEqual(
                    "Unrouted Warning" in book.full_view(1, 1)[0]["alert_types"],
                    country in expected,
                )
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
                clock[0] = 120
                self.assertEqual(
                    "No-Fill Alert" in book.full_view(1, 1)[0]["alert_types"],
                    country in expected,
                )

    def test_reference_change_clears_region_alert_without_changing_order(self) -> None:
        clock = [0.0]
        book = DeskBook(now=lambda: clock[0])
        for feed in ("O", "R"):
            book.ingest(feed, 11, {})
        book.ingest(
            "O",
            4,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_TICKER": "TEST JP Equity",
                "EMSX_STATUS": "NEW",
                "EMSX_AMOUNT": 100,
            },
        )
        clock[0] = 600
        self.assertFalse(book.full_view()[0]["alert_types"])
        book.update_reference("TEST JP Equity", {"iso_country": " jp "})
        self.assertIn("Unrouted Warning", book.full_view()[0]["alert_types"])
        book.update_reference("TEST JP Equity", {"iso_country": "US"})
        self.assertFalse(book.full_view()[0]["alert_types"])
        self.assertEqual(book.orders[1].fields["EMSX_STATUS"], "NEW")

    def test_adv_highlight_has_single_background_and_critical_priority(self) -> None:
        self.assertEqual(row_tags(0, "Working", False, True), ("adv",))
        self.assertEqual(row_tags(1, "Working", True, True), ("alert",))
        self.assertEqual(row_tags(1, "Rejected", True, True), ("danger",))
        self.assertNotIn("even", row_tags(0, "Working", False, True))

    def test_alarm_asset_is_pcm_nonclipping_and_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with wave.open(str(root / "assets" / "desk_alert.wav"), "rb") as source:
            self.assertEqual(source.getnchannels(), 1)
            self.assertEqual(source.getsampwidth(), 2)
            duration = source.getnframes() / source.getframerate()
            samples = array.array("h", source.readframes(source.getnframes()))
        if sys.byteorder != "little":
            samples.byteswap()
        self.assertGreater(duration, 1.5)
        self.assertLess(duration, 2.5)
        self.assertGreater(max(abs(value) for value in samples), 28000)
        self.assertLess(max(abs(value) for value in samples), 32767)
        self.assertEqual(samples[0], 0)
        self.assertEqual(samples[-1], 0)
        self.assertIn(
            "desk_alert.wav", (root / "EMSX_Monitor.spec").read_text(encoding="utf-8")
        )

    def test_existing_config_preserved_and_adv_period_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin.json"
            path.write_text(
                json.dumps({"team_name": "HDGB", "beta_write_enabled": False})
            )
            self.assertEqual(load_config(path)["adv_period"], 20)
            path.write_text(json.dumps({"adv_period": 5}))
            self.assertEqual(load_config(path)["adv_period"], 5)
            for invalid in (5.0, True, 10, "20D"):
                path.write_text(json.dumps({"adv_period": invalid}))
                with self.assertRaises(ValueError):
                    load_config(path)
