"""화면 없이 시장 배지·일괄 내보내기·완료 알림의 UI 연결을 검증한다."""

from datetime import datetime
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from alerting import AlertTracker
from desk_core import DeskBook
from emsx_desk_monitor import MonitorApp
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
