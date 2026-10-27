"""거래 상태 전이와 신규 필드의 부분 갱신 보존을 검증한다."""

import unittest

from bloomberg_feed import ORDER_FIELDS
from desk_core import DeskBook, line_status


class TestOrderFields(unittest.TestCase):
    def test_partial_update_preserves_instruction_and_identity(self) -> None:
        book = DeskBook()
        book.ingest(
            "O",
            4,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_STATUS": "ASSIGN",
                "EMSX_NOTES": "TWAP",
                "EMSX_AMOUNT": 100,
            },
        )
        book.ingest(
            "R", 4, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 2, "EMSX_STATUS": "WORKING"}
        )
        book.ingest(
            "O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "PARTFILLED", "EMSX_FILLED": 10}
        )
        row = book.full_view()[0]
        self.assertEqual(row["seq"], 1)
        self.assertEqual(row["line_status"], "Part-filled")
        self.assertEqual(row["instruction"], "TWAP")
        self.assertEqual(book.route_view()[0]["instruction"], "TWAP")
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_NOTES": ""})
        self.assertEqual(book.full_view()[0]["instruction"], "")
        self.assertIn("EMSX_NOTES", ORDER_FIELDS)

    def test_assign_cancel_and_part_filled_monitoring(self) -> None:
        clock = [0.0]
        book = DeskBook(now=lambda: clock[0])
        book.update_reference("TEST HK Equity", {"iso_country": "HK"})
        for feed in ("O", "R"):
            book.ingest(feed, 11, {})
        book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_STATUS": "ASSIGN",
                "EMSX_AMOUNT": 100,
                "EMSX_TICKER": "TEST HK Equity",
            },
        )
        clock[0] = 301
        self.assertIn("Unrouted Warning", book.full_view()[0]["warning"])
        book.ingest(
            "R",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "Part-filled",
                "EMSX_FILLED": 10,
            },
        )
        clock[0] += 901
        self.assertNotIn("No-Fill Alert", book.full_view()[0]["warning"])
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "CANCEL"})
        self.assertEqual(book.full_view()[0]["line_status"], "Cancel")
        self.assertFalse(book.full_view()[0]["warning"])
        book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "CANCELED"})
        self.assertFalse(book.full_view()[0]["warning"])

    def test_unknown_and_missing_status_are_not_inferred(self) -> None:
        self.assertEqual(line_status(None), "")
        self.assertEqual(line_status("CUSTOM_STATE"), "CUSTOM_STATE")
        self.assertEqual(line_status("WORKING"), "Working")
        book = DeskBook()
        book.ingest("O", 4, {"EMSX_SEQUENCE": 1})
        self.assertEqual(book.full_view()[0]["instruction"], "")
        self.assertEqual(book.full_view()[0]["line_status"], "")
