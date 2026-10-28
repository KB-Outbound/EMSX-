"""진단 로그의 소유권과 모든 작업자 종료 경로의 파일 해제를 검증한다."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from bloomberg_feed import BloombergWorker
from diagnostics import LOGGER, configure_diagnostics, diagnostic_session


def file_handler(path: Path) -> RotatingFileHandler:
    """현재 파일을 소유한 핸들러를 찾아 실제 닫힘 상태를 검사한다."""
    return next(
        handler
        for handler in LOGGER.handlers
        if isinstance(handler, RotatingFileHandler)
        and handler.baseFilename == str(path.resolve())
    )


class TestDiagnosticLifetime(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_level = LOGGER.level

    def tearDown(self) -> None:
        LOGGER.setLevel(self.previous_level)

    def assert_released(self, handler: RotatingFileHandler) -> None:
        self.assertIsNone(handler.stream)
        self.assertTrue(handler._closed)
        self.assertNotIn(handler, LOGGER.handlers)

    def test_session_exit_closes_stream_before_file_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with diagnostic_session(Path(tmp)) as path:
                handler = file_handler(path)
                LOGGER.info("진단 파일 정상 종료 검사")
                self.assertFalse(handler.stream.closed)
            self.assert_released(handler)
            self.assertIn("정상 종료 검사", path.read_text(encoding="utf-8"))
            moved = path.with_suffix(".moved")
            path.rename(moved)
            moved.unlink()

    def test_shared_path_closes_only_after_last_user_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with diagnostic_session(Path(tmp)) as path:
                handler = file_handler(path)
                with diagnostic_session(Path(tmp)) as shared:
                    self.assertEqual(shared, path)
                    self.assertIs(file_handler(shared), handler)
                self.assertIsNotNone(handler.stream)
                self.assertIn(handler, LOGGER.handlers)
                LOGGER.info("공유 사용자의 기록 유지")
            self.assert_released(handler)

    def test_preexisting_handlers_are_not_owned_or_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            persistent = configure_diagnostics(directory / "existing")
            existing_handler = file_handler(persistent)
            try:
                with diagnostic_session(persistent.parent):
                    self.assertIs(file_handler(persistent), existing_handler)
                with diagnostic_session(directory / "worker") as owned_path:
                    owned_handler = file_handler(owned_path)
                self.assert_released(owned_handler)
                self.assertIsNotNone(existing_handler.stream)
                self.assertIn(existing_handler, LOGGER.handlers)
            finally:
                LOGGER.removeHandler(existing_handler)
                existing_handler.close()

    def test_delayed_record_cannot_reopen_closed_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with diagnostic_session(Path(tmp)) as path:
                handler = file_handler(path)
            path.unlink()
            record = logging.LogRecord(
                "emsx", logging.INFO, __file__, 1, "종료 후 지연된 기록", (), None
            )
            handler.handle(record)
            self.assert_released(handler)
            self.assertFalse(path.exists())

    def test_missing_sdk_early_return_releases_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = BloombergWorker(
                "BETA", "HDGB", queue.Queue(), ledger_path=Path(tmp) / "ack.db"
            )
            original_emit = worker.emit
            captured: list[RotatingFileHandler] = []

            def emit(kind: str, **fields: Any) -> None:
                if kind == "diagnostics":
                    captured.append(file_handler(Path(fields["path"])))
                original_emit(kind, **fields)

            with (
                patch.object(worker, "emit", side_effect=emit),
                patch.dict("sys.modules", {"blpapi": None}),
            ):
                worker.run()
            self.assertEqual(len(captured), 1)
            self.assert_released(captured[0])
            messages = list(worker.output.queue)
            self.assertEqual(messages[-1]["type"], "ended")

    def test_unexpected_worker_exception_releases_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = BloombergWorker(
                "BETA", "HDGB", queue.Queue(), ledger_path=Path(tmp) / "ack.db"
            )
            captured: list[RotatingFileHandler] = []

            def fail() -> None:
                captured.append(file_handler(Path(tmp) / "feed_diagnostics.log"))
                raise RuntimeError("모의 초기화 오류")

            with patch.object(worker, "_run", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "초기화 오류"):
                    worker.run()
            self.assert_released(captured[0])

    def test_initial_notification_failure_releases_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = BloombergWorker(
                "BETA", "HDGB", queue.Queue(), ledger_path=Path(tmp) / "ack.db"
            )
            captured: list[RotatingFileHandler] = []

            def fail(kind: str, **fields: Any) -> None:
                captured.append(file_handler(Path(fields["path"])))
                raise RuntimeError("모의 화면 큐 과부하")

            with patch.object(worker, "emit", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "큐 과부하"):
                    worker.run()
            self.assert_released(captured[0])


if __name__ == "__main__":
    unittest.main()
