"""실제 Tk 표에서 고정 열·행 정렬·선택·CSV·탭 구성을 검증한다."""

from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import sys
import tkinter as tk
from typing import Any
import unittest
from unittest.mock import Mock, patch

from desk_tables import PinnedTreeview, ROUTE_COLUMNS
from emsx_desk_monitor import MonitorApp
from kb_theme import apply_theme


class TestPinnedTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == "win32":
                raise
            raise unittest.SkipTest("그래픽 표시 서버를 사용할 수 없습니다") from exc
        cls.root.title("거래 표 배치 검증")
        cls.root.geometry("1000x450-10000-10000")
        cls.callback_errors: list[BaseException] = []
        cls.root.report_callback_exception = lambda kind, error, trace: (
            cls.callback_errors.append(error)
        )
        apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.callback_errors.clear()
        self.table = PinnedTreeview(self.root, ROUTE_COLUMNS)
        self.table.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self) -> None:
        self.table.destroy()
        self.root.update()
        self.assertFalse(self.callback_errors)

    def row(self, number: int, **kwargs: Any) -> tuple[Any, ...]:
        values = {
            "account": "KB_ACCOUNT_12345678",
            "ticker": "000001 CH Equity",
            "broker": "TEST",
            "qty": 100,
            "filled": 0,
            "remain": 100,
            "line_status": "Working",
            "adv_pct": "12%",
            "route_id": number + 1,
            **kwargs,
        }
        return tuple(values.get(key, "") for key in self.table.column_keys)

    def fill(self, count: int) -> None:
        for index in range(count):
            self.table.insert("", "end", str(index), values=self.row(index))
        self.table.refresh_identity_widths()
        self.root.update()

    def test_identity_columns_stay_visible_at_small_and_large_window_widths(
        self,
    ) -> None:
        self.fill(1)
        for width in (1000, 1330, 1920, 1000):
            with self.subTest(width=width):
                self.root.geometry(f"{width}x450-10000-10000")
                self.root.update()
                self.table.details.xview_moveto(1.0)
                self.root.update()
                self.assertGreater(self.table.details.xview()[0], 0)
                self.assertEqual(self.table.fixed.xview(), (0.0, 1.0))
                for key in ("account", "ticker"):
                    x, _, cell_width, _ = self.table.fixed.bbox("0", key)
                    self.assertGreaterEqual(cell_width, self.table._minimum[key])
                    self.assertLessEqual(x + cell_width, self.table.fixed.winfo_width())
                    text = self.table.fixed.set("0", key)
                    self.assertLessEqual(
                        self.table._font.measure(text) + 24, cell_width
                    )

    def test_vertical_scroll_selection_and_keyboard_focus_match(self) -> None:
        self.fill(200)
        self.table.details.yview_moveto(0.5)
        self.root.update()
        self.assertEqual(self.table.fixed.yview(), self.table.details.yview())
        self.table.fixed.selection_set("100")
        self.table.fixed.focus("100")
        self.root.update()
        self.assertEqual(self.table.selection(), ("100",))
        self.assertEqual(self.table.details.focus(), "100")
        fixed_box = self.table.fixed.bbox("100", "account")
        detail_box = self.table.details.bbox("100", "broker")
        self.assertEqual((fixed_box[1], fixed_box[3]), (detail_box[1], detail_box[3]))
        self.table.details.selection_set("110")
        self.table.details.focus("110")
        self.root.update()
        self.assertEqual(self.table.fixed.selection(), ("110",))
        self.assertEqual(self.table.fixed.focus(), "110")
        self.table.fixed.yview_scroll(-10, "units")
        self.root.update()
        self.assertEqual(self.table.fixed.yview(), self.table.details.yview())

    def test_reorder_updates_tags_and_removes_rows_from_both_panes(self) -> None:
        self.fill(3)
        updated = self.row(2, account="고객 Account", line_status="Cancel")
        self.table.item("2", values=updated, tags=("terminal",))
        self.table.move("2", "", 0)
        self.table.delete("1")
        self.root.update()
        self.assertEqual(self.table.get_children(), ("2", "0"))
        self.assertEqual(self.table.fixed.get_children(), self.table.get_children())
        self.assertEqual(self.table.item("2", "values"), updated)
        for tree in (self.table.fixed, self.table.details):
            self.assertEqual(tree.item("2", "tags"), ("terminal",))
        self.assertNotIn("1", self.table._values)
        self.assertEqual(self.table.item("2")["values"], updated)

    def test_long_values_expand_with_a_bound_and_tooltip_is_cleaned_up(self) -> None:
        self.root.geometry("1000x450-10000-10000")
        self.table.insert(
            "", "end", "0", values=self.row(0, account="A" * 120, ticker="T" * 120)
        )
        self.table.refresh_identity_widths()
        self.root.update()
        self.assertGreaterEqual(self.table.details.winfo_width(), 290)
        self.assertGreater(self.table.fixed.column("account", "width"), 180)
        self.table._show_full_value("A" * 120)
        popup = self.table._tooltip
        self.assertTrue(popup.winfo_exists())
        self.table._hover = ("0", "#1")
        self.table.delete("0")
        self.assertIsNone(self.table._tooltip)
        self.assertFalse(popup.winfo_exists())
        self.table.refresh_identity_widths()
        self.assertEqual(
            self.table.fixed.column("account", "width"), self.table._minimum["account"]
        )

    def test_scrollbar_hides_when_details_fit_and_pending_tooltip_is_cancelled(
        self,
    ) -> None:
        self.assertTrue(self.table.scroll_x.winfo_ismapped())
        self.table.destroy()
        self.table = PinnedTreeview(
            self.root,
            (
                ("account", "Account", 180),
                ("ticker", "Ticker", 220),
                ("broker", "Broker", 80),
            ),
        )
        self.table.pack(fill="both", expand=True)
        self.root.update()
        self.assertFalse(self.table.scroll_x.winfo_ismapped())
        self.table._hover_timer = self.table.after(50000, lambda: None)
        timer = self.table._hover_timer
        self.table.destroy()
        self.assertNotIn(timer, self.root.tk.call("after", "info"))

    def test_csv_includes_identity_and_all_details_after_horizontal_scroll(
        self,
    ) -> None:
        self.fill(1)
        self.table.heading("adv_pct", text="ADV (5D)")
        self.table.details.xview_moveto(1.0)
        app = MonitorApp.__new__(MonitorApp)
        app.nb = Mock()
        app.nb.tab.return_value = "Routes"
        app.tabs = {
            "Routes": {"tree": self.table, "columns": list(self.table.column_keys)}
        }
        app._append = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "보고서.csv")
            with (
                patch("emsx_desk_monitor.messagebox.askokcancel", return_value=True),
                patch(
                    "emsx_desk_monitor.filedialog.asksaveasfilename", return_value=path
                ),
                patch("emsx_desk_monitor.messagebox.showinfo"),
            ):
                app.export()
            with open(path, encoding="utf-8-sig", newline="") as source:
                rows = list(csv.reader(source))
            self.assertEqual(rows[0][:2], ["account", "ticker"])
            self.assertIn("adv_5d_pct", rows[0])
            self.assertEqual(rows[1], [str(cell) for cell in self.row(0)])


class TestMonitorTabs(unittest.TestCase):
    def test_routes_is_the_initial_tab_and_order_tab_is_not_created(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == "win32":
                raise
            raise unittest.SkipTest("그래픽 표시 서버를 사용할 수 없습니다") from exc
        try:
            root.geometry("1000x900-10000-10000")
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                with (
                    patch("emsx_desk_monitor.APP_DIR", directory),
                    patch("emsx_desk_monitor.ADMIN_CONFIG", directory / "admin.json"),
                    patch("desk_utilities.DeskJournal.start"),
                    patch.object(MonitorApp, "_load_market_calendar"),
                ):
                    app = MonitorApp(root, environment="DEMO")
                    root.update()
                    labels = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
                    self.assertEqual(
                        labels, ["Routes", "Brokers", "자동 수신확인(Ack)", "Event Log"]
                    )
                    self.assertEqual(app.nb.tab(app.nb.select(), "text"), "Routes")
                    self.assertEqual(set(app.tabs), {"Routes", "Brokers"})
                    self.assertIsInstance(app.tabs["Routes"]["tree"], PinnedTreeview)
                    self.assertIsInstance(app.ack_tree, PinnedTreeview)
                    app._closing = True
        finally:
            root.destroy()
