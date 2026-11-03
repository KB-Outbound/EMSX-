"""거래 표의 화면 점유율·기본 열·요약 상태·보조 창을 실제 Tk로 검증한다."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest import TestCase, SkipTest
from unittest.mock import patch

from desk_layout import DeskViews
from emsx_desk_monitor import MonitorApp
from kb_theme import GOLD, TEXT
from order_fields import desk_today


class TestCompactLayout(TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == "win32":
                raise
            raise SkipTest("그래픽 표시 서버를 사용할 수 없습니다") from exc
        self.errors: list[BaseException] = []
        self.root.report_callback_exception = lambda kind, error, trace: (
            self.errors.append(error)
        )
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name)
        with (
            patch("emsx_desk_monitor.APP_DIR", path),
            patch("emsx_desk_monitor.ADMIN_CONFIG", path / "admin.json"),
            patch("desk_utilities.DeskJournal.start"),
            patch.object(MonitorApp, "_load_market_calendar"),
            patch.object(MonitorApp, "_pump"),
        ):
            self.app = MonitorApp(self.root)
        loader = patch.object(self.app, "_load_market_calendar")
        loader.start()
        self.addCleanup(loader.stop)
        self.root.geometry("1000x800-10000-10000")
        self.root.update()

    def tearDown(self) -> None:
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.directory.cleanup()
        self.assertFalse(self.errors)

    def seed_route(self) -> str:
        self.app.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ACCOUNT": "KB_FUND_01",
                "EMSX_TICKER": "7203 JP Equity",
                "EMSX_BASKET_NAME": "ASIA_GROWTH",
                "EMSX_SIDE": "BUY",
                "EMSX_AMOUNT": 1000,
                "EMSX_FILLED": 100,
                "EMSX_NOTES": "TWAP",
                "EMSX_STATUS": "PARTFILL",
                "EMSX_DATE": int(desk_today().strftime("%Y%m%d")),
            },
        )
        self.app.book.ingest(
            "R",
            6,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_AMOUNT": 1000,
                "EMSX_FILLED": 100,
                "EMSX_STATUS": "PARTFILL",
                "EMSX_BROKER": "JEFF",
            },
        )
        self.app._render()
        self.root.update()
        return "1:1"

    def test_single_status_line_and_table_occupy_the_main_window(self) -> None:
        self.assertIsInstance(self.app.nb, DeskViews)
        self.assertEqual(
            self.app.environment_header.get(),
            "PROD | KB증권 해외주식영업부 EMSX 모니터",
        )
        self.assertGreater(self.app.nb.winfo_height(), self.root.winfo_height() * 0.70)
        self.assertFalse(self.app.settings_window.winfo_viewable())
        self.assertIsNone(self.app.market_window)
        self.assertFalse(self.app.market_badges)
        self.assertEqual(self.app.view_name.get(), "Routes")
        # Cancel은 Route가 없어도 Unrouted로 집계하지 않는다.
        self.app.book.ingest(
            "O",
            6,
            {
                "EMSX_SEQUENCE": 2,
                "EMSX_STATUS": "CANCEL",
                "EMSX_AMOUNT": 100,
                "EMSX_DATE": int(desk_today().strftime("%Y%m%d")),
            },
        )
        self.app._render()
        self.assertIn("Unrouted 0", self.app.visible_counts.get())

    def test_primary_fields_fit_without_horizontal_scrolling(self) -> None:
        iid = self.seed_route()
        table = self.app.tabs["Routes"]["tree"]
        for width in (1000, 1330, 1000):
            with self.subTest(width=width):
                self.root.geometry(f"{width}x800-10000-10000")
                self.root.update()
                self.assertEqual(table.details.xview(), (0.0, 1.0))
                self.assertEqual(table.fixed.xview(), (0.0, 1.0))
                for key in (
                    "basket_name",
                    "side",
                    "qty",
                    "instruction",
                    "line_status",
                    "adv_pct",
                ):
                    x, _, cell_width, _ = table.details.bbox(iid, key)
                    self.assertLessEqual(x + cell_width, table.details.winfo_width())

    def test_wider_window_adds_fill_fields_and_detail_mode_retains_data(self) -> None:
        iid = self.seed_route()
        table = self.app.tabs["Routes"]["tree"]
        before = table.item(iid, "values")
        self.assertNotIn("filled", table.details["displaycolumns"])
        self.root.geometry("1330x800-10000-10000")
        self.root.update()
        self.assertIn("filled", table.details["displaycolumns"])
        self.assertIn("remain", table.details["displaycolumns"])
        self.app.detail_columns.set(True)
        self.app._set_detail_columns()
        self.root.update()
        self.assertIn("market_warning", table.details["displaycolumns"])
        self.assertLess(table.details.xview()[1], 1.0)
        self.assertEqual(table.item(iid, "values"), before)
        self.app.detail_columns.set(False)
        self.app._set_detail_columns()
        self.root.update()
        self.assertEqual(table.details.xview(), (0.0, 1.0))

    def test_market_status_and_settings_are_available_on_demand(self) -> None:
        table_height = self.app.nb.winfo_height()
        self.app._show_market_status()
        self.app.market_window.geometry("860x360-10000-10000")
        self.root.update()
        self.assertEqual(len(self.app.market_badges), 26)
        self.assertTrue(self.app.market_window.winfo_viewable())
        self.assertEqual(self.app.market_summary.get(), "시장 확인 필요")
        self.app.market_window.withdraw()
        self.app._show_settings()
        self.app.settings_window.geometry("500x500-10000-10000")
        self.root.update()
        self.assertTrue(self.app.settings_window.winfo_viewable())
        self.assertEqual(self.app.nb.winfo_height(), table_height)
        self.app.env_ctrl.set("BETA")
        self.app._render()
        self.assertTrue(self.app.environment_header.get().startswith("BETA |"))

    def test_view_menu_replaces_tab_bar_and_preserves_selected_route(self) -> None:
        iid = self.seed_route()
        table = self.app.tabs["Routes"]["tree"]
        table.fixed.selection_set(iid)
        self.root.update()
        pages = {self.app.nb.tab(page, "text"): page for page in self.app.nb.tabs()}
        for label in ("Brokers", "자동 수신확인(Ack)", "Event Log", "Routes"):
            self.app.nb.select(pages[label])
            self.root.update()
            self.assertEqual(self.app.nb.tab(self.app.nb.select(), "text"), label)
        self.assertEqual(table.selection(), (iid,))

    def test_single_default_on_toggle_can_be_turned_off_while_waiting(
        self,
    ) -> None:
        self.assertIsInstance(self.app.arm_btn, ttk.Checkbutton)
        self.assertTrue(self.app.auto_ack_enabled.get())
        self.assertFalse(hasattr(self.app, "disarm_btn"))
        self.app.armed = True
        self.app._render()
        self.assertEqual(str(self.app.arm_btn.cget("state")), "normal")
        self.assertEqual(self.app.arm_btn.cget("text"), "자동 Ack ON")
        self.app.arm_btn.invoke()
        self.assertFalse(self.app.auto_ack_enabled.get())
        self.assertFalse(self.app.armed)
        self.app.armed = False
        self.app.monitoring = True
        self.app.worker = SimpleNamespace(is_alive=lambda: True)
        self.app.auto_ack_enabled.set(True)
        self.app.arm_state.set("활성화 요청 중 · 응답 대기")
        self.app._render()
        self.assertEqual(str(self.app.arm_btn.cget("state")), "normal")
        self.app.arm_btn.invoke()
        self.assertEqual(
            self.app.commands.get_nowait(), {"action": "arm", "armed": False}
        )
        self.app.monitoring = False
        self.app._render()
        self.assertEqual(self.app.arm_btn.cget("text"), "자동 Ack OFF")

    def test_unrouted_identity_is_replaced_when_the_first_route_arrives(self) -> None:
        iid = self.seed_route()
        self.app.book.ingest("R", 8, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": 1})
        self.app.book.ingest("O", 7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "PENDING"})
        self.app._render()
        table = self.app.tabs["Routes"]["tree"]
        self.assertEqual(table.get_children(), ("1:0",))
        self.assertIn("KB_FUND_01", table.item("1:0", "values"))
        self.assertIn("7203 JP Equity", table.item("1:0", "values"))
        self.assertIn("Pending", table.item("1:0", "values"))
        self.assertIn("Route 0", self.app.visible_counts.get())
        self.assertEqual(self.app.tabs["Brokers"]["tree"].get_children(), ())
        self.seed_route()
        self.assertEqual(table.get_children(), (iid,))

    def test_toggle_matches_latest_worker_response_after_rapid_off_on(self) -> None:
        self.app.monitoring = True
        self.app.worker = SimpleNamespace(is_alive=lambda: True)
        self.app.messages.put({"type": "ack_armed", "armed": False})
        self.app.messages.put({"type": "ack_armed", "armed": True})
        # 상태 반영 검사는 PC 속도와 분리하고 시간 기한은 별도 검사한다.
        with patch("emsx_desk_monitor.time.monotonic", return_value=0.0):
            self.app._pump()
        self.assertTrue(self.app.armed)
        self.assertTrue(self.app.auto_ack_enabled.get())
        self.assertEqual(self.app.arm_btn.cget("text"), "자동 Ack ON")
        self.app.messages.put({"type": "ack_armed", "armed": False})
        with patch("emsx_desk_monitor.time.monotonic", return_value=0.0):
            self.app._pump()
        self.assertFalse(self.app.armed)
        self.assertFalse(self.app.auto_ack_enabled.get())
        self.assertEqual(self.app.arm_btn.cget("text"), "자동 Ack OFF")

    def test_expired_ui_budget_defers_responses_and_preserves_toggle_order(
        self,
    ) -> None:
        self.app.monitoring = True
        self.app.worker = SimpleNamespace(is_alive=lambda: True)
        self.app.messages.put({"type": "ack_armed", "armed": False})
        self.app.messages.put({"type": "ack_armed", "armed": True})
        clock = [0.0]
        poll = self.app._poll_utilities

        def slow_poll() -> None:
            poll()
            clock[0] += 0.025

        def record_event(*args: object, **kwargs: object) -> bool:
            clock[0] += 0.021
            return True

        with patch("emsx_desk_monitor.time.monotonic", side_effect=lambda: clock[0]):
            # 보조 작업이 20ms 기한을 쓰면 응답은 소비하지 않고 다음 주기로 넘긴다.
            with patch.object(self.app, "_poll_utilities", side_effect=slow_poll):
                self.app._pump()
            self.assertEqual(self.app.messages.qsize(), 2)
            self.assertFalse(self.app.armed)

            # 응답 한 건마다 기한을 넘겨도 OFF → ON 순서와 최종 표시를 보존한다.
            with patch.object(self.app, "_journal_event", side_effect=record_event):
                self.app._pump()
                self.assertEqual(self.app.messages.qsize(), 1)
                self.assertFalse(self.app.armed)
                self.assertFalse(self.app.auto_ack_enabled.get())
                self.app._pump()
            self.assertTrue(self.app.messages.empty())
            self.assertTrue(self.app.armed)
            self.assertTrue(self.app.auto_ack_enabled.get())
            self.assertEqual(self.app.arm_btn.cget("text"), "자동 Ack ON")

    def test_broker_currency_and_notional_are_visible_and_missing_price_is_explicit(
        self,
    ) -> None:
        self.seed_route()
        self.app.book.ingest(
            "O",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_CURRENCY": "USD",
                "EMSX_LIMIT_PRICE": 200,
            },
        )
        self.app.book.ingest(
            "R",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_AVG_PRICE": 199.5,
            },
        )
        self.app._render()
        tree = self.app.tabs["Brokers"]["tree"]
        iid = tree.get_children()[0]
        self.assertEqual(tree.set(iid, "currency"), "USD")
        self.assertEqual(tree.set(iid, "notional_value"), "199,950.00")
        self.assertEqual(tree.set(iid, "notional_missing"), "0")
        self.app.book.ingest(
            "R",
            7,
            {
                "EMSX_SEQUENCE": 1,
                "EMSX_ROUTE_ID": 1,
                "EMSX_LIMIT_PRICE": 0,
            },
        )
        self.app._render()
        self.assertEqual(tree.set(iid, "notional_value"), "—")
        self.assertEqual(tree.set(iid, "notional_missing"), "1")

    def test_journal_error_is_visible_and_connectivity_is_not_shown_as_live(
        self,
    ) -> None:
        self.app.journal.error = "모의 기록 오류"
        self.app._render()
        self.root.update()
        self.assertEqual(self.app.operation_notice.get(), "일지 기록 오류")
        self.assertTrue(self.app.notice_button.winfo_viewable())
        self.app.book.stream_error = "모의 연결 오류"
        self.app._render()
        self.assertEqual(self.app.connection_state.get(), "● 수신 오류")

    def test_default_grid_and_normal_status_do_not_use_brand_gold(self) -> None:
        self.seed_route()
        table = self.app.tabs["Routes"]["tree"]
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("Treeview.Heading", "foreground"), TEXT)
        self.assertNotEqual(style.lookup("Badge.TLabel", "foreground"), GOLD)
        self.assertEqual(str(table.fixed.tag_configure("working", "foreground")), TEXT)
        self.assertEqual(style.lookup("Primary.TButton", "background"), GOLD)
        self.assertEqual(style.lookup("Risk.Badge.TLabel", "foreground"), GOLD)

    def test_report_action_opens_export_dialog_without_changing_monitoring(
        self,
    ) -> None:
        with patch(
            "desk_utilities.filedialog.asksaveasfilename", return_value=""
        ) as save:
            self.app.report_button.invoke()
            save.assert_called_once()
        self.assertFalse(self.app.monitoring)
        self.assertFalse(self.app.armed)
