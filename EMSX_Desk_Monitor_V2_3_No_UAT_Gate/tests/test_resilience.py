"""외부 접속 없이 동시성, 전송 안전성, 복구와 부하를 검증한다."""

import concurrent.futures
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ack_engine import AckEngine, AckLedger, default_config, eligibility
from bloomberg_feed import BloombergWorker
from desk_core import DeskBook
from demo_feed import DemoWorker
from test_ack_engine import ORDER


class TestSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.ledger = AckLedger(self.path / "ack.db")
        self.sent = []
        self.engine = AckEngine(
            "BETA",
            "HDGB",
            {**default_config(), "beta_write_enabled": True},
            self.ledger,
            lambda *a, **kw: None,
            lambda seq, cid: self.sent.append((seq, cid)),
            self.path / "audit.jsonl",
        )
        self.engine.set_ready(True)
        self.engine.set_arm(True)

    def tearDown(self) -> None:
        self.engine.close()
        self.ledger.close()
        self.tmp.cleanup()

    def test_concurrent_duplicate_burst(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            results = [
                pool.submit(self.engine.on_order, 6, ORDER, True, True)
                for _ in range(2000)
            ]
            for result in results:
                result.result()
        self.assertEqual(len(self.sent), 1)
        self.assertIn(
            "ACK_SENT", (self.path / "audit.jsonl").read_text(encoding="utf-8")
        )

    def test_shared_database_reserves_once(self) -> None:
        other = AckLedger(self.path / "ack.db")
        try:
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                results = list(
                    pool.map(
                        lambda i: (other if i % 2 else self.ledger).reserve(
                            "BETA", "HDGB", 999
                        ),
                        range(100),
                    )
                )
            self.assertEqual(sum(results), 1)
        finally:
            other.close()

    def test_immediate_response_not_overwritten(self) -> None:
        self.engine.send_func = lambda seq, cid: self.engine.on_response(
            cid, "SellSideAck", 0, "확인"
        )
        self.engine.on_order(6, ORDER, True, True)
        self.assertFalse(self.engine.pending)
        self.assertEqual(
            self.ledger.get("BETA", "HDGB", 111), "API_RESPONSE_OK_UNVERIFIED"
        )

    def test_audit_failure_prevents_send(self) -> None:
        self.engine.audit_path = self.path
        with self.assertRaises(OSError):
            self.engine.on_order(6, ORDER, True, True)
        self.assertFalse(self.sent)
        self.assertFalse(self.engine.armed)
        self.assertEqual(self.ledger.get("BETA", "HDGB", 111), "AUDIT_FAILED")
        self.engine.audit_path = None

    def test_timeout_is_unknown_without_retry(self) -> None:
        self.engine.on_order(6, ORDER, True, True)
        with patch("ack_engine.time.monotonic", return_value=time.monotonic() + 21):
            self.engine.check_pending()
        self.engine.on_order(6, ORDER, True, True)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.ledger.get("BETA", "HDGB", 111), "UNKNOWN")
        self.assertFalse(self.engine.armed)

    def test_disconnect_and_close_block_reuse(self) -> None:
        self.engine.on_order(6, ORDER, True, True)
        self.engine.close()
        self.assertEqual(self.ledger.get("BETA", "HDGB", 111), "UNKNOWN")
        self.engine.set_ready(True)
        self.engine.set_arm(True)
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 112}, True, True)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.engine.orders)

    def test_invalid_nonintegral_numbers(self) -> None:
        for value in (True, 1.5, float("inf"), float("nan"), "1.5"):
            for field in ("EMSX_SEQUENCE", "EMSX_AMOUNT"):
                self.assertFalse(
                    eligibility({**ORDER, field: value}, default_config())[0]
                )

    def test_tracker_limit_fails_closed(self) -> None:
        self.engine.max_tracked_orders = 1
        self.engine.on_order(4, ORDER, True, True)
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 112}, True, True)
        self.assertFalse(self.sent)
        self.assertFalse(self.engine.armed)


class TestRecovery(unittest.TestCase):
    def test_queue_overflow_fails_closed(self) -> None:
        output = queue.Queue(maxsize=1)
        worker = BloombergWorker("BETA", "HDGB", output)
        worker.queue_timeout = 0.001
        worker.emit("data")
        with self.assertRaises(RuntimeError):
            worker.emit("data")
        self.assertTrue(worker.feed_error)
        self.assertEqual(output.qsize(), 1)

    def test_reconnect_resets_health_and_exponential_wait(self) -> None:
        worker = BloombergWorker("BETA", "HDGB", queue.Queue())
        waits = []
        calls = []

        def session(api: object) -> None:
            calls.append(True)
            self.assertFalse(worker.started)
            self.assertEqual(worker.seq_last, {"O": None, "R": None})
            worker.started.add("O")
            worker.seq_last["O"] = 50
            worker.feed_error = True

        def wait(delay: float) -> bool:
            waits.append(delay)
            return len(waits) == 4

        with (
            patch.dict(sys.modules, {"blpapi": SimpleNamespace()}),
            patch.object(worker, "_run_session", side_effect=session),
            patch.object(worker.stop_event, "wait", side_effect=wait),
            patch("bloomberg_feed.random.uniform", return_value=1),
        ):
            worker.run()
        self.assertEqual(waits, [1, 2, 4, 8])
        self.assertEqual(len(calls), 4)
        self.assertIsNone(worker.engine)

    def test_stop_interrupts_reconnect_wait(self) -> None:
        worker = BloombergWorker("BETA", "HDGB", queue.Queue())
        reached = threading.Event()
        with (
            patch.dict(sys.modules, {"blpapi": SimpleNamespace()}),
            patch.object(worker, "_run_session", side_effect=lambda api: reached.set()),
        ):
            worker.start()
            self.assertTrue(reached.wait(1))
            worker.stop()
            worker.join(1)
        self.assertFalse(worker.is_alive())

    def test_demo_functional_snapshot(self) -> None:
        output = queue.Queue()
        demo = DemoWorker(output)
        book = DeskBook()
        demo.start()
        try:
            while not all(book.snapshot_done.values()):
                message = output.get(timeout=2)
                if message["type"] == "subscription":
                    book.subscriptions[message["feed"]] = message["status"]
                elif message["type"] == "data":
                    book.ingest(message["feed"], message["status"], message["fields"])
            self.assertEqual(book.counters()["orders"], 5)
            self.assertEqual(book.counters()["routes"], 3)
            self.assertTrue(book.counters()["stream_ok"])
        finally:
            demo.stop()
            demo.join(1)
        self.assertFalse(demo.is_alive())

    def test_concurrent_book_burst(self) -> None:
        book = DeskBook()

        def write() -> None:
            for i in range(10000):
                book.ingest(
                    "O",
                    7,
                    {
                        "EMSX_SEQUENCE": i % 100,
                        "EMSX_AMOUNT": 100,
                        "EMSX_FILLED": i % 101,
                    },
                )

        def read() -> None:
            for _ in range(100):
                book.full_view()
                book.route_view()
                book.counters()

        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            results = [pool.submit(write), pool.submit(read), pool.submit(read)]
            for result in results:
                result.result()
        self.assertEqual(len(book.full_view()), 100)
        self.assertLessEqual(len(book.activity), 300)


class SimMessage:
    """실제 파서가 사용하는 Bloomberg 메시지 표면을 모사한다."""

    def __init__(self, name: str, fields: dict, cid: int = 901) -> None:
        self.name = name
        self.fields = fields
        self.cid = cid

    def hasElement(self, field: str) -> bool:
        return field in self.fields

    def getElementAsInteger(self, field: str) -> int:
        return int(self.fields[field])

    def getElementAsFloat(self, field: str) -> float:
        return float(self.fields[field])

    def getElementAsString(self, field: str) -> str:
        return str(self.fields[field])

    def correlationIds(self) -> list:
        return [SimpleNamespace(value=lambda: self.cid)]

    def messageType(self) -> str:
        return self.name


class SimEvent(list):
    """종류와 메시지 순서를 보존하는 테스트 이벤트다."""

    def __init__(self, kind: int, messages: list) -> None:
        super().__init__(messages)
        self.kind = kind

    def eventType(self) -> int:
        return self.kind


class TestSessionIntegration(unittest.TestCase):
    def test_subscription_burst_disconnect_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = queue.Queue()
            worker = BloombergWorker(
                "BETA",
                "HDGB",
                output,
                config=default_config(),
                ledger_path=Path(tmp) / "ack.db",
            )
            kinds = SimpleNamespace(
                SUBSCRIPTION_DATA=1,
                SUBSCRIPTION_STATUS=2,
                RESPONSE=3,
                PARTIAL_RESPONSE=4,
                SESSION_STATUS=5,
                SERVICE_STATUS=6,
                ADMIN=7,
            )
            count = 5000
            events = iter(
                [
                    SimEvent(
                        2,
                        [
                            SimMessage("SubscriptionStarted", {}, 901),
                            SimMessage("SubscriptionStarted", {}, 902),
                        ],
                    ),
                    SimEvent(
                        1,
                        [
                            SimMessage(
                                "데이터",
                                {
                                    "MSG_SUB_TYPE": "O",
                                    "EVENT_STATUS": 4,
                                    "API_SEQ_NUM": i,
                                    "EMSX_SEQUENCE": i,
                                    "EMSX_AMOUNT": 100,
                                    "EMSX_STATUS": "ORD-PEND",
                                },
                            )
                            for i in range(1, count + 1)
                        ],
                    ),
                    SimEvent(
                        1,
                        [
                            SimMessage(
                                "종료", {"MSG_SUB_TYPE": feed, "EVENT_STATUS": 11}
                            )
                            for feed in ("O", "R")
                        ],
                    ),
                    SimEvent(5, [SimMessage("SessionConnectionDown", {})]),
                ]
            )
            stopped = []
            subscriptions = []
            session = SimpleNamespace(
                start=lambda: True,
                openService=lambda name: True,
                openServiceAsync=lambda name, correlationId: True,
                getService=lambda name: object(),
                subscribe=lambda subs: subscriptions.append(subs),
                nextEvent=lambda timeout: next(events),
                stop=lambda: stopped.append(True),
            )
            options = SimpleNamespace(
                setConnectTimeout=lambda value: None,
                setServerHost=lambda host: None,
                setServerPort=lambda port: None,
            )
            api = SimpleNamespace(
                SessionOptions=lambda: options,
                Session=lambda opts: session,
                Event=kinds,
                CorrelationId=lambda cid: cid,
                SubscriptionList=lambda: SimpleNamespace(add=lambda **kw: None),
            )
            try:
                with self.assertLogs("emsx", level="ERROR") as logs:
                    worker._run_session(api)
                self.assertIn("SessionConnectionDown", "\n".join(logs.output))
                self.assertNotIn("AttributeError", "\n".join(logs.output))
                self.assertTrue(worker.feed_error)
                self.assertFalse(worker.engine.armed)
                self.assertEqual(len(subscriptions), 5)
                book = DeskBook()
                while not output.empty():
                    item = output.get_nowait()
                    if item["type"] == "data":
                        book.ingest(item["feed"], item["status"], item["fields"])
                self.assertEqual(len(book.orders), count)
                self.assertFalse(book.event_gaps)
                self.assertEqual(worker.seq_last["O"], count)
            finally:
                worker._cleanup_session()
            self.assertEqual(stopped, [True])
            self.assertIsNone(worker.ledger)
