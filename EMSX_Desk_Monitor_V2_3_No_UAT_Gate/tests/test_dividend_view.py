"""배당 화면의 실제 열·위험 우선 배치·KST 전환·감사·CSV를 검증한다."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

from dividend_rules import DIVIDEND_ALERT
from emsx_desk_monitor import MonitorApp
from market_sessions import MarketCalendar


class TestDividendView(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == "win32":
                raise
            raise unittest.SkipTest("그래픽 표시 서버를 사용할 수 없습니다") from exc
        self.errors: list[BaseException] = []
        self.root.report_callback_exception = lambda kind, error, trace: (
            self.errors.append(error)
        )
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        with (
            patch("emsx_desk_monitor.APP_DIR", folder),
            patch("emsx_desk_monitor.ADMIN_CONFIG", folder / "admin.json"),
            patch("desk_utilities.DeskJournal.start"),
            patch.object(MonitorApp, "_load_market_calendar"),
            patch.object(MonitorApp, "_pump"),
        ):
            self.app = MonitorApp(self.root)
        self.app.market_calendar = MarketCalendar.load()
        self.app._journal_event = Mock(return_value=True)
        self.app._test_alarm = Mock()
        self.day = date(2026, 10, 30)
        self.day_patch = patch("order_fields.desk_today", return_value=self.day)
        self.today = self.day_patch.start()
        self.addCleanup(self.day_patch.stop)
        self.root.geometry("1000x800-10000-10000")
        self.root.update()
        self.tree = self.app.tabs["배당 확인"]["tree"]

    def tearDown(self) -> None:
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.directory.cleanup()
        self.assertFalse(self.errors)

    def order(
        self,
        sequence: int,
        ex_date: Any,
        *,
        iso: str = "US",
        instruction: str = "T+2 / TWAP",
        hour: int = 9,
        created: date | None = None,
    ) -> None:
        ticker = f"TEST{sequence} US Equity"
        self.app.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": sequence,
                "EMSX_TICKER": ticker,
                "EMSX_ACCOUNT": f"KB_FUND_{sequence}",
                "EMSX_AMOUNT": 1000,
                "EMSX_FILLED": 0,
                "EMSX_STATUS": "NEW",
                "EMSX_SIDE": "BUY",
                "EMSX_DATE": int((created or self.day).strftime("%Y%m%d")),
                "EMSX_TIME": hour * 3600,
                "EMSX_NOTES": instruction,
                "EMSX_BASKET_NAME": "US_BASKET",
            },
        )
        self.app.book.update_reference(
            ticker, {"iso_country": iso, "dividend_ex_date": ex_date}
        )

    def render(self) -> None:
        self.app._render()
        self.root.update()

    def test_tab_is_next_to_ack_and_has_exactly_eight_visible_columns(self) -> None:
        labels = [self.app.nb.tab(page, "text") for page in self.app.nb.tabs()]
        self.assertEqual(labels[labels.index("자동 수신확인(Ack)") + 1], "배당 확인")
        self.assertEqual(
            list(self.app.tab_buttons), ["Routes", "자동 수신확인(Ack)", "배당 확인"]
        )
        self.app.tab_buttons["배당 확인"].invoke()
        self.root.update()
        self.assertEqual(self.app.nb.tab(self.app.nb.select(), "text"), "배당 확인")
        headers = [self.tree.heading(key, "text") for key in self.tree["columns"]]
        self.assertEqual(
            headers,
            [
                "Create Time",
                "Account",
                "Ticker",
                "Qty",
                "ISO",
                "Instruction",
                "Basket Name",
                "Dividend Ex Date",
            ],
        )
        self.assertNotIn("seq", self.tree["columns"])
        self.assertGreater(self.tree.winfo_height(), self.root.winfo_height() * 0.70)

    def test_us_candidates_are_sorted_with_risks_above_newer_normal_orders(
        self,
    ) -> None:
        self.order(1, "2026-11-02", hour=9)
        self.order(2, "2026-11-03", hour=12)
        self.order(3, "2026-11-02", hour=8)
        self.order(4, None, hour=14)
        self.order(5, "2026-11-02", iso="JP", hour=15)
        self.order(6, "2026-11-02", instruction="TWAP", hour=16)
        self.render()
        self.assertEqual(self.tree.get_children(), ("1", "3", "4", "2"))
        self.assertEqual(self.tree.item("1", "tags"), ("dividend",))
        self.assertNotIn("dividend", self.tree.item("4", "tags"))
        self.assertEqual(self.tree.item("4", "values")[-1], "—")
        self.assertEqual(self.app.dividend_target_date, date(2026, 11, 2))
        self.app.only_alerts.set(True)
        self.app.broker_filter.set("배당 화면에 적용하지 않는 Broker 검색")
        self.render()
        self.assertEqual(self.tree.get_children(), ("1", "3"))
        self.app.only_alerts.set(False)
        self.app.account_filter.set("KB_FUND_3")
        self.render()
        self.assertEqual(self.tree.get_children(), ("3",))

    def test_kst_day_rollover_recomputes_target_instead_of_using_order_entry_day(
        self,
    ) -> None:
        self.today.return_value = date(2026, 11, 1)
        self.order(1, "2026-11-02", created=date(2026, 11, 1))
        self.render()
        self.assertEqual(self.tree.item("1", "tags"), ("dividend",))
        self.today.return_value = date(2026, 11, 2)
        self.render()
        self.assertEqual(self.tree.get_children(), ("1",))
        self.assertNotIn("dividend", self.tree.item("1", "tags"))
        self.assertEqual(self.app.dividend_target_date, date(2026, 11, 3))

    def test_reference_clearing_removes_risk_without_disarming_ack_or_sounding(
        self,
    ) -> None:
        self.order(1, "2026-11-02")
        for feed in ("O", "R"):
            self.app.book.ingest(feed, 11, {})
            self.app.book.subscriptions[feed] = "STARTED"
        self.app.worker = SimpleNamespace(is_alive=lambda: True)
        self.app.monitoring = True
        self.app.armed = True
        self.render()
        self.assertEqual(self.tree.item("1", "tags"), ("dividend",))
        self.app._journal_event.reset_mock()
        self.app.book.update_reference("TEST1 US Equity", {"dividend_ex_date": None})
        self.render()
        self.assertNotIn("dividend", self.tree.item("1", "tags"))
        self.assertTrue(self.app.armed)
        self.assertTrue(self.app.commands.empty())
        self.app._test_alarm.assert_not_called()
        calls = self.app._journal_event.call_args_list
        self.assertTrue(
            any(
                call.args[0] == "Alert 표시 해제"
                and call.args[1]["alert"] == DIVIDEND_ALERT
                for call in calls
            )
        )

    def test_risk_audit_is_deduplicated_and_contains_the_business_target(self) -> None:
        self.order(1, "2026-11-02")
        self.render()
        self.render()
        calls = [
            call
            for call in self.app._journal_event.call_args_list
            if call.args[0] == "Alert 표시 시작"
        ]
        self.assertEqual(len(calls), 1)
        payload = calls[0].args[1]
        self.assertEqual(payload["alert"], DIVIDEND_ALERT)
        self.assertEqual(payload["session_date"], "2026-10-30")
        self.assertEqual(payload["target_date"], "2026-11-02")
        self.assertEqual(payload["dividend_ex_date"], "2026-11-02")
        self.assertEqual(payload["qty"], 1000)

    def test_csv_exports_only_the_eight_dividend_columns_and_filtered_rows(
        self,
    ) -> None:
        self.order(1, "2026-11-02")
        self.order(2, "2026-11-03")
        self.app.only_alerts.set(True)
        self.render()
        self.app.tab_buttons["배당 확인"].invoke()
        path = str(Path(self.directory.name) / "배당 확인.csv")
        with (
            patch("emsx_desk_monitor.messagebox.askokcancel", return_value=True),
            patch("emsx_desk_monitor.filedialog.asksaveasfilename", return_value=path),
            patch("emsx_desk_monitor.messagebox.showinfo"),
        ):
            self.app.export()
        with open(path, encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows[0]), 8)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][-1], "2026-11-02")
        self.assertNotIn("seq", rows[0])
