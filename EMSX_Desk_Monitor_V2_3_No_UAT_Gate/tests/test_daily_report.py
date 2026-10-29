"""보고서의 날짜 경계·원문 보존·수식 차단·원자 저장·비동기 일지를 검증한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from daily_report import ReportRequest, export_daily, read_ack_audit, write_xlsx
from desk_journal import DeskJournal, JournalRows
from order_fields import DESK_TIMEZONE

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def request(directory: Path) -> ReportRequest:
    return ReportRequest(
        directory / "일지.xlsx",
        "2026-10-29",
        "PROD",
        "HDGB",
        [],
        [],
        [],
        directory / "ack.jsonl",
        0,
        "2026-10-29T14:00:00+00:00",
        "검증용 로컬 일정",
    )


def sheet_rows(path: Path, index: int) -> list[list[str]]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
    return [
        ["".join(cell.itertext()) for cell in row.findall("x:c", NS)]
        for row in root.findall("x:sheetData/x:row", NS)
    ]


class TestDailyReport(unittest.TestCase):
    def test_seven_sheets_preserve_korean_notes_and_batch_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            req = request(directory)
            audit = {
                "utc": "2026-10-29T00:00:00+00:00",
                "environment": "PROD",
                "team": "HDGB",
                "type": "ACK_BATCH_SENT",
                "sequences": [1, 2, 3],
                "batch_id": "배치",
            }
            req.audit_path.write_text(
                json.dumps(audit, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            req = replace(req, audit_size=req.audit_path.stat().st_size)
            events = [
                {
                    "id": 1,
                    "utc": "2026-10-29T01:00:00+00:00",
                    "environment": "PROD",
                    "team": "HDGB",
                    "kind": "메모",
                    "sequence": 1,
                    "payload": json.dumps(
                        {"author": "담당자", "note": "고객 MOC 확인"},
                        ensure_ascii=False,
                    ),
                }
            ]
            result = export_daily(req, events)
            self.assertEqual(result["events"], 1)
            self.assertEqual(result["audits"], 1)
            with ZipFile(req.path) as archive:
                for name in archive.namelist():
                    if name.endswith((".xml", ".rels")):
                        ET.fromstring(archive.read(name))
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                self.assertEqual(len(workbook.findall("x:sheets/x:sheet", NS)), 7)
            self.assertIn("고객 MOC 확인", sheet_rows(req.path, 6)[1])
            self.assertIn("[1, 2, 3]", sheet_rows(req.path, 5)[1])

    def test_audit_filter_uses_kst_day_environment_team_and_byte_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = request(Path(tmp))
            records = []
            for instant, environment, team in (
                ("2026-10-28T14:59:59+00:00", "PROD", "HDGB"),
                ("2026-10-28T15:00:00+00:00", "PROD", "HDGB"),
                ("2026-10-29T00:30:00+09:00", "PROD", "HDGB"),
                ("2026-10-29T15:00:00+00:00", "PROD", "HDGB"),
                ("2026-10-29T01:00:00+00:00", "BETA", "HDGB"),
                ("2026-10-29T01:00:00+00:00", "PROD", "OTHER"),
            ):
                records.append(
                    {"utc": instant, "environment": environment, "team": team}
                )
            req.audit_path.write_text(
                "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
            )
            req = replace(req, audit_size=req.audit_path.stat().st_size)
            with req.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "utc": "2026-10-29T10:00:00+00:00",
                            "environment": "PROD",
                            "team": "HDGB",
                        }
                    )
                    + "\n"
                )
            rows, warnings = read_ack_audit(req)
            self.assertEqual(
                [row["utc"] for row in rows], [records[1]["utc"], records[2]["utc"]]
            )
            self.assertFalse(warnings)

    def test_partial_or_malformed_audit_is_reported_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = request(Path(tmp))
            req.audit_path.write_bytes(b'{"broken":1}\nnot-json\n{"unfinished":')
            req = replace(req, audit_size=req.audit_path.stat().st_size)
            rows, warnings = read_ack_audit(req)
            self.assertFalse(rows)
            self.assertIn("3개", warnings[0])

    def test_formula_like_text_and_long_unicode_are_values_without_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "메모.xlsx"
            note = "종가😀" * 14000
            write_xlsx(
                path,
                [
                    (
                        "메모",
                        ["기록 ID", "내용", "외부 입력"],
                        [
                            [
                                1234567890123456789,
                                note,
                                '=HYPERLINK("http://example.invalid")',
                            ]
                        ],
                    )
                ],
            )
            rows = sheet_rows(path, 1)
            self.assertEqual("".join(row[1] for row in rows[1:]), note)
            self.assertTrue(all(row[0] == "1234567890123456789" for row in rows[1:]))
            with ZipFile(path) as archive:
                xml = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                self.assertFalse(xml.findall(".//x:f", NS))
                self.assertTrue(
                    all(
                        cell.attrib.get("t") == "inlineStr"
                        for cell in xml.findall(".//x:c", NS)
                    )
                )

    def test_failed_export_preserves_existing_report_and_cleans_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "일지.xlsx"
            path.write_bytes("기존 보고서".encode("utf-8"))
            with patch(
                "daily_report._write_sheet", side_effect=OSError("모의 디스크 오류")
            ):
                with self.assertRaises(OSError):
                    write_xlsx(path, [("요약", ["항목"], [["내용"]])])
            self.assertEqual(path.read_bytes(), "기존 보고서".encode("utf-8"))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


class TestDeskJournal(unittest.TestCase):
    def test_burst_is_persisted_beyond_ui_history_and_reopen_preserves_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "일지.db"
            for count in (1200, 5):
                journal = DeskJournal(path, queue.Queue())
                journal.start()
                for index in range(count):
                    self.assertTrue(
                        journal.record(
                            "수신:data",
                            "PROD",
                            "hdgb",
                            {"fields": {"EMSX_SEQUENCE": index}},
                        )
                    )
                journal.stop()
                journal.join(10)
                self.assertFalse(journal.is_alive())
                self.assertFalse(journal.error)
                self.assertFalse(journal.record("메모", "PROD", "HDGB", {}))
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM desk_event").fetchone()[0],
                    1205,
                )
                connection.row_factory = sqlite3.Row
                now = datetime.now(timezone.utc)
                req = replace(
                    request(Path(tmp)),
                    day=str(now.astimezone(DESK_TIMEZONE).date()),
                    created=now.isoformat(),
                )
                source = JournalRows(connection, req, 1205)
                self.assertEqual(len(source), 1205)
                self.assertEqual(len({row["id"] for row in source}), 1205)
                self.assertFalse(list(source.notes()))
            finally:
                connection.close()

    def test_kst_midnight_and_null_fields_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeskJournal(Path(tmp) / "일지.db", queue.Queue())
            journal.start()
            for instant in ("2026-10-28T14:59:59+00:00", "2026-10-28T15:00:00+00:00"):
                journal.record(
                    "수신:data",
                    "PROD",
                    "HDGB",
                    {"fields": None},
                    datetime.fromisoformat(instant),
                )
            journal.stop()
            journal.join(5)
            connection = sqlite3.connect(journal.path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT day_kst FROM desk_event ORDER BY id"
                    ).fetchall(),
                    [("2026-10-28",), ("2026-10-29",)],
                )
            finally:
                connection.close()
            with self.assertRaises(ValueError):
                DeskJournal(journal.path, queue.Queue()).record(
                    "메모", "PROD", "HDGB", {}, datetime(2026, 10, 29)
                )

    def test_overflow_is_visible_and_not_silently_accepted(self) -> None:
        journal = DeskJournal(Path("사용하지않음.db"), queue.Queue(), capacity=1)
        self.assertTrue(journal.record("메모", "PROD", "HDGB", {"note": "첫 기록"}))
        with self.assertLogs("emsx", "ERROR"):
            self.assertFalse(journal.record("메모", "PROD", "HDGB", {"note": "초과"}))
        self.assertEqual(journal.dropped, 1)
        self.assertEqual(journal.output.get_nowait()["type"], "journal_error")

    def test_export_runs_separately_and_new_records_continue_during_export(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeskJournal(Path(tmp) / "일지.db", queue.Queue())
            entered, release = threading.Event(), threading.Event()
            captured = []

            def export(req: ReportRequest, events: list[dict]) -> dict:
                captured.extend(events)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("보고서 대역 시간 초과")
                return {
                    "path": str(req.path),
                    "events": len(events),
                    "audits": 0,
                    "warnings": [],
                }

            now = datetime.now(timezone.utc)
            req = replace(
                request(Path(tmp)),
                day=str(now.astimezone(DESK_TIMEZONE).date()),
                created=now.isoformat(),
            )
            journal.record("메모", "PROD", "HDGB", {"note": "보고서 이전"}, now)
            with patch("desk_journal.export_daily", side_effect=export):
                journal.start()
                try:
                    self.assertTrue(journal.request_report(req))
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(journal.request_report(req))
                    self.assertTrue(
                        journal.record("메모", "PROD", "HDGB", {"note": "보고서 이후"})
                    )
                    journal.stop()
                    journal.join(5)
                    self.assertFalse(journal.is_alive())
                    self.assertEqual(len(captured), 1)
                finally:
                    release.set()
                    journal.stop()
                    journal.join(5)
                    if journal.report_thread:
                        journal.report_thread.join(5)
            connection = sqlite3.connect(journal.path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM desk_event").fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_initialization_failure_is_visible_and_rejects_new_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "파일"
            parent.write_text("폴더가 아님", encoding="utf-8")
            journal = DeskJournal(parent / "일지.db", queue.Queue())
            with self.assertLogs("emsx", "ERROR"):
                journal.start()
                journal.join(5)
            self.assertTrue(journal.failed.is_set())
            self.assertFalse(journal.record("메모", "PROD", "HDGB", {}))
            self.assertFalse(journal.request_report(request(Path(tmp))))
