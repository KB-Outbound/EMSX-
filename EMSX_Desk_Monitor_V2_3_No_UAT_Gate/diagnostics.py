"""실행 파일에서도 추적 가능한 크기 제한 진단 로그를 설정한다."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = logging.getLogger("emsx")


def configure_diagnostics(directory: Path) -> Path:
    """핸들러 중복 없이 최대 10MB의 UTF-8 순환 로그를 보존한다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / "feed_diagnostics.log").resolve()
    for handler in LOGGER.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(
            path
        ):
            return path
    handler = RotatingFileHandler(
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
