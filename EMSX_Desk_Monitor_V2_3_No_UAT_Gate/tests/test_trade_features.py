"""날짜 경계·표시 필터·경고음·자동 Ack 날짜 안전장치를 검증한다."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from typing import Any

from ack_engine import AckEngine, AckLedger, default_config
from alerting import AlertTracker, play_alarm
from bloomberg_feed import ORDER_FIELDS
from desk_core import DeskBook
from emsx_desk_monitor import MonitorApp
from order_fields import client_instruction, creation_fields, recent_order, sort_orders
from test_ack_engine import ORDER

TODAY = date(2026, 1, 1)


class TestTradeFields(unittest.TestCase):
    def test_year_boundary_and_invalid_dates(self) -> None:
        for value in (20260101, 20251231, "2025-12-31"):
            self.assertTrue(recent_order({"EMSX_DATE": value}, TODAY))
        for value in (
            None,
            "",
            20251230,
            20260102,
            20260230,
            202611,
            "20260101.0",
            True,
        ):
            self.assertFalse(recent_order({"EMSX_DATE": value}, TODAY))
        self.assertFalse(recent_order({"EMSX_DATE": 20251231}, TODAY, True))
        self.assertTrue(recent_order({"EMSX_DATE": 20260101}, TODAY, True))

    def test_create_time_and_missing_time(self) -> None:
        self.assertEqual(
            creation_fields({"EMSX_DATE": 20260101, "EMSX_TIME": 3601})["create_time"],
            "2026-01-01 01:00:01",
        )
        self.assertEqual(
            creation_fields({"EMSX_DATE": 20260101, "EMSX_TIME": 0})["create_time"],
            "2026-01-01 00:00:00",
        )
        self.assertEqual(
            creation_fields({"EMSX_DATE": 20260101, "EMSX_TIME": 86400})["create_time"],
            "2026-01-01",
        )
        self.assertEqual(creation_fields({"EMSX_TIME": 3600})["create_sort"], "")

    def test_notes_are_client_instruction_not_operational_flag(self) -> None:
        for note in ("TWAP", "MOC", "TWAP; limit 100"):
            self.assertEqual(
                client_instruction(
                    {"EMSX_NOTES": note, "EMSX_HAND_INSTRUCTION": "AUTO"}
                ),
                note,
            )
        for note in ("MAN", "AUTO", " manual ", "", None):
            self.assertEqual(client_instruction({"EMSX_NOTES": note}), "")
        self.assertEqual(client_instruction({"EMSX_HAND_INSTRUCTION": "TWAP"}), "")
        self.assertTrue(
            {"EMSX_NOTES", "EMSX_BASKET_NAME", "EMSX_DATE", "EMSX_TIME"}.issubset(
                ORDER_FIELDS
            )
        )

    def test_chronology_precedes_sequence_and_basket_group_is_stable(self) -> None:
        rows = [
            {"seq": 100, "create_sort": "2026-01-01 10:00:00", "basket_name": "B"},
            {"seq": 1, "create_sort": "2026-01-01 12:00:00", "basket_name": "A"},
            {"seq": 3, "create_sort": "2026-01-01 11:00:00", "basket_name": "A"},
        ]
        self.assertEqual([r["seq"] for r in sort_orders(rows)], [1, 3, 100])
        self.assertEqual(
            [r["seq"] for r in sort_orders(rows, oldest_first=True)], [100, 3, 1]
        )
        self.assertEqual(
            [r["seq"] for r in sort_orders(rows, basket_first=True)], [1, 3, 100]
        )


class TestDeskAlerts(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [0.0]
        self.book = DeskBook(now=lambda: self.clock[0])
        for feed in ("O", "R"):
            self.book.ingest(feed, 11, {})
        self.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_STATUS": "ORD-PEND",
                "EMSX_AMOUNT": 100,
                "EMSX_FILLED": 0,
            },
        )

    def test_unrouted_timer_begins_after_ack_pending_clears(self) -> None:
        self.clock[0] = 1000
        self.assertNotIn("Unrouted Warning", self.book.full_view(1, 1)[0]["warning"])
        self.book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "NEW"})
        self.clock[0] = 1059
        self.assertNotIn("Unrouted Warning", self.book.full_view(1, 1)[0]["warning"])
        self.clock[0] = 1060
        self.assertIn("Unrouted Warning", self.book.full_view(1, 1)[0]["warning"])
        self.book.ingest(
            "R",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "WORKING",
                "EMSX_FILLED": 0,
            },
        )
        self.assertNotIn("Unrouted Warning", self.book.full_view(1, 1)[0]["warning"])

    def test_no_fill_timer_route_transition_and_partial_fill(self) -> None:
        self.book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "WORKING"})
        self.book.ingest(
            "R",
            4,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_STATUS": "CANCEL",
                "EMSX_FILLED": 0,
            },
        )
        self.clock[0] = 1000
        self.book.ingest(
            "R", 7, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 1, "EMSX_STATUS": "WORKING"}
        )
        self.clock[0] = 1059
        self.assertNotIn("No-Fill Alert", self.book.full_view(1, 1)[0]["warning"])
        self.clock[0] = 1060
        self.assertIn("No-Fill Alert", self.book.full_view(1, 1)[0]["warning"])
        self.book.ingest(
            "R", 7, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 1, "EMSX_FILLED": 10}
        )
        self.assertNotIn("No-Fill Alert", self.book.full_view(1, 1)[0]["warning"])
        self.book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "CANCEL"})
        self.assertFalse(self.book.full_view(1, 1)[0]["alert_types"])

    def test_unknown_fills_are_not_zero_fills(self) -> None:
        self.book.ingest(
            "R", 6, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 1, "EMSX_STATUS": "WORKING"}
        )
        self.clock[0] = 1000
        self.assertNotIn("No-Fill Alert", self.book.full_view(1, 1)[0]["warning"])
        for invalid in ("bad", "", float("nan"), True):
            self.book.ingest(
                "R", 7, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 1, "EMSX_FILLED": invalid}
            )
            self.assertNotIn("No-Fill Alert", self.book.full_view(1, 1)[0]["warning"])

    def test_tracker_repeats_and_rearms_after_resolution(self) -> None:
        tracker = AlertTracker(now=lambda: self.clock[0])
        rows = [{"seq": 1, "alert_types": ("Unrouted Warning",)}]
        self.assertTrue(tracker.update(rows))
        self.clock[0] = 29.9
        self.assertFalse(tracker.update(rows))
        self.clock[0] = 30
        self.assertTrue(tracker.update(rows))
        self.assertFalse(tracker.update([]))
        self.assertTrue(tracker.update(rows))
        self.assertFalse(tracker.update(rows, False))

    def test_sound_uses_async_windows_api_and_fallback(self) -> None:
        fallback = Mock()
        sound = SimpleNamespace(PlaySound=Mock(), SND_ALIAS=1, SND_ASYNC=2)
        with patch.dict(sys.modules, {"winsound": sound}):
            play_alarm(fallback)
            sound.PlaySound.assert_called_once_with("SystemExclamation", 3)
            fallback.assert_not_called()
            sound.PlaySound.side_effect = RuntimeError("모의 장치 오류")
            play_alarm(fallback)
        fallback.assert_called_once()
        fallback.reset_mock()
        with patch.dict(sys.modules, {"winsound": None}):
            play_alarm(fallback)
        fallback.assert_called_once()


class TestAckDateSafety(unittest.TestCase):
    def test_old_missing_and_rollover_dates_never_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = AckLedger(Path(tmp) / "ack.db")
            sent = Mock()
            config = {**default_config(), "beta_write_enabled": True}
            engine = AckEngine(
                "BETA",
                "HDGB",
                config,
                ledger,
                Mock(),
                sent,
                date_guard=lambda order: recent_order(order, TODAY),
            )
            try:
                engine.set_ready(True)
                engine.set_arm(True)
                for seq, value in ((1, 20251230), (2, None), (3, 20260102)):
                    engine.on_order(
                        6,
                        {**ORDER, "EMSX_SEQUENCE": seq, "EMSX_DATE": value},
                        True,
                        True,
                    )
                sent.assert_not_called()
                engine.on_order(6, {**ORDER, "EMSX_DATE": 20260101}, True, True)
                sent.assert_called_once()
                sent.reset_mock()
                engine.date_guard = Mock(side_effect=[True, False])
                engine.on_order(
                    6,
                    {**ORDER, "EMSX_SEQUENCE": 112, "EMSX_DATE": 20260101},
                    True,
                    True,
                )
                sent.assert_not_called()
                self.assertIsNone(ledger.get("BETA", "HDGB", 112))
            finally:
                engine.close()
                ledger.close()


class Variable:
    """화면 없이 표시 계층의 실제 조합 로직을 실행하기 위한 값 저장소다."""

    def __init__(self, value: Any = "") -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


class Tree:
    """행 식별자와 표시 값·순서를 보존하는 최소 표 대역이다."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.rows)

    def item(self, iid: str, option: str = "", **values: Any) -> Any:
        self.rows[iid].update(values)
        return self.rows[iid].get(option, ())

    def insert(self, parent: str, position: str, iid: str, **values: Any) -> None:
        self.rows[iid] = values

    def move(self, iid: str, parent: str, index: int) -> None:
        pass

    def delete(self, iid: str) -> None:
        self.rows.pop(iid)


class TestDisplayIntegration(unittest.TestCase):
    def test_date_basket_projection_and_alarm_dispatch(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.book = DeskBook(now=lambda: 1000)
        for feed in ("O", "R"):
            app.book.ingest(feed, 11, {})
            app.book.subscriptions[feed] = "STARTED"
        for seq, day, basket in (
            (1, 20260101, "A"),
            (2, 20251231, "B"),
            (3, 20251230, "A"),
            (4, None, "A"),
        ):
            app.book.ingest(
                "O",
                6,
                {
                    **ORDER,
                    "EMSX_SEQUENCE": seq,
                    "EMSX_DATE": day,
                    "EMSX_TIME": 3600 + seq,
                    "EMSX_BASKET_NAME": basket,
                    "EMSX_NOTES": "MOC",
                    "EMSX_STATUS": "NEW",
                },
            )
            app.book.orders[seq].acknowledged_at = 0
        for name, value in {
            "env": "PROD",
            "date_filter": "오늘·전일",
            "order_sort": "최신순",
            "search": "",
            "account_filter": "",
            "broker_filter": "",
            "basket_filter": "",
            "only_alerts": False,
            "sound_enabled": True,
            "unrouted_min": "1",
            "nofill_min": "1",
            "environment_header": "",
            "counts": "",
            "feed_state": "",
            "status": "",
            "arm_blocker": "",
            "alert_summary": "",
        }.items():
            setattr(app, name, Variable(value))
        app.root = Mock()
        app.worker = SimpleNamespace(is_alive=lambda: True)
        app.status_badge = Mock()
        app.alert_badge = Mock()
        app.arm_btn = Mock()
        app.disarm_btn = Mock()
        app.armed = False
        app.monitoring = True
        app.admin_config = default_config()
        app.ack_states = {}
        app.ack_tree = Tree()
        app.alert_tracker = AlertTracker(now=lambda: 1000)
        app.log_text = Mock()
        app._append = Mock()
        app.tabs = {
            name: {
                "tree": Tree(),
                "columns": ["create_time", "basket_name", "instruction"],
            }
            for name in ("Orders", "Routes", "Brokers")
        }
        with (
            patch("order_fields.desk_today", return_value=TODAY),
            patch("emsx_desk_monitor.play_alarm") as sound,
        ):
            app._render()
            self.assertEqual(set(app.tabs["Orders"]["tree"].rows), {"1", "2"})
            self.assertEqual(app.tabs["Orders"]["tree"].rows["1"]["values"][-1], "MOC")
            sound.assert_called_once()
            app.basket_filter.set("a")
            app._render()
            self.assertEqual(set(app.tabs["Orders"]["tree"].rows), {"1"})
            app.date_filter.set("오늘")
            app.basket_filter.set("")
            app._render()
            self.assertEqual(set(app.tabs["Orders"]["tree"].rows), {"1"})
            self.assertIn("Date Missing 1", app.counts.get())
            self.assertNotIn("정동화", app.environment_header.get())
            app.monitoring = False
            app.alert_tracker.last_sound = None
            app._render()
            sound.assert_called_once()
