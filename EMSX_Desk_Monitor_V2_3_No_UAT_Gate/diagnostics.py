"""실행 파일에서도 추적 가능한 크기 제한 진단 로그를 설정한다."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
from typing import Iterator

LOGGER = logging.getLogger("emsx")
_LOCK = threading.RLock()


class _DiagnosticFileHandler(RotatingFileHandler):
    """종료 직후 지연된 로그 호출이 파일을 다시 열지 않도록 한다."""

    def emit(self, record: logging.LogRecord) -> None:
        if not self._closed:
            super().emit(record)


@dataclass
class _DiagnosticScope:
    """로그 파일의 활성 사용자 수와 생성 소유권을 보관한다."""

    handler: RotatingFileHandler
    users: int
    owned: bool


_ACTIVE_SCOPES: dict[Path, _DiagnosticScope] = {}


def configure_diagnostics(directory: Path) -> Path:
    """핸들러 중복 없이 최대 10MB의 UTF-8 순환 로그를 보존한다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / "feed_diagnostics.log").resolve()
    with _LOCK:
        for handler in LOGGER.handlers:
            if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(
                path
            ):
                return path
        handler = _DiagnosticFileHandler(
            path, maxBytes=2_000_000, backupCount=4, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s"
            )
        )
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    return path


@contextmanager
def diagnostic_session(directory: Path) -> Iterator[Path]:
    """마지막 작업자가 끝나면 해당 작업자들이 연 진단 파일만 해제한다."""
    with _LOCK:
        previous = tuple(LOGGER.handlers)
        path = configure_diagnostics(directory)
        scope = _ACTIVE_SCOPES.get(path)
        if scope is None:
            handler = next(
                handler
                for handler in LOGGER.handlers
                if isinstance(handler, RotatingFileHandler)
                and handler.baseFilename == str(path)
            )
            scope = _DiagnosticScope(handler, 0, handler not in previous)
            _ACTIVE_SCOPES[path] = scope
        scope.users += 1
    try:
        yield path
    finally:
        with _LOCK:
            scope.users -= 1
            if scope.users == 0:
                _ACTIVE_SCOPES.pop(path)
                if scope.owned:
                    LOGGER.removeHandler(scope.handler)
                    scope.handler.close()
