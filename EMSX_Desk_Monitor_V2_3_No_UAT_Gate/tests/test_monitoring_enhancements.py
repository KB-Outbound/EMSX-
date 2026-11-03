"""미라우팅 전환·통화별 금액·자동 Ack 초기 활성화와 OFF 경합을 검증한다."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock
from typing import Any

from ack_engine import AckEngine, AckLedger, default_config
from bloomberg_feed import BloombergWorker
from desk_core import DeskBook
from test_ack_engine import ORDER
from test_feed_initialization import Message


class TestMonitoringRows(unittest.TestCase):
    def setUp(self) -> None:
        self.book = DeskBook()

    def test_new_pending_and_ord_pend_are_visible_without_fake_broker_totals(
        self,
    ) -> None:
        for seq, status in enumerate(
            ("NEW", "PENDING", "ORD-PEND", "CANCEL", "FILLED"), 1
        ):
            self.book.ingest(
                "O",
                6,
                {
                    "EMSX_SEQUENCE": seq,
                    "EMSX_STATUS": status,
                    "EMSX_ACCOUNT": f"FUND{seq}",
                    "EMSX_TICKER": "TEST US Equity",
                    "EMSX_AMOUNT": 100,
                    "EMSX_NOTES": "T+2",
                },
            )
        rows = self.book.monitoring_view()
        self.assertEqual({row["seq"] for row in rows}, {1, 2, 3})
        self.assertTrue(all(row["is_unrouted"] for row in rows))
        self.assertEqual(self.book.broker_summary(rows), [])
        self.assertEqual(self.book.counters()["routes"], 0)
        self.assertEqual({row["account"] for row in rows}, {"FUND1", "FUND2", "FUND3"})

    def test_route_creation_split_updates_and_deletion_do_not_duplicate_orders(
        self,
    ) -> None:
        self.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_STATUS": "NEW",
                "EMSX_AMOUNT": 100,
            },
        )
        self.assertEqual(len(self.book.monitoring_view()), 1)
        for rid in (1, 2):
            self.book.ingest(
                "R",
                6,
                {
                    "EMSX_SEQUENCE": 1,
                    "EMSX_ROUTE_ID": rid,
                    "EMSX_AMOUNT": 50,
                    "EMSX_BROKER": "JEFF",
                    "EMSX_STATUS": "WORKING",
                },
            )
        rows = self.book.monitoring_view()
        self.assertEqual({row["route_id"] for row in rows}, {1, 2})
        self.assertTrue(all(not row["is_unrouted"] for row in rows))
        for rid in (1, 2):
            self.book.ingest("R", 8, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": rid})
        self.assertTrue(self.book.monitoring_view()[0]["is_unrouted"])
        self.book.ingest("O", 8, {"EMSX_SEQUENCE": 1})
        self.assertEqual(self.book.monitoring_view(), [])


class TestBrokerNotional(unittest.TestCase):
    def setUp(self) -> None:
        self.book = DeskBook()

    def route(self, seq: int = 1, currency: str = "USD", **fields: Any) -> None:
        self.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": seq,
                "EMSX_CURRENCY": currency,
                "EMSX_LIMIT_PRICE": "100.10",
                "EMSX_STATUS": "WORKING",
            },
        )
        self.book.ingest(
            "R",
            6,
            {
                "EMSX_SEQUENCE": seq,
                "EMSX_ROUTE_ID": 1,
                "EMSX_BROKER": "JEFF",
                "EMSX_AMOUNT": 100,
                "EMSX_FILLED": 20,
                "EMSX_AVG_PRICE": "99.90",
                "EMSX_STATUS": "WORKING",
                **fields,
            },
        )

    def test_exec_plus_working_price_and_currency_groups_are_separate(self) -> None:
        self.route()
        self.route(2, "JPY", EMSX_LIMIT_PRICE="200")
        result = {row["currency"]: row for row in self.book.broker_summary()}
        self.assertEqual(result["USD"]["notional_value"], Decimal("10006.00"))
        self.assertEqual(result["JPY"]["notional_value"], Decimal("17998.00"))
        self.assertEqual(len(result), 2)
        self.book.ingest(
            "R",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_FILLED": 20,
            },
        )
        self.assertEqual(self.book.broker_summary()[0]["routes"], 1)

    def test_unpriced_market_route_is_unknown_and_does_not_show_partial_total(
        self,
    ) -> None:
        self.route()
        self.route(2, EMSX_LIMIT_PRICE=0)
        row = self.book.broker_summary()[0]
        self.assertIsNone(row["notional_value"])
        self.assertEqual(row["notional_missing"], 1)
        self.assertEqual(row["known_notional"], Decimal("10006.00"))

    def test_cancel_remainder_and_completed_routes_use_only_actual_fills(self) -> None:
        self.route(EMSX_STATUS="CANCEL", EMSX_LIMIT_PRICE=0)
        self.assertEqual(
            self.book.broker_summary()[0]["notional_value"], Decimal("1998.00")
        )
        self.book.ingest(
            "R",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "FILLED",
                "EMSX_FILLED": 100,
                "EMSX_AVG_PRICE": "99.95",
            },
        )
        self.assertEqual(
            self.book.broker_summary()[0]["notional_value"], Decimal("9995.00")
        )

    def test_pence_conversion_null_currency_and_corrupt_prices(self) -> None:
        self.route(currency="GBp")
        self.assertEqual(self.book.broker_summary()[0]["currency"], "GBP")
        self.assertEqual(
            self.book.broker_summary()[0]["notional_value"], Decimal("100.0600")
        )
        for currency, price in (("", "100"), ("USD", "NaN"), ("USD", True)):
            with self.subTest(currency=currency, price=price):
                self.book = DeskBook()
                self.route(currency=currency, EMSX_LIMIT_PRICE=price)
                self.assertIsNone(self.book.broker_summary()[0]["notional_value"])

    def test_parent_and_route_price_units_are_converted_or_rejected(self) -> None:
        self.route(currency="GBp", EMSX_CURRENCY="GBP", EMSX_AVG_PRICE="0.999")
        self.assertEqual(
            self.book.broker_summary()[0]["notional_value"], Decimal("100.060")
        )
        self.book = DeskBook()
        self.route(currency="USD", EMSX_CURRENCY="JPY")
        self.assertIsNone(self.book.broker_summary()[0]["notional_value"])

    def test_optional_route_failure_keeps_valid_parent_price_and_currency(self) -> None:
        self.route(EMSX_CURRENCY="USD", EMSX_LIMIT_PRICE="101")
        self.book.clear_optional_field("EMSX_CURRENCY", "R")
        self.book.clear_optional_field("EMSX_LIMIT_PRICE", "R")
        row = self.book.broker_summary()[0]
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["notional_value"], Decimal("10006.00"))


class TestDefaultAutoAck(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = AckLedger(Path(self.directory.name) / "ack.db")
        self.addCleanup(self.ledger.close)
        self.worker = BloombergWorker("BETA", "HDGB", queue.Queue(), auto_ack=True)
        self.sent = Mock()
        self.engine = AckEngine(
            "BETA",
            "HDGB",
            {**default_config(), "beta_write_enabled": True},
            self.ledger,
            self.worker._notify,
            batch_send_func=self.sent,
        )
        self.addCleanup(self.engine.close)
        self.worker.engine = self.engine
        self.worker.started = {"O", "R"}

    def ready(self) -> None:
        self.worker._handle_data_message(Message({"EVENT_STATUS": 11}, cid=901))
        self.worker._handle_data_message(Message({"EVENT_STATUS": 11}, cid=902))

    def test_snapshot_is_excluded_and_new_burst_is_one_batch_without_manual_arm(
        self,
    ) -> None:
        self.worker._handle_data_message(Message({"EVENT_STATUS": 4, **ORDER}))
        self.assertFalse(self.engine.armed)
        self.ready()
        self.assertTrue(self.engine.armed)
        for seq in range(201, 301):
            self.worker._handle_data_message(
                Message(
                    {
                        "EVENT_STATUS": 6,
                        **ORDER,
                        "EMSX_SEQUENCE": seq,
                    }
                )
            )
        self.engine.flush_batch(force=True)
        self.sent.assert_called_once()
        self.assertEqual(self.sent.call_args.args[0], tuple(range(201, 301)))

    def test_off_queued_before_snapshot_completion_prevents_activation(self) -> None:
        self.worker.commands.put({"action": "arm", "armed": False})
        self.ready()
        self.assertFalse(self.engine.armed)
        self.assertFalse(self.worker._auto_arm_pending)
        self.sent.assert_not_called()

    def test_existing_admin_gate_is_checked_once_without_retry_loop(self) -> None:
        self.engine.config["beta_write_enabled"] = False
        self.ready()
        self.assertFalse(self.engine.armed)
        for _ in range(10):
            self.worker._maybe_auto_arm()
        denied = []
        while not self.worker.output.empty():
            item = self.worker.output.get_nowait()
            if item["type"] == "ack_armed":
                denied.append(item)
        self.assertEqual(len(denied), 1)
        self.sent.assert_not_called()

    def test_repeated_on_does_not_clear_queue_and_off_cancels_it(self) -> None:
        self.ready()
        self.worker._handle_data_message(Message({"EVENT_STATUS": 6, **ORDER}))
        self.worker.commands.put({"action": "arm", "armed": True})
        self.worker._commands()
        self.assertIn(111, self.engine.queued)
        self.worker.commands.put({"action": "arm", "armed": False})
        self.worker._commands()
        self.engine.flush_batch(force=True)
        self.sent.assert_not_called()

    def test_critical_drop_cancels_startup_intent(self) -> None:
        with self.assertLogs("emsx", level="ERROR"):
            self.worker._trip("모의 연결 단절")
        self.ready()
        self.assertFalse(self.worker.auto_ack_requested)
        self.assertFalse(self.engine.armed)
