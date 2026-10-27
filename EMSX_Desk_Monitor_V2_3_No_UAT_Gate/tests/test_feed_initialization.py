"""초기 구독 거부와 필드 파싱 경고의 격리·진단·Ack 안전성을 검증한다."""

from __future__ import annotations

import logging
import queue
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from typing import Any

from ack_engine import AckEngine, AckLedger, default_config
from bloomberg_feed import (
    BloombergWorker,
    read_field,
)
from desk_core import DeskBook
from diagnostics import LOGGER, configure_diagnostics
from test_ack_engine import ORDER
from test_resilience import SimEvent, SimMessage


class Element:
    """BLPAPI의 스칼라·중첩 요소 표면을 모사한다."""

    def __init__(self, value: Any, broken: bool = False) -> None:
        self.value = value
        self.broken = broken

    def isNull(self) -> bool:
        return self.value is None

    def getValue(self) -> Any:
        if self.broken:
            raise TypeError("모의 스칼라 변환 실패")
        return self.value

    def hasElement(self, field: str) -> bool:
        return field in self.value

    def getElement(self, field: str) -> Element:
        return Element(self.value[field])

    def getElementAsString(self, field: str) -> str:
        return str(self.value[field])


class Message(SimMessage):
    def __init__(
        self, fields: dict, cid: int = 901, name: str = "모의 데이터", broken: str = ""
    ) -> None:
        super().__init__(name, fields, cid)
        self.broken = broken

    def getElement(self, field: str) -> Element:
        return Element(self.fields[field], broken=field == self.broken)

    def getElementAsString(self, field: str) -> str:
        if field == self.broken:
            raise TypeError("모의 문자열 변환 실패")
        return str(self.fields[field])


class TestFieldParsing(unittest.TestCase):
    def test_missing_and_explicit_null_are_normal_partial_updates(self) -> None:
        warning = Mock()
        self.assertIsNone(read_field(Message({}), "EMSX_NOTES", warning))
        self.assertIsNone(
            read_field(Message({"EMSX_NOTES": None}), "EMSX_NOTES", warning)
        )
        warning.assert_not_called()

    def test_native_date_time_and_numeric_strings(self) -> None:
        msg = Message(
            {
                "EMSX_DATE": date(2026, 10, 27),
                "EMSX_TIME": time(12, 30, 1),
                "EMSX_AMOUNT": "5000",
                "EMSX_AVG_PRICE": "123.5",
            }
        )
        self.assertEqual(read_field(msg, "EMSX_DATE"), 20261027)
        self.assertEqual(read_field(msg, "EMSX_TIME"), 45001)
        self.assertEqual(read_field(msg, "EMSX_AMOUNT"), 5000)
        self.assertEqual(read_field(msg, "EMSX_AVG_PRICE"), 123.5)

    def test_corrupt_numeric_does_not_truncate_and_logs_traceback(self) -> None:
        warning = Mock()
        with self.assertLogs("emsx", logging.WARNING) as captured:
            self.assertIsNone(
                read_field(Message({"EMSX_AMOUNT": 1.5}), "EMSX_AMOUNT", warning)
            )
            self.assertIsNone(
                read_field(Message({"EMSX_AVG_PRICE": float("nan")}), "EMSX_AVG_PRICE")
            )
        warning.assert_called_once_with("EMSX_AMOUNT")
        self.assertIn("EMSX_AMOUNT", "\n".join(captured.output))
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_full_subscription_reason_survives_long_prefix(self) -> None:
        worker = BloombergWorker("PROD", "HDGB", queue.Queue())
        description = (
            "초기 요청 실패 " + "원인 확인 " * 100 + "Invalid field EMSX_BASKET_NAME"
        )
        msg = Message(
            {
                "reason": {
                    "source": "emblpapi",
                    "category": "Error",
                    "errorCode": 11,
                    "description": description,
                }
            }
        )
        reason = worker._subscription_reason(msg)
        self.assertIn("errorCode=11", reason)
        self.assertTrue(reason.endswith("EMSX_BASKET_NAME"))
        self.assertGreater(len(reason), 250)

    def test_persistent_logger_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing = list(LOGGER.handlers)
            original_level = LOGGER.level
            try:
                path = configure_diagnostics(Path(tmp))
                self.assertEqual(configure_diagnostics(Path(tmp)), path)
                self.assertEqual(len(LOGGER.handlers), len(existing) + 1)
                LOGGER.error("모의 초기화 오류 기록")
                for handler in LOGGER.handlers:
                    handler.flush()
                self.assertIn("모의 초기화 오류 기록", path.read_text(encoding="utf-8"))
            finally:
                for handler in list(LOGGER.handlers):
                    if handler not in existing:
                        LOGGER.removeHandler(handler)
                        handler.close()
                LOGGER.setLevel(original_level)


class TestMessageIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = AckLedger(Path(self.tmp.name) / "ack.db")
        self.output = queue.Queue()
        self.worker = BloombergWorker("BETA", "HDGB", self.output)
        self.sent = Mock()
        self.engine = AckEngine(
            "BETA",
            "HDGB",
            {**default_config(), "beta_write_enabled": True},
            self.ledger,
            Mock(),
            self.sent,
        )
        self.worker.engine = self.engine
        self.worker.started = {"O", "R"}
        self.worker.snapshot_done = {"O": True, "R": True}
        self.engine.set_ready(True)
        self.engine.set_arm(True)

    def tearDown(self) -> None:
        self.engine.close()
        self.ledger.close()
        self.tmp.cleanup()

    def test_optional_parse_failure_does_not_disarm(self) -> None:
        with self.assertLogs("emsx", logging.WARNING):
            self.worker._handle_data_message(
                Message(
                    {"EVENT_STATUS": 4, "EMSX_SEQUENCE": 111, "EMSX_NOTES": "비고"},
                    cid=903,
                    broken="EMSX_NOTES",
                )
            )
        self.assertTrue(self.engine.armed)
        self.assertFalse(self.worker.feed_error)
        self.worker._handle_data_message(
            Message({**ORDER, "EVENT_STATUS": 6, "API_SEQ_NUM": 1})
        )
        self.sent.assert_called_once()

    def test_bad_mandatory_field_blocks_only_affected_order(self) -> None:
        with self.assertLogs("emsx", logging.WARNING):
            self.worker._handle_data_message(
                Message(
                    {**ORDER, "EMSX_AMOUNT": "bad", "EVENT_STATUS": 6, "API_SEQ_NUM": 1}
                )
            )
        self.assertTrue(self.engine.armed)
        self.sent.assert_not_called()
        self.worker._handle_data_message(
            Message({**ORDER, "EVENT_STATUS": 6, "API_SEQ_NUM": 2})
        )
        self.sent.assert_not_called()
        self.worker._handle_data_message(
            Message(
                {**ORDER, "EMSX_SEQUENCE": 112, "EVENT_STATUS": 6, "API_SEQ_NUM": 3}
            )
        )
        self.sent.assert_called_once()

    def test_missing_status_does_not_cause_false_sequence_gap(self) -> None:
        with self.assertLogs("emsx", logging.WARNING):
            self.worker._handle_data_message(Message({**ORDER, "API_SEQ_NUM": 1}))
        self.worker._handle_data_message(
            Message(
                {**ORDER, "EMSX_SEQUENCE": 112, "EVENT_STATUS": 6, "API_SEQ_NUM": 2}
            )
        )
        self.assertTrue(self.engine.armed)
        self.assertFalse(self.worker.feed_error)
        self.assertEqual(self.worker.seq_last["O"], 2)
        self.sent.assert_called_once()

    def test_enrichment_cannot_create_order_or_trigger_ack(self) -> None:
        self.worker._handle_data_message(
            Message(
                {
                    "EVENT_STATUS": 4,
                    "API_SEQ_NUM": 500,
                    "EMSX_SEQUENCE": 111,
                    "EMSX_NOTES": "TWAP",
                },
                cid=903,
            )
        )
        self.assertFalse(self.engine.orders)
        self.worker._handle_data_message(
            Message({**ORDER, "EVENT_STATUS": 4, "API_SEQ_NUM": 1})
        )
        self.assertEqual(self.worker.seq_last["O"], 1)
        data = [item for item in list(self.output.queue) if item["type"] == "data"]
        self.assertEqual(data[-1]["fields"]["EMSX_NOTES"], "TWAP")
        self.worker._handle_data_message(
            Message(
                {
                    "EVENT_STATUS": 7,
                    "API_SEQ_NUM": 501,
                    "EMSX_SEQUENCE": 111,
                    "EMSX_NOTES": "MOC",
                },
                cid=903,
            )
        )
        self.sent.assert_not_called()
        self.assertEqual(self.worker.seq_last["O"], 1)
        self.assertTrue(self.engine.armed)

    def test_invalid_core_identity_is_rejected_without_projection_crash(self) -> None:
        book = DeskBook()
        with self.assertLogs("emsx", logging.WARNING):
            book.ingest("O", 4, {"EMSX_SEQUENCE": float("inf")})
            book.ingest("R", 4, {"EMSX_SEQUENCE": 1, "EMSX_ROUTE_ID": float("nan")})
            book.ingest("O", 4, None)
        self.assertFalse(book.orders)
        self.assertFalse(book.routes)
        book.ingest("O", 4, {"EMSX_SEQUENCE": 2, "EMSX_NOTES": None})
        self.assertEqual(len(book.full_view()), 1)


class TestInitializationIntegration(unittest.TestCase):
    def test_optional_subscription_rejection_and_slow_warning_keep_core_ready(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = queue.Queue()
            worker = BloombergWorker(
                "BETA",
                "HDGB",
                output,
                config={**default_config(), "beta_write_enabled": True},
                ledger_path=Path(tmp) / "ack.db",
            )
            events = iter(
                [
                    SimEvent(
                        2,
                        [
                            Message({}, 901, "SubscriptionStarted"),
                            Message({}, 902, "SubscriptionStarted"),
                        ],
                    ),
                    SimEvent(
                        1,
                        [
                            Message({"EVENT_STATUS": 11}, 901),
                            Message({"EVENT_STATUS": 11}, 902),
                        ],
                    ),
                    SimEvent(
                        2,
                        [
                            Message(
                                {
                                    "reason": {
                                        "errorCode": 11,
                                        "description": "Invalid field EMSX_BASKET_NAME",
                                    }
                                },
                                904,
                                "SubscriptionFailure",
                            )
                        ],
                    ),
                    SimEvent(7, [Message({}, 901, "SlowConsumerWarning")]),
                    SimEvent(
                        1,
                        [Message({**ORDER, "EVENT_STATUS": 4, "API_SEQ_NUM": 1}, 901)],
                    ),
                ]
            )
            topics = []
            session = SimpleNamespace(
                start=lambda: True,
                openService=lambda name: True,
                getService=lambda name: object(),
                subscribe=lambda subs: None,
                stop=lambda: None,
            )

            def next_event(timeout: int) -> Any:
                if worker._ready() and not worker.engine.armed:
                    worker.engine.set_arm(True)
                try:
                    return next(events)
                except StopIteration:
                    self.assertTrue(worker._ready())
                    self.assertFalse(worker.feed_error)
                    self.assertFalse(worker.engine.error)
                    self.assertTrue(worker.engine.armed)
                    worker.stop()
                    return SimEvent(0, [])

            session.nextEvent = next_event
            options = SimpleNamespace(
                setConnectTimeout=lambda value: None,
                setServerHost=lambda host: None,
                setServerPort=lambda port: None,
            )
            api = SimpleNamespace(
                SessionOptions=lambda: options,
                Session=lambda opts: session,
                Event=SimpleNamespace(
                    SUBSCRIPTION_DATA=1,
                    SUBSCRIPTION_STATUS=2,
                    RESPONSE=3,
                    PARTIAL_RESPONSE=4,
                    SESSION_STATUS=5,
                    SERVICE_STATUS=6,
                    ADMIN=7,
                ),
                CorrelationId=lambda cid: cid,
                SubscriptionList=lambda: SimpleNamespace(
                    add=lambda **kw: topics.append(kw)
                ),
            )
            try:
                with self.assertLogs("emsx", logging.WARNING):
                    worker._run_session(api)
                self.assertEqual(len(topics), 5)
                self.assertNotIn("EMSX_BASKET_NAME", topics[0]["topic"])
                self.assertIn("EMSX_DATE", topics[0]["topic"])
                self.assertEqual(worker.optional_disabled, {"EMSX_BASKET_NAME"})
                types = [item["type"] for item in list(output.queue)]
                self.assertNotIn("error", types)
                self.assertIn("warning", types)
            finally:
                worker._cleanup_session()

    def test_core_rejection_is_critical_and_next_connection_uses_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = queue.Queue()
            worker = BloombergWorker(
                "BETA",
                "HDGB",
                output,
                config=default_config(),
                ledger_path=Path(tmp) / "ack.db",
            )
            events = [
                SimEvent(
                    2,
                    [
                        Message({}, 901, "SubscriptionStarted"),
                        Message({}, 902, "SubscriptionStarted"),
                    ],
                ),
                SimEvent(
                    1,
                    [
                        Message({"EVENT_STATUS": 11}, 901),
                        Message({"EVENT_STATUS": 11}, 902),
                    ],
                ),
                SimEvent(
                    2,
                    [
                        Message(
                            {
                                "reason": {
                                    "errorCode": 11,
                                    "description": "기본 구독 종료 원문",
                                }
                            },
                            901,
                            "SubscriptionTerminated",
                        )
                    ],
                ),
            ]
            session = Mock()
            session.start.return_value = True
            session.openService.return_value = True
            session.nextEvent.side_effect = events
            api = SimpleNamespace(
                SessionOptions=Mock,
                Session=lambda opts: session,
                SubscriptionList=Mock,
                CorrelationId=lambda cid: cid,
                Event=SimpleNamespace(
                    SUBSCRIPTION_DATA=1,
                    SUBSCRIPTION_STATUS=2,
                    RESPONSE=3,
                    PARTIAL_RESPONSE=4,
                    SESSION_STATUS=5,
                    SERVICE_STATUS=6,
                    ADMIN=7,
                ),
            )
            try:
                with self.assertLogs("emsx", logging.WARNING):
                    worker._run_session(api)
                self.assertTrue(worker.feed_error)
                self.assertFalse(worker.engine.armed)
                self.assertFalse(worker.enrichment_enabled)
                errors = [
                    item for item in list(output.queue) if item["type"] == "error"
                ]
                self.assertIn("기본 구독 종료 원문", errors[-1]["message"])
                self.assertIn("errorCode=11", errors[-1]["message"])
            finally:
                worker._cleanup_session()
