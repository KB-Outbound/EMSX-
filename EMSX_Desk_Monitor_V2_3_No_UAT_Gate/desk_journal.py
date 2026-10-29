"""UI를 막지 않고 데스크 관측·메모를 보존하고 일일 보고서를 생성한다."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import sqlite3
import threading
from typing import Any, Iterator

from daily_report import ReportRequest, export_daily
from diagnostics import LOGGER
from order_fields import DESK_TIMEZONE


class JournalRows:
    """고정된 접수 경계의 기록을 작은 묶음으로 읽어 보고서 메모리를 제한한다."""

    def __init__(
        self, connection: sqlite3.Connection, request: ReportRequest, cutoff: int
    ) -> None:
        self.connection = connection
        self.parameters = (
            request.day,
            request.environment,
            request.team.upper(),
            cutoff,
        )
        self.where = " WHERE day_kst=? AND environment=? AND team=? AND id<=?"
        self.count = connection.execute(
            "SELECT COUNT(*) FROM desk_event" + self.where, self.parameters
        ).fetchone()[0]

    def __len__(self) -> int:
        return self.count

    def _read(self, extra: str = "") -> Iterator[dict[str, Any]]:
        last_time, last_id = "", 0
        while True:
            cursor = self.connection.execute(
                "SELECT * FROM desk_event"
                + self.where
                + extra
                + " AND (utc>? OR (utc=? AND id>?)) ORDER BY utc,id LIMIT 1000",
                (*self.parameters, last_time, last_time, last_id),
            )
            try:
                rows = cursor.fetchall()
            finally:
                cursor.close()
            if not rows:
                return
            for row in rows:
                yield dict(row)
            last_time, last_id = rows[-1]["utc"], rows[-1]["id"]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self._read()

    def notes(self) -> Iterator[dict[str, Any]]:
        return self._read(" AND kind='메모'")


class DeskJournal(threading.Thread):
    def __init__(self, path: Path, output: queue.Queue, capacity: int = 50000) -> None:
        super().__init__(name="데스크 일지", daemon=True)
        self.path = path
        self.output = output
        self.tasks: queue.Queue = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.failed = threading.Event()
        self._lock = threading.Lock()
        self.error = ""
        self.dropped = 0
        self.reporting = False
        self.report_thread: threading.Thread | None = None

    def _failure(self, message: str, dropped: bool = False) -> None:
        with self._lock:
            first = not self.error
            self.error = message
            self.dropped += int(dropped)
        if first:
            LOGGER.error("데스크 일지 오류: %s", message)
            self.output.put({"type": "journal_error", "message": message})

    def record(
        self,
        kind: str,
        environment: str,
        team: str,
        payload: dict[str, Any],
        instant: datetime | None = None,
    ) -> bool:
        if self.stopping.is_set() or self.failed.is_set():
            return False
        now = instant or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("일지 시각에는 시간대가 필요합니다")
        fields = payload.get("fields")
        sequence = payload.get(
            "sequence",
            fields.get("EMSX_SEQUENCE") if isinstance(fields, dict) else None,
        )
        if type(sequence) is not int or not -(2**63) <= sequence < 2**63:
            sequence = None
        item = (
            now.astimezone(timezone.utc).isoformat(),
            str(now.astimezone(DESK_TIMEZONE).date()),
            environment,
            team.upper(),
            kind,
            sequence,
            json.dumps(payload, ensure_ascii=False, default=str),
        )
        try:
            with self._lock:
                if self.stopping.is_set() or self.failed.is_set():
                    return False
                self.tasks.put_nowait(("event", item))
            return True
        except queue.Full:
            self._failure("일지 대기열 초과 · 관측 기록 누락 가능성", dropped=True)
            return False

    def request_report(self, request: ReportRequest) -> bool:
        with self._lock:
            if self.reporting or self.stopping.is_set() or self.failed.is_set():
                return False
            try:
                self.tasks.put_nowait(("export", request))
            except queue.Full:
                pass
            else:
                self.reporting = True
                return True
        self._failure("보고서 요청 대기열 초과")
        return False

    def stop(self) -> None:
        """접수를 중단하고 이미 접수한 기록을 모두 저장한 뒤 종료한다."""
        with self._lock:
            self.stopping.set()

    def _export(self, request: ReportRequest, cutoff: int) -> None:
        connection = None
        try:
            connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True
            )
            connection.row_factory = sqlite3.Row
            result = export_daily(request, JournalRows(connection, request, cutoff))
            self.output.put({"type": "report_done", **result})
        except Exception as exc:
            LOGGER.exception("당일 보고서 생성 실패")
            self.output.put({"type": "report_error", "message": str(exc)})
        finally:
            if connection is not None:
                connection.close()
            with self._lock:
                self.reporting = False

    def run(self) -> None:
        connection = None
        batch: list[tuple] = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.path), timeout=10)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS desk_event ("
                "id INTEGER PRIMARY KEY, utc TEXT NOT NULL, day_kst TEXT NOT NULL, "
                "environment TEXT NOT NULL, team TEXT NOT NULL, kind TEXT NOT NULL, "
                "sequence INTEGER, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS desk_event_clock "
                "ON desk_event(day_kst,environment,team,utc,id)"
            )
            connection.commit()
            self.output.put({"type": "journal_ready"})

            def flush() -> None:
                if batch:
                    with connection:
                        connection.executemany(
                            "INSERT INTO desk_event(utc,day_kst,environment,team,"
                            "kind,sequence,payload) "
                            "VALUES(?,?,?,?,?,?,?)",
                            batch,
                        )
                    batch.clear()

            while not self.stopping.is_set() or not self.tasks.empty():
                try:
                    kind, item = self.tasks.get(timeout=0.2)
                except queue.Empty:
                    flush()
                    continue
                if kind == "event":
                    batch.append(item)
                    if len(batch) >= 200:
                        flush()
                elif kind == "export":
                    flush()
                    cutoff = connection.execute(
                        "SELECT COALESCE(MAX(id),0) FROM desk_event"
                    ).fetchone()[0]
                    self.report_thread = threading.Thread(
                        target=self._export,
                        args=(item, cutoff),
                        name="당일 보고서",
                        daemon=True,
                    )
                    self.report_thread.start()
            flush()
        except Exception as exc:
            self.failed.set()
            self._failure(f"일지 저장 실패: {exc}")
            with self._lock:
                active_report = self.report_thread and self.report_thread.is_alive()
                pending_report = self.reporting and not active_report
                if not active_report:
                    self.reporting = False
            if pending_report:
                self.output.put(
                    {
                        "type": "report_error",
                        "message": "일지 저장 실패로 내보내기를 완료하지 못했습니다",
                    }
                )
        finally:
            if connection is not None:
                connection.close()
