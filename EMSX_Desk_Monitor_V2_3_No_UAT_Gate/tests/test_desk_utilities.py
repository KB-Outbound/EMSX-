"""화면 없이 시장 배지·일괄 내보내기·완료 알림의 UI 연결을 검증한다."""

from datetime import datetime
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from alerting import AlertTracker
from desk_core import DeskBook, line_status
from emsx_desk_monitor import MonitorApp
from kb_theme import row_tags
from market_sessions import MarketCalendar
from order_fields import desk_today
from test_trade_features import Variable


class TestDeskUtilities(unittest.TestCase):
    def test_report_popup_is_deferred_so_feed_pump_can_reschedule(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.utility_messages = queue.Queue()
        app.utility_messages.put(
            {
                "type": "report_done",
                "path": "일지.xlsx",
                "events": 10,
                "audits": 2,
                "warnings": [],
            }
        )
        app._closing = False
        app.root = Mock()
        app.report_button = Mock()
        app._append = Mock()
        app._update_market_panel = Mock()
        with patch("desk_utilities.messagebox.showinfo") as popup:
            app._poll_utilities()
            popup.assert_not_called()
            app.root.after_idle.assert_called_once()
            app.root.after_idle.call_args.args[0]()
            popup.assert_called_once()
        app.report_button.configure.assert_called_once_with(state="normal")

    def test_calendar_error_removes_stale_open_states(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.utility_messages = queue.Queue()
        app.utility_messages.put(
            {"type": "market_error", "message": "손상된 로컬 지정"}
        )
        app.market_calendar = object()
        app.market_statuses = {"US": object()}
        app._market_tick = 123.0
        app._append = Mock()
        app._update_market_panel = Mock()
        app._poll_utilities()
        self.assertIsNone(app.market_calendar)
        self.assertFalse(app.market_statuses)
        self.assertEqual(app._market_tick, 0.0)

    def test_export_snapshot_ignores_screen_search_filters(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        with tempfile.TemporaryDirectory() as tmp:
            app.operations_directory = Path(tmp)
            app.env, app.team = Variable("PROD"), Variable("HDGB")
            app.search = Variable("화면에 표시되지 않는 검색어")
            app.date_filter = Variable("오늘")
            app._thresholds = Mock(return_value=(5, 15))
            app._update_market_panel = Mock()
            app.market_calendar = None
            app.market_statuses = {}
            app.journal = SimpleNamespace(
                error="", request_report=Mock(return_value=True)
            )
            app.report_button = Mock()
            app._append = Mock()
            app.book = DeskBook()
            for seq in (1, 2):
                app.book.ingest(
                    "O",
                    4,
                    {
                        "EMSX_SEQUENCE": seq,
                        "EMSX_DATE": int(desk_today().strftime("%Y%m%d")),
                        "EMSX_TICKER": "TEST JP Equity",
                        "EMSX_STATUS": "NEW",
                    },
                )
            path = str(Path(tmp) / "일지.xlsx")
            with patch(
                "desk_utilities.filedialog.asksaveasfilename", return_value=path
            ):
                app.export_daily_report()
            saved = app.journal.request_report.call_args.args[0]
            self.assertEqual({row["seq"] for row in saved.orders}, {1, 2})
            self.assertEqual(saved.day, str(desk_today()))
            self.assertEqual(saved.environment, "PROD")
            app.report_button.configure.assert_called_once_with(state="disabled")

    def test_alert_transitions_are_recorded_once_and_clear_is_not_claimed_as_fill(
        self,
    ) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app._seen_operational_alerts = {}
        app._closing = False
        app._journal_event = Mock()
        row = {
            "seq": 1,
            "account": "TEST",
            "ticker": "TEST JP Equity",
            "instruction": "TWAP",
            "iso_country": "JP",
            "alert_types": ("No-Fill Alert",),
        }
        app._track_operational_events([row])
        app._track_operational_events([row])
        self.assertEqual(app._journal_event.call_count, 1)
        app._track_operational_events([{**row, "alert_types": ()}])
        self.assertEqual(app._journal_event.call_args.args[0], "Alert 표시 해제")
        self.assertEqual(app._journal_event.call_count, 2)

    def test_holiday_advisory_does_not_enter_existing_audio_alarm(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.market_calendar = MarketCalendar.load()
        app.market_statuses = {
            "JP": app.market_calendar.status(
                "JP", datetime.fromisoformat("2026-01-01T01:00:00+00:00")
            )
        }
        row = {
            "seq": 1,
            "ticker": "TEST JP Equity",
            "iso_country": "JP",
            "status": "WORKING",
            "alert_types": (),
            "warning": "",
        }
        app._annotate_market_rows([row], [])
        self.assertIn("휴장일", row["market_warning"])
        app.alert_summary = Variable("")
        app.alert_badge = Mock()
        app.sound_enabled = Variable(True)
        app.monitoring = True
        app.worker = SimpleNamespace(is_alive=lambda: True)
        app.book = SimpleNamespace(stream_healthy=lambda: True)
        app.alert_tracker = AlertTracker()
        app._test_alarm = Mock()
        app._update_alarms([row])
        app._test_alarm.assert_not_called()
        filled = {**row, "qty": 100, "remain": 0}
        app._annotate_market_rows([filled], [])
        self.assertEqual(filled["market_warning"], "")

    def test_market_annotation_preserves_execution_highlights_in_both_tables(
        self,
    ) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.market_calendar = MarketCalendar.load()
        app.market_statuses = {
            "NL": app.market_calendar.status(
                "NL", datetime.fromisoformat("2026-10-29T00:00:00+00:00")
            )
        }
        for state, warning in (
            ("PARTFILL", ""),
            ("WORKING", "No-Fill Alert"),
            ("CANCEL", ""),
            ("NEW", ""),
        ):
            for adv in (True, False):
                with self.subTest(state=state, adv=adv):
                    order = {
                        "seq": 1,
                        "ticker": "TEST NL Equity",
                        "iso_country": "NL",
                        "status": state,
                        "line_status": line_status(state),
                        "warning": warning,
                        "alert_types": (warning,) if warning else (),
                        "adv_check": adv,
                        "qty": 100,
                        "remain": 100,
                    }
                    route = {**order, "route_id": 1}
                    expected = row_tags(0, order["line_status"], bool(warning), adv)
                    before = order.copy()
                    app._annotate_market_rows([order], [route])
                    for row in (order, route):
                        self.assertEqual(row["market_warning"], "")
                        self.assertEqual(row["market_status"], "장 종료")
                        self.assertEqual(
                            row_tags(
                                0,
                                row["line_status"],
                                bool(row["warning"] or row["market_warning"]),
                                adv,
                            ),
                            expected,
                        )
                        self.assertEqual({key: row[key] for key in before}, before)

    def test_missing_calendar_clears_badge_and_retains_execution_alert(self) -> None:
        app = MonitorApp.__new__(MonitorApp)
        app.market_calendar = None
        row = {
            "seq": 1,
            "status": "WORKING",
            "iso_country": "JP",
            "warning": "No-Fill Alert",
            "alert_types": ("No-Fill Alert",),
            "market_warning": "이전 안내",
            "market_exchange": "XTKS",
        }
        app._annotate_market_rows([row], [])
        self.assertEqual(row["market_status"], "확인 필요")
        self.assertEqual(row["market_warning"], "")
        self.assertEqual(row["market_exchange"], "")
        self.assertEqual(row["alert_types"], ("No-Fill Alert",))

    def test_real_no_fill_to_partial_fill_to_cancel_keeps_market_separate(
        self,
    ) -> None:
        clock = [0.0]
        book = DeskBook(now=lambda: clock[0])
        for feed in ("O", "R"):
            book.ingest(feed, 11, {})
        book.update_reference("TEST JP Equity", {"iso_country": "JP"})
        for feed, extra in (
            ("O", {"EMSX_TICKER": "TEST JP Equity"}),
            ("R", {"EMSX_ROUTE_ID": 1}),
        ):
            book.ingest(
                feed,
                6,
                {
                    "EMSX_SEQUENCE": 1,
                    "EMSX_STATUS": "WORKING",
                    "EMSX_AMOUNT": 100,
                    "EMSX_FILLED": 0,
                    **extra,
                },
            )
        app = MonitorApp.__new__(MonitorApp)
        app.market_calendar = MarketCalendar.load()
        app.market_statuses = {
            "JP": app.market_calendar.status(
                "JP", datetime.fromisoformat("2026-10-29T09:00:00+00:00")
            )
        }
        clock[0] = 1000.0
        tracker = AlertTracker()
        for status, filled, has_alarm in (
            ("WORKING", 0, True),
            ("PARTFILL", 30, False),
            ("CANCEL", 30, False),
        ):
            for feed in ("O", "R"):
                book.ingest(
                    feed,
                    7,
                    {
                        "EMSX_SEQUENCE": 1,
                        "EMSX_ROUTE_ID": 1,
                        "EMSX_STATUS": status,
                        "EMSX_FILLED": filled,
                    },
                )
            orders, routes = book.full_view(1, 1), book.route_view()
            original = orders[0].copy()
            app._annotate_market_rows(orders, routes)
            self.assertEqual({key: orders[0][key] for key in original}, original)
            self.assertEqual(orders[0]["market_warning"], "")
            self.assertEqual(routes[0]["market_warning"], "")
            self.assertEqual(tracker.update(orders, True), has_alarm)
