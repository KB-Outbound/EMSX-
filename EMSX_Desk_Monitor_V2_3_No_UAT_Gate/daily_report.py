"""읽기 전용 일지와 Ack 감사를 표준 Excel 파일로 내보낸다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Iterator, Protocol
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET

from order_fields import DESK_TIMEZONE

Sheet = tuple[str, list[str], Iterable[list[Any]]]


class EventRows(Protocol):
    def __iter__(self) -> Iterator[dict[str, Any]]: ...
    def __len__(self) -> int: ...


@dataclass(frozen=True)
class ReportRequest:
    path: Path
    day: str
    environment: str
    team: str
    orders: list[dict[str, Any]]
    routes: list[dict[str, Any]]
    markets: list[dict[str, Any]]
    audit_path: Path
    audit_size: int
    created: str
    calendar_source: str
    journal_health: str = ""


def kst_time(value: str) -> str:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("감사 시각의 시간대를 확인할 수 없습니다")
    return instant.astimezone(DESK_TIMEZONE).isoformat(timespec="microseconds")


def read_ack_audit(request: ReportRequest) -> tuple[list[dict[str, Any]], list[str]]:
    """내보내기 접수 당시의 파일 크기까지만 읽어 미래 기록의 혼입을 막는다."""
    result: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not request.audit_path.exists():
        return result, ["Ack 감사 파일 없음 · 해당 날짜의 실행 여부 별도 대조 필요"]
    remaining = request.audit_size
    malformed = 0
    with request.audit_path.open("rb") as stream:
        while remaining > 0:
            line = stream.readline(remaining)
            if not line:
                break
            remaining -= len(line)
            if not line.endswith(b"\n"):
                malformed += 1
                continue
            try:
                item = json.loads(line)
                day = kst_time(item["utc"])[:10]
                if (
                    day == request.day
                    and item.get("environment") == request.environment
                    and str(item.get("team", "")).upper() == request.team.upper()
                ):
                    result.append(item)
            except (ValueError, KeyError, TypeError, UnicodeError):
                malformed += 1
    if malformed:
        warnings.append(
            f"해석 불가 또는 작성 중인 Ack 감사 행 {malformed}개 · 원본 대조 필요"
        )
    if remaining:
        warnings.append("Ack 감사 파일 크기가 변경됨 · 원본 대조 필요")
    return sorted(
        result, key=lambda item: datetime.fromisoformat(item["utc"]).timestamp()
    ), warnings


def _xml_text(value: Any) -> str:
    text = str(value if value is not None else "")
    return "".join(
        char
        if char in "\t\r\n"
        or 32 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
        else "�"
        for char in text
    )


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _write_sheet(
    archive: ZipFile, index: int, headers: list[str], rows: Iterable[list[Any]]
) -> None:
    """긴 원문은 연속 행으로 나누고 모든 문자열을 수식이 아닌 값으로 저장한다."""
    with archive.open(f"xl/worksheets/sheet{index}.xml", "w") as stream:

        def write(text: str) -> None:
            stream.write(text.encode("utf-8"))

        write(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultColWidth="22"/><sheetData>'
        )
        row_number = 0
        for source in [headers + ["내용 분할"]]:
            write('<row r="1">')
            for column, value in enumerate(source, 1):
                write(
                    f'<c r="{_column(column)}1" s="1" t="inlineStr">'
                    f"<is><t>{escape(value)}</t></is></c>"
                )
            write("</row>")
            row_number = 1
        for source in rows:
            texts = [_xml_text(value) for value in source]
            parts = max([1, *(math.ceil(len(value) / 16000) for value in texts)])
            for part in range(parts):
                row_number += 1
                if row_number > 1048576:
                    raise ValueError(
                        "Excel 최대 행 수 초과: 날짜·원본 기록을 나누어 확인하십시오"
                    )
                write(f'<row r="{row_number}">')
                for column, value in enumerate(
                    [*texts, f"{part + 1}/{parts}" if parts > 1 else ""], 1
                ):
                    original = source[column - 1] if column <= len(source) else None
                    if (
                        type(original) in (int, float)
                        and abs(original) < 10**15
                        and math.isfinite(original)
                    ):
                        write(
                            f'<c r="{_column(column)}{row_number}" t="n">'
                            f"<v>{original}</v></c>"
                        )
                        continue
                    fragment = (
                        value[part * 16000 : (part + 1) * 16000]
                        if len(value) > 16000
                        else value
                    )
                    write(
                        f'<c r="{_column(column)}{row_number}" t="inlineStr"><is>'
                        f'<t xml:space="preserve">{escape(fragment)}</t></is></c>'
                    )
                write("</row>")
        write(
            "</sheetData><autoFilter "
            f'ref="A1:{_column(len(headers) + 1)}{row_number}"/></worksheet>'
        )


def _write_metadata(archive: ZipFile, sheets: list[Sheet]) -> None:
    """워크북 관계와 공통 서식을 XML 요소로 구성한다."""
    base = "http://schemas.openxmlformats.org"
    relation = base + "/officeDocument/2006/relationships/"
    content = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    sub = ET.SubElement

    def save(name: str, root: ET.Element) -> None:
        archive.writestr(
            name, ET.tostring(root, encoding="utf-8", xml_declaration=True)
        )

    types = ET.Element("Types", {"xmlns": base + "/package/2006/content-types"})
    sub(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    sub(types, "Default", Extension="xml", ContentType="application/xml")
    for path, kind in [
        ("/xl/workbook.xml", "sheet.main+xml"),
        ("/xl/styles.xml", "styles+xml"),
    ]:
        sub(types, "Override", PartName=path, ContentType=content + kind)
    for index in range(1, len(sheets) + 1):
        sub(
            types,
            "Override",
            PartName=f"/xl/worksheets/sheet{index}.xml",
            ContentType=content + "worksheet+xml",
        )
    save("[Content_Types].xml", types)
    root_links = ET.Element(
        "Relationships", {"xmlns": base + "/package/2006/relationships"}
    )
    sub(
        root_links,
        "Relationship",
        Id="rId1",
        Type=relation + "officeDocument",
        Target="xl/workbook.xml",
    )
    save("_rels/.rels", root_links)
    book = ET.Element(
        "workbook",
        {"xmlns": base + "/spreadsheetml/2006/main", "xmlns:r": relation[:-1]},
    )
    names = sub(book, "sheets")
    links = ET.Element("Relationships", {"xmlns": base + "/package/2006/relationships"})
    for index, (name, _, _) in enumerate(sheets, 1):
        sub(
            names, "sheet", {"name": name, "sheetId": str(index), "r:id": f"rId{index}"}
        )
        sub(
            links,
            "Relationship",
            Id=f"rId{index}",
            Type=relation + "worksheet",
            Target=f"worksheets/sheet{index}.xml",
        )
    sub(
        links,
        "Relationship",
        Id="rIdStyles",
        Type=relation + "styles",
        Target="styles.xml",
    )
    save("xl/workbook.xml", book)
    save("xl/_rels/workbook.xml.rels", links)
    styles = ET.Element("styleSheet", {"xmlns": base + "/spreadsheetml/2006/main"})
    fonts = sub(styles, "fonts", count="2")
    for bold in (False, True):
        font = sub(fonts, "font")
        sub(font, "sz", val="10")
        sub(font, "name", val="맑은 고딕")
        if bold:
            sub(font, "b")
            sub(font, "color", rgb="FFFFCC00")
    fills = sub(styles, "fills", count="3")
    for pattern in ("none", "gray125", "solid"):
        fill = sub(sub(fills, "fill"), "patternFill", patternType=pattern)
        if pattern == "solid":
            sub(fill, "fgColor", rgb="FF1E1E1E")
    sub(sub(styles, "borders", count="1"), "border")
    normal = {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"}
    sub(sub(styles, "cellStyleXfs", count="1"), "xf", normal)
    formats = sub(styles, "cellXfs", count="2")
    sub(
        sub(formats, "xf", {**normal, "xfId": "0"}),
        "alignment",
        vertical="top",
        wrapText="1",
    )
    sub(
        formats,
        "xf",
        {
            **normal,
            "xfId": "0",
            "fontId": "1",
            "fillId": "2",
            "applyFont": "1",
            "applyFill": "1",
        },
    )
    sub(
        sub(styles, "cellStyles", count="1"),
        "cellStyle",
        name="Normal",
        xfId="0",
        builtinId="0",
    )
    save("xl/styles.xml", styles)


def write_xlsx(path: Path, sheets: list[Sheet]) -> None:
    """같은 폴더의 임시 파일을 완성한 뒤 최종 파일로 교체한다."""
    if path.suffix.lower() != ".xlsx":
        raise ValueError("보고서 확장자는 .xlsx여야 합니다")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix="emsx_report_", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            _write_metadata(archive, sheets)
            for index, (_, headers, rows) in enumerate(sheets, 1):
                _write_sheet(archive, index, headers, rows)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_daily(request: ReportRequest, events: EventRows) -> dict[str, Any]:
    audits, warnings = read_ack_audit(request)
    if request.journal_health:
        warnings.append(request.journal_health)
    summary = [
        ["기준 날짜(KST)", request.day],
        ["환경", request.environment],
        ["구독 팀", request.team],
        ["요청 시각(KST)", kst_time(request.created)],
        ["시장 일정 출처", request.calendar_source],
        ["일지 건수", len(events)],
        ["Ack 감사 건수", len(audits)],
        ["관측 범위", "이 기능을 실행한 동안 관측·저장한 기록 · 화면 검색 필터와 무관"],
        [
            "내용 분할",
            "16,000자 초과 내용은 동일 기록의 연속 행으로 분할 · 분할 순서대로 결합",
        ],
        [
            "확인 사항",
            " / ".join(warnings) or "기록 해석 오류 없음 · EMSX 원장과 별도 대조",
        ],
    ]
    order_keys = [
        "seq",
        "account",
        "ticker",
        "side",
        "qty",
        "filled",
        "remain",
        "line_status",
        "instruction",
        "iso_country",
        "market_code",
        "market_status",
        "market_warning",
        "create_time",
        "basket_name",
        "warning",
        "dividend_ex_date",
    ]
    route_keys = [
        "seq",
        "route_id",
        "account",
        "ticker",
        "broker",
        "qty",
        "filled",
        "line_status",
        "instruction",
        "market_code",
        "market_status",
        "market_warning",
    ]

    def event_rows() -> Iterator[list[Any]]:
        for item in events:
            yield [
                item["id"],
                kst_time(item["utc"]),
                item["environment"],
                item["team"],
                item["kind"],
                item["sequence"],
                item["payload"],
            ]

    def notes() -> Iterator[list[Any]]:
        note_reader = getattr(events, "notes", None)
        source = note_reader() if callable(note_reader) else events
        for item in source:
            if item["kind"] == "메모":
                payload = json.loads(item["payload"])
                yield [
                    item["id"],
                    kst_time(item["utc"]),
                    payload.get("author", ""),
                    item["sequence"],
                    payload.get("account", ""),
                    payload.get("ticker", ""),
                    payload.get("note", ""),
                ]

    audit_rows = [
        [
            kst_time(item["utc"]),
            item.get("type", ""),
            item.get("environment", ""),
            item.get("team", ""),
            json.dumps(
                item.get("sequences", [item.get("sequence")]), ensure_ascii=False
            ),
            item.get("batch_id", ""),
            item.get("correlation_id", ""),
            item.get("detail", ""),
        ]
        for item in audits
    ]
    sheets = [
        ("요약", ["항목", "내용"], summary),
        (
            "Order 현황",
            [
                "Order",
                "Account",
                "Ticker",
                "Side",
                "Shares",
                "Fill",
                "Remain",
                "Line Status",
                "Instruction",
                "ISO Country",
                "Market",
                "장 상태",
                "휴장 확인",
                "Create Time",
                "Basket Name",
                "Alerts",
                "Dividend Ex Date",
            ],
            [[row.get(key, "") for key in order_keys] for row in request.orders],
        ),
        (
            "Route 현황",
            [
                "Order",
                "Route",
                "Account",
                "Ticker",
                "Broker",
                "Shares",
                "Fill",
                "Line Status",
                "Instruction",
                "Market",
                "장 상태",
                "휴장 확인",
            ],
            [[row.get(key, "") for key in route_keys] for row in request.routes],
        ),
        (
            "Event Log",
            ["기록 ID", "관측 시각(KST)", "환경", "팀", "유형", "Order", "원본 내용"],
            event_rows(),
        ),
        (
            "자동 Ack",
            [
                "감사 시각(KST)",
                "종류",
                "환경",
                "팀",
                "Order 목록",
                "배치 ID",
                "상관 ID",
                "내용",
            ],
            audit_rows,
        ),
        (
            "트레이더 메모",
            ["기록 ID", "시각(KST)", "작성자", "Order", "Account", "Ticker", "메모"],
            notes(),
        ),
        (
            "시장 상태",
            [
                "국가",
                "거래소",
                "상태",
                "현지 시각",
                "KST",
                "정규장·확인 사항",
                "향후 휴장",
            ],
            [
                [
                    row.get(key, "")
                    for key in (
                        "code",
                        "exchange",
                        "state",
                        "local_time",
                        "kst_time",
                        "detail",
                        "upcoming_holiday",
                    )
                ]
                for row in request.markets
            ],
        ),
    ]
    write_xlsx(request.path, sheets)
    return {
        "path": str(request.path),
        "events": len(events),
        "audits": len(audits),
        "warnings": warnings,
    }
