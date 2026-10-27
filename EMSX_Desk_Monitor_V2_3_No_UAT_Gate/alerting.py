"""화면 갱신 빈도와 무관하게 신규·지속 경고의 알림 주기를 관리한다."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class AlertTracker:
    def __init__(
        self, now: Callable[[], float] = time.monotonic, repeat_seconds: float = 30.0
    ) -> None:
        self.now = now
        self.repeat_seconds = repeat_seconds
        self.active: set[tuple[int, str]] = set()
        self.last_sound: float | None = None

    def update(self, rows: list[dict[str, Any]], enabled: bool = True) -> bool:
        """경고가 지속되면 재알림하고 해소 후 재발한 경고도 다시 통지한다."""
        self.active = {
            (row["seq"], kind) for row in rows for kind in row.get("alert_types", ())
        }
        if not self.active:
            self.last_sound = None
            return False
        if not enabled:
            return False
        now = self.now()
        if self.last_sound is None or now - self.last_sound >= self.repeat_seconds:
            self.last_sound = now
            return True
        return False


def play_alarm(fallback: Callable[[], Any]) -> None:
    """Windows 시스템 경고음을 비동기로 재생하며 다른 환경에서는 창 알림을 사용한다."""
    try:
        import winsound
    except ImportError:
        fallback()
        return
    try:
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except RuntimeError:
        fallback()
