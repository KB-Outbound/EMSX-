"""배치 전송의 중복 방지·감사 기록·응답 처리와 작업자 통합을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from collections import deque
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

from ack_engine import (
    ACK_BATCH_MAX_WAIT_SECONDS,
    ACK_BATCH_QUIET_SECONDS,
    ACK_MAX_INFLIGHT,
    AckEngine,
    AckLedger,
    default_config,
)
from bloomberg_feed import BloombergWorker
from order_fields import desk_today
from test_ack_engine import ORDER
from test_resilience import SimEvent, SimMessage


class TestAckBatch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.ledger = AckLedger(self.path / "ack.db")
        self.config = {**default_config(), "beta_write_enabled": True}
        self.sent: list[tuple[tuple[int, ...], int]] = []
        self.events: list[tuple[str, dict]] = []
        self.clock = patch("ack_engine.time.monotonic", return_value=100.0)
        self.now = self.clock.start()
        self.engine = AckEngine(
            "BETA",
            "HDGB",
            self.config,
            self.ledger,
            lambda typ, **kw: self.events.append((typ, kw)),
            audit_path=self.path / "audit.jsonl",
            batch_send_func=lambda seqs, cid: self.sent.append((seqs, cid)),
        )
        self.engine.set_ready(True)
        self.engine.set_arm(True)

    def tearDown(self) -> None:
        self.engine.close()
        self.ledger.close()
        self.clock.stop()
        self.tmp.cleanup()

    def enqueue(self, count: int, start: int = 1) -> None:
        for seq in range(start, start + count):
            self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": seq}, True, True)

    def flush_due(self) -> None:
        self.now.return_value += ACK_BATCH_QUIET_SECONDS + 0.001
        self.engine.flush_batch()

    def test_small_burst_uses_one_request_after_window_without_sleep(self) -> None:
        self.enqueue(12)
        self.engine.flush_batch()
        self.assertFalse(self.sent)
        self.assertGreaterEqual(self.engine.batch_timeout_ms(), 1)
        self.assertLessEqual(self.engine.batch_timeout_ms(), 50)
        self.flush_due()
        self.assertEqual(self.sent[0][0], tuple(range(1, 13)))
        self.assertEqual(len(self.engine.pending), 1)
        self.assertFalse(self.engine.queued)
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) == "SENT" for s in range(1, 13))
        )

    def test_all_500_orders_are_submitted_in_exactly_one_request(self) -> None:
        self.enqueue(500)
        self.engine.flush_batch()
        self.assertFalse(self.sent)
        self.flush_due()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], tuple(range(1, 501)))
        self.assertEqual(len(self.engine.pending), 1)
        self.assertFalse(self.engine.queued)

    def test_fragmented_events_30ms_apart_are_collected_before_submission(self) -> None:
        for seq in range(1, 7):
            self.now.return_value = 100.0 + (seq - 1) * 0.030
            self.enqueue(1, seq)
            self.engine.flush_batch()
            self.assertFalse(self.sent)
        self.flush_due()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], tuple(range(1, 7)))

    def test_continuous_arrivals_cannot_postpone_batch_indefinitely(self) -> None:
        for seq in range(1, 10):
            self.now.return_value = 100.0 + (seq - 1) * 0.030
            self.enqueue(1, seq)
            self.engine.flush_batch()
            self.assertFalse(self.sent)
        self.now.return_value = 100.0 + ACK_BATCH_MAX_WAIT_SECONDS + 0.001
        self.engine.flush_batch()
        self.assertEqual(self.sent[0][0], tuple(range(1, 10)))

    def test_later_burst_does_not_wait_for_first_response(self) -> None:
        self.enqueue(500)
        self.flush_due()
        self.enqueue(200, 501)
        self.flush_due()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual([len(seqs) for seqs, _ in self.sent], [500, 200])
        self.assertEqual(len(self.engine.pending), 2)

    def test_missing_bulk_callback_never_falls_back_to_single_sender(self) -> None:
        legacy_send = Mock()
        with self.assertLogs("emsx", "WARNING"):
            old = AckEngine(
                "BETA",
                "HDGB",
                self.config,
                self.ledger,
                Mock(),
                legacy_send,
            )
        try:
            old.set_ready(True)
            old.set_arm(True)
            for seq in range(1, 101):
                old.on_order(6, {**ORDER, "EMSX_SEQUENCE": seq}, True, True)
            self.assertFalse(legacy_send.called)
            old.flush_batch(force=True)
            self.assertFalse(legacy_send.called)
            self.assertFalse(old.armed)
            self.assertFalse(old.queued)
            self.assertTrue(
                all(self.ledger.get("BETA", "HDGB", s) is None for s in range(1, 101))
            )
        finally:
            old.close()

    def test_bulk_ui_notification_contains_all_orders_in_one_event(self) -> None:
        self.enqueue(500)
        self.events.clear()
        self.flush_due()
        self.assertEqual([kind for kind, _ in self.events], ["ack_batch"])
        message = self.events[0][1]
        self.assertEqual(message["count"], 500)
        self.assertEqual(message["sequences"], tuple(range(1, 501)))
        self.assertEqual(message["state"], "SENT")

    def test_collection_and_submission_share_one_lock(self) -> None:
        self.enqueue(10)
        sending = threading.Event()
        release = threading.Event()
        producer_started = threading.Event()
        producer_finished = threading.Event()

        def send(sequences: tuple[int, ...], cid: int) -> None:
            sending.set()
            self.assertTrue(release.wait(1))
            self.sent.append((sequences, cid))

        def produce() -> None:
            producer_started.set()
            self.enqueue(1, 11)
            producer_finished.set()

        self.engine.batch_send_func = send
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.engine.flush_batch, force=True)
            try:
                self.assertTrue(sending.wait(1))
                second = pool.submit(produce)
                self.assertTrue(producer_started.wait(1))
                self.assertFalse(producer_finished.wait(0.020))
            finally:
                release.set()
            first.result()
            second.result()
        self.engine.flush_batch(force=True)
        self.assertEqual([seqs for seqs, _ in self.sent], [tuple(range(1, 11)), (11,)])

    def test_batch_audit_preserves_full_membership_and_response_identity(self) -> None:
        self.enqueue(50)
        self.flush_due()
        seqs, cid = self.sent[0]
        self.engine.on_response(cid, "SellSideAck", 0, "수신 완료")
        rows = [
            json.loads(line)
            for line in (self.path / "audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        batch = [row for row in rows if row["type"].startswith("ACK_BATCH_")]
        self.assertEqual(
            [row["type"] for row in batch],
            ["ACK_BATCH_RESERVED", "ACK_BATCH_SENT", "ACK_BATCH_RESPONSE"],
        )
        self.assertEqual(len({row["batch_id"] for row in batch}), 1)
        for row in batch:
            self.assertEqual(row["sequences"], list(seqs))
            self.assertEqual(row["correlation_id"], cid)
        with patch("ack_engine.os.fsync") as sync:
            self.enqueue(50, 51)
            self.flush_due()
            self.assertEqual(sync.call_count, 2)

    def test_concurrent_replays_and_flushes_cannot_submit_twice(self) -> None:
        with ThreadPoolExecutor(12) as pool:
            list(pool.map(lambda _: self.enqueue(50), range(20)))
            list(pool.map(lambda _: self.engine.flush_batch(force=True), range(50)))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], tuple(range(1, 51)))

    def test_cancel_delete_and_parse_warning_remove_queued_candidates(self) -> None:
        self.enqueue(4)
        self.engine.on_order(
            7, {"EMSX_SEQUENCE": 1, "EMSX_STATUS": "CANCEL"}, True, True
        )
        self.engine.on_order(8, {"EMSX_SEQUENCE": 2}, True, True)
        with self.assertLogs("emsx", "WARNING"):
            self.engine.block_order(3, "수량 파싱 오류")
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 1}, True, True)
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 2}, True, True)
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 3}, True, True)
        self.flush_due()
        self.assertEqual(self.sent[0][0], (4,))

    def test_snapshot_prearm_and_later_partial_updates_never_enter_batch(self) -> None:
        self.engine.on_order(4, {**ORDER, "EMSX_SEQUENCE": 1}, True, True)
        self.engine.set_arm(False)
        self.enqueue(1, 2)
        self.engine.set_arm(True)
        self.engine.on_order(7, {**ORDER, "EMSX_SEQUENCE": 1}, True, True)
        self.engine.on_order(6, {**ORDER, "EMSX_SEQUENCE": 2}, True, True)
        self.flush_due()
        self.assertFalse(self.sent)

    def test_dispatch_rechecks_date_and_authorization(self) -> None:
        self.enqueue(3)
        self.engine.date_guard = lambda order: order["EMSX_SEQUENCE"] != 2
        self.flush_due()
        self.assertEqual(self.sent[0][0], (1, 3))
        self.enqueue(2, 4)
        self.config["beta_write_enabled"] = False
        self.now.return_value = 101.0
        self.engine.flush_batch()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.ledger.get("BETA", "HDGB", 4))

    def test_disarm_and_rearm_do_not_resurrect_queued_orders(self) -> None:
        self.enqueue(3)
        self.engine.set_arm(False)
        self.assertFalse(self.engine.queued)
        self.engine.set_arm(True)
        self.enqueue(3)
        self.flush_due()
        self.assertFalse(self.sent)

    def test_immediate_batch_response_is_not_overwritten(self) -> None:
        self.engine.batch_send_func = lambda seqs, cid: self.engine.on_response(
            cid, "SellSideAck", 0, "수신 완료"
        )
        self.enqueue(3)
        self.flush_due()
        self.assertFalse(self.engine.pending)
        for seq in (1, 2, 3):
            self.assertEqual(
                self.ledger.get("BETA", "HDGB", seq), "API_RESPONSE_OK_UNVERIFIED"
            )

    def test_sender_exception_after_immediate_response_preserves_known_result(
        self,
    ) -> None:
        def send(sequences: tuple[int, ...], cid: int) -> None:
            self.engine.on_response(cid, "SellSideAck", 0, "수신 완료")
            raise RuntimeError("응답 이후 전송 함수 오류")

        self.engine.batch_send_func = send
        self.enqueue(3)
        self.flush_due()
        self.assertFalse(self.engine.armed)
        for seq in (1, 2, 3):
            self.assertEqual(
                self.ledger.get("BETA", "HDGB", seq), "API_RESPONSE_OK_UNVERIFIED"
            )

    def test_partial_success_then_final_success_confirms_entire_batch(self) -> None:
        self.enqueue(3)
        self.flush_due()
        cid = self.sent[0][1]
        self.engine.on_response(cid, "SellSideAck", 0, "부분 응답", final=False)
        self.engine.on_response(cid, "SellSideAck", 0, "최종 응답", final=True)
        self.assertFalse(self.engine.pending)
        self.assertTrue(self.engine.armed)
        self.assertTrue(
            all(
                self.ledger.get("BETA", "HDGB", seq) == "API_RESPONSE_OK_UNVERIFIED"
                for seq in (1, 2, 3)
            )
        )

    def test_request_failure_preserves_unknown_and_discards_unsent_queue(self) -> None:
        self.enqueue(75)
        self.flush_due()
        self.enqueue(25, 76)
        self.engine.on_request_failure(self.sent[0][1], "요청 연결 실패")
        self.assertFalse(self.engine.armed)
        self.assertFalse(self.engine.queued)
        self.assertFalse(self.engine.pending)
        self.assertTrue(
            all(
                self.ledger.get("BETA", "HDGB", seq) == "UNKNOWN"
                for seq in range(1, 76)
            )
        )
        self.assertIsNone(self.ledger.get("BETA", "HDGB", 76))

    def test_loss_of_ready_state_discards_unsent_batch(self) -> None:
        self.enqueue(3)
        self.engine.set_ready(False)
        self.flush_due()
        self.assertFalse(self.sent)
        self.assertFalse(self.engine.queued)
        self.assertFalse(self.engine.armed)

    def test_reserved_by_other_sender_before_flush_is_excluded(self) -> None:
        self.enqueue(3)
        self.ledger.reserve_many("BETA", "HDGB", (1, 3))
        self.flush_due()
        self.assertEqual(self.sent[0][0], (2,))

    def test_large_sequence_audit_manifest_is_not_truncated(self) -> None:
        start = 10**18
        self.enqueue(50, start)
        self.flush_due()
        rows = [
            json.loads(line)
            for line in (self.path / "audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        manifest = next(row for row in rows if row["type"] == "ACK_BATCH_RESERVED")
        self.assertGreater(len(json.dumps(manifest["sequences"])), 600)
        self.assertEqual(manifest["sequences"], list(range(start, start + 50)))

    def test_partial_success_waits_for_final_and_error_stops_batch(self) -> None:
        self.enqueue(3)
        self.flush_due()
        cid = self.sent[0][1]
        self.engine.on_response(cid, "SellSideAck", 0, "부분 응답", final=False)
        self.assertIn(cid, self.engine.pending)
        self.assertEqual(self.ledger.get("BETA", "HDGB", 1), "SENT")
        self.engine.on_response(cid, "ErrorInfo", 10, "권한 오류", final=False)
        self.assertFalse(self.engine.armed)
        self.engine.on_response(cid, "SellSideAck", 0, "지연 성공 응답")
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) == "API_ERROR" for s in (1, 2, 3))
        )

    def test_inflight_limit_yields_until_response_releases_capacity(self) -> None:
        for seq in range(1, ACK_MAX_INFLIGHT + 1):
            self.enqueue(1, seq)
            self.engine.flush_batch(force=True)
        self.enqueue(75, ACK_MAX_INFLIGHT + 1)
        self.engine.flush_batch(force=True)
        self.assertEqual(len(self.sent), ACK_MAX_INFLIGHT)
        self.assertEqual(len(self.engine.queued), 75)
        self.assertEqual(self.engine.batch_timeout_ms(), 50)
        self.engine.on_response(self.sent[0][1], "SellSideAck", 0, "수신 완료")
        self.flush_due()
        self.assertEqual(len(self.sent), ACK_MAX_INFLIGHT + 1)
        self.assertEqual(len(self.sent[-1][0]), 75)

    def test_send_failure_marks_whole_batch_unknown_without_retry(self) -> None:
        self.engine.batch_send_func = Mock(side_effect=RuntimeError("통신 오류"))
        self.enqueue(75)
        self.flush_due()
        self.engine.batch_send_func.assert_called_once()
        self.assertFalse(self.engine.armed)
        self.assertFalse(self.engine.queued)
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) == "UNKNOWN" for s in range(1, 76))
        )
        self.enqueue(75)
        self.flush_due()
        self.engine.batch_send_func.assert_called_once()

    def test_audit_failure_blocks_every_order_before_api_call(self) -> None:
        self.enqueue(3)
        self.engine.audit_path = self.path
        with self.assertRaises(OSError):
            self.flush_due()
        self.engine.audit_path = self.path / "audit.jsonl"
        self.assertFalse(self.sent)
        self.assertFalse(self.engine.armed)
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) == "AUDIT_FAILED" for s in (1, 2, 3))
        )

    def test_timeout_and_close_preserve_all_unconfirmed_members(self) -> None:
        self.enqueue(75)
        self.flush_due()
        self.enqueue(25, 76)
        self.now.return_value = 121.0
        self.engine.check_pending()
        self.assertFalse(self.engine.queued)
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) == "UNKNOWN" for s in range(1, 76))
        )
        self.assertIsNone(self.ledger.get("BETA", "HDGB", 76))
        self.assertFalse(self.engine.armed)

    def test_connection_close_leaves_queued_orders_unsent(self) -> None:
        self.enqueue(75)
        self.flush_due()
        self.enqueue(25, 76)
        self.engine.close()
        self.assertFalse(self.engine.pending)
        self.assertFalse(self.engine.queued)
        self.assertEqual(self.ledger.get("BETA", "HDGB", 1), "UNKNOWN")
        self.assertIsNone(self.ledger.get("BETA", "HDGB", 76))

    def test_shared_database_batches_never_overlap(self) -> None:
        other = AckLedger(self.path / "ack.db")
        try:
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(
                    self.ledger.reserve_many, "BETA", "HDGB", range(1, 101)
                )
                second = pool.submit(other.reserve_many, "BETA", "HDGB", range(50, 151))
                a, b = set(first.result()), set(second.result())
            self.assertFalse(a & b)
            self.assertEqual(a | b, set(range(1, 151)))
        finally:
            other.close()

    def test_batch_reservation_rolls_back_all_members_on_database_error(self) -> None:
        self.ledger.conn.execute(
            "CREATE TRIGGER reject_second BEFORE INSERT ON ack_intent "
            "WHEN NEW.sequence=2 BEGIN SELECT RAISE(ABORT, '저장 실패'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.reserve_many("BETA", "HDGB", (1, 2, 3))
        self.assertTrue(
            all(self.ledger.get("BETA", "HDGB", s) is None for s in (1, 2, 3))
        )


class TestBatchWorker(unittest.TestCase):
    def test_native_boundary_appends_all_sequences_to_one_request(self) -> None:
        worker = BloombergWorker("BETA", "HDGB", queue.Queue())
        worker.started = {"O", "R"}
        worker.snapshot_done = {"O": True, "R": True}
        worker.service = Mock()
        worker.session = Mock()
        worker.api = SimpleNamespace(CorrelationId=lambda cid: cid)
        with self.assertLogs("emsx", "INFO") as captured:
            worker._send_batch((101, 102, 103), 121000)
        self.assertIn("Order 3개", " ".join(captured.output))
        self.assertIn("sendRequest=1 CID=121000", " ".join(captured.output))
        request = worker.service.createRequest.return_value
        self.assertEqual(request.append.call_count, 3)
        self.assertEqual(
            [call.args for call in request.append.call_args_list],
            [("EMSX_SEQUENCE", seq) for seq in (101, 102, 103)],
        )
        worker.session.sendRequest.assert_called_once_with(
            request, correlationId=121000
        )
        worker.stop()
        with self.assertRaises(RuntimeError):
            worker._send_batch((104,), 121001)
        self.assertEqual(worker.session.sendRequest.call_count, 1)

    def test_final_event_error_wins_over_success_for_same_batch(self) -> None:
        worker = BloombergWorker("BETA", "HDGB", queue.Queue())
        worker.api = SimpleNamespace(Event=SimpleNamespace(RESPONSE=3))
        worker.engine = Mock(pending={121000: object()})
        event = SimEvent(
            3,
            [
                SimMessage("SellSideAck", {"STATUS": 0}, 121000),
                SimMessage(
                    "ErrorInfo", {"ERROR_CODE": 99, "ERROR_MESSAGE": "거부"}, 121000
                ),
                SimMessage("SellSideAck", {"STATUS": 0}, 121000),
            ],
        )
        worker._process_response(event)
        worker.engine.on_response.assert_called_once_with(
            121000, "ErrorInfo", 99, "거부", final=True
        )

    def test_worker_batches_on_background_thread_and_honors_stop_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = queue.Queue()
            worker = BloombergWorker(
                "BETA",
                "HDGB",
                output,
                config={**default_config(), "beta_write_enabled": True},
                ledger_path=Path(tmp) / "ack.db",
            )
            today = int(desk_today().strftime("%Y%m%d"))
            events = iter(
                [
                    SimEvent(
                        2,
                        [
                            SimMessage("SubscriptionStarted", {}, cid)
                            for cid in (901, 902)
                        ],
                    ),
                    SimEvent(
                        1,
                        [
                            SimMessage("수신 완료", {"EVENT_STATUS": 11}, cid)
                            for cid in (901, 902)
                        ],
                    ),
                    SimEvent(
                        1,
                        [
                            SimMessage(
                                "신규 Order",
                                {
                                    **ORDER,
                                    "EMSX_SEQUENCE": seq,
                                    "EMSX_DATE": today,
                                    "EVENT_STATUS": 6,
                                    "API_SEQ_NUM": seq,
                                },
                            )
                            for seq in range(1, 121)
                        ],
                    ),
                    SimEvent(0, []),
                ]
            )
            session = Mock()
            session.tryNextEvent.return_value = None
            session.start.return_value = True
            session.openService.return_value = True
            sent_threads: list[int] = []
            clock = [100.0]

            def send(request: object, correlationId: int) -> None:
                sent_threads.append(threading.get_ident())

            def next_event(timeout: int) -> SimEvent:
                self.assertGreaterEqual(timeout, 1)
                if worker._ready() and not worker.engine.armed:
                    worker.commands.put({"action": "arm", "armed": True})
                if len(sent_threads) == 1:
                    worker.commands.put({"action": "stop"})
                    return SimEvent(0, [])
                event = next(events, None)
                if event is None:
                    clock[0] += timeout / 1000
                    return SimEvent(0, [])
                return event

            session.sendRequest.side_effect = send
            session.nextEvent.side_effect = next_event
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
            worker.enrichment_enabled = False
            main_thread = threading.get_ident()
            with (
                patch.dict("sys.modules", {"blpapi": api}),
                patch("bloomberg_feed.time.monotonic", side_effect=lambda: clock[0]),
            ):
                worker.start()
                worker.join(3)
                if worker.is_alive():
                    worker.stop()
                    worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(sent_threads), 1)
            self.assertEqual(
                session.getService.return_value.createRequest.return_value.append.call_count,
                120,
            )
            self.assertTrue(all(tid != main_thread for tid in sent_threads))
            messages = list(output.queue)
            self.assertTrue(any(m["type"] == "data" for m in messages))
            self.assertFalse(any(m["type"] == "error" for m in messages))
            ledger = AckLedger(Path(tmp) / "ack.db")
            try:
                self.assertEqual(ledger.get("BETA", "HDGB", 1), "UNKNOWN")
                self.assertEqual(ledger.get("BETA", "HDGB", 120), "UNKNOWN")
            finally:
                ledger.close()


class TestFragmentedSession(unittest.TestCase):
    """개별 Bloomberg 이벤트와 실제 전송 경계 사이의 전체 경로를 재생한다."""

    def replay(
        self,
        arrivals: list[tuple[float, SimEvent]],
    ) -> tuple[list[tuple[int, ...]], list[dict[str, Any]], int]:
        with tempfile.TemporaryDirectory() as tmp:
            worker = BloombergWorker(
                "BETA",
                "HDGB",
                queue.Queue(),
                config={**default_config(), "beta_write_enabled": True},
                ledger_path=Path(tmp) / "ack.db",
            )
            worker.enrichment_enabled = False
            clock = [100.0]
            events = deque(
                [
                    (
                        0.0,
                        SimEvent(
                            2,
                            [
                                SimMessage("SubscriptionStarted", {}, cid)
                                for cid in (901, 902)
                            ],
                        ),
                    ),
                    (
                        0.0,
                        SimEvent(
                            1,
                            [
                                SimMessage("초기 수신 완료", {"EVENT_STATUS": 11}, cid)
                                for cid in (901, 902)
                            ],
                        ),
                    ),
                    *arrivals,
                ]
            )
            requests: list[tuple[int, ...]] = []
            poll_count = 0
            arm_queued = False

            def enqueue_arm() -> None:
                nonlocal arm_queued
                if worker._ready() and not arm_queued:
                    worker.commands.put({"action": "arm", "armed": True})
                    arm_queued = True

            def poll() -> SimEvent | None:
                nonlocal poll_count
                poll_count += 1
                enqueue_arm()
                if events and events[0][0] <= clock[0] - 100.0 + 0.000001:
                    return events.popleft()[1]
                return None

            def next_event(timeout: int) -> SimEvent:
                enqueue_arm()
                if requests:
                    worker.commands.put({"action": "stop"})
                    return SimEvent(0, [])
                deadline = clock[0] + timeout / 1000
                if events and 100.0 + events[0][0] <= deadline:
                    instant, event = events.popleft()
                    clock[0] = 100.0 + instant
                    return event
                clock[0] = deadline
                if clock[0] > 101.0:
                    worker.stop()
                return SimEvent(0, [])

            def create_request(name: str) -> Any:
                self.assertEqual(name, "SellSideAck")
                values: list[int] = []

                def append(field: str, sequence: int) -> None:
                    self.assertEqual(field, "EMSX_SEQUENCE")
                    values.append(sequence)

                return SimpleNamespace(values=values, append=append)

            service = SimpleNamespace(createRequest=Mock(side_effect=create_request))
            session = SimpleNamespace(
                start=lambda: True,
                openService=lambda name: True,
                openServiceAsync=lambda name, correlationId: None,
                getService=lambda name: service,
                subscribe=lambda subs: None,
                nextEvent=next_event,
                tryNextEvent=poll,
                sendRequest=lambda request, correlationId: requests.append(
                    tuple(request.values)
                ),
                stop=lambda: None,
            )
            api = SimpleNamespace(
                SessionOptions=Mock,
                Session=lambda options: session,
                CorrelationId=lambda cid: cid,
                SubscriptionList=Mock,
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
                with patch(
                    "bloomberg_feed.time.monotonic", side_effect=lambda: clock[0]
                ):
                    worker._run_session(api)
                self.assertEqual(service.createRequest.call_count, len(requests))
                return requests, list(worker.output.queue), poll_count
            finally:
                worker._cleanup_session()

    def order_event(self, sequence: int) -> SimEvent:
        return SimEvent(
            1,
            [
                SimMessage(
                    "신규 Order",
                    {
                        **ORDER,
                        "EMSX_SEQUENCE": sequence,
                        "API_SEQ_NUM": sequence,
                        "EMSX_DATE": int(desk_today().strftime("%Y%m%d")),
                        "EVENT_STATUS": 6,
                    },
                )
            ],
        )

    def test_500_separate_ready_events_become_one_native_api_submission(self) -> None:
        requests, output, polls = self.replay(
            [(0.0, self.order_event(seq)) for seq in range(1, 501)]
        )
        self.assertEqual(requests, [tuple(range(1, 501))])
        self.assertGreaterEqual(polls, 500)
        sent = [
            msg
            for msg in output
            if msg["type"] == "ack_batch" and msg["state"] == "SENT"
        ]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["count"], 500)
        self.assertEqual(sent[0]["sequences"], tuple(range(1, 501)))

    def test_30ms_spaced_events_are_submitted_together_through_worker(self) -> None:
        requests, _, _ = self.replay(
            [(0.020 + (seq - 1) * 0.030, self.order_event(seq)) for seq in range(1, 7)]
        )
        self.assertEqual(requests, [tuple(range(1, 7))])

    def test_buffered_disconnect_is_applied_before_bulk_submission(self) -> None:
        with self.assertLogs("emsx", "ERROR"):
            requests, output, _ = self.replay(
                [
                    *[(0.0, self.order_event(seq)) for seq in range(1, 101)],
                    (0.0, SimEvent(5, [SimMessage("SessionConnectionDown", {})])),
                ]
            )
        self.assertFalse(requests)
        self.assertTrue(any(m["type"] == "error" for m in output))

    def test_buffered_cancel_is_excluded_before_native_submission(self) -> None:
        requests, _, _ = self.replay(
            [
                *[(0.0, self.order_event(seq)) for seq in range(1, 101)],
                (
                    0.0,
                    SimEvent(
                        1,
                        [
                            SimMessage(
                                "취소",
                                {
                                    "EMSX_SEQUENCE": 50,
                                    "EMSX_STATUS": "CANCEL",
                                    "EVENT_STATUS": 7,
                                    "API_SEQ_NUM": 101,
                                },
                            )
                        ],
                    ),
                ),
            ]
        )
        self.assertEqual(requests, [tuple(seq for seq in range(1, 101) if seq != 50)])


if __name__ == "__main__":
    unittest.main()
