"""시장 상태·일지·메모·일일 보고서를 메인 화면에 연결한다."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any

from daily_report import ReportRequest
from diagnostics import LOGGER
from desk_journal import DeskJournal
from kb_theme import SURFACE, GOLD, MUTED
from market_sessions import MarketCalendar, MarketStatus, STATE_LABELS, STATE_COLORS
from order_fields import DESK_TIMEZONE, desk_today, recent_order

MARKET_CODES = (
    "US",
    "HK",
    "JP",
    "CN",
    "SG",
    "AU",
    "DE",
    "CH",
    "ES",
    "FR",
    "FI",
    "GB",
    "ID",
    "IE",
    "IL",
    "IN",
    "IT",
    "MY",
    "NL",
    "NZ",
    "PH",
    "PT",
    "SE",
    "TH",
    "TW",
    "VN",
)


class OperationsMixin:
    def _init_operations(self, directory: Path) -> None:
        self.operations_directory = directory
        self.utility_messages: queue.Queue = queue.Queue()
        self.journal = DeskJournal(
            directory / "desk_journal.sqlite3", self.utility_messages
        )
        self.market_calendar: MarketCalendar | None = None
        self.market_statuses: dict[str, MarketStatus] = {}
        self.market_loader: threading.Thread | None = None
        self._market_tick = 0.0
        self._calendar_reload = 0.0
        self._journal_message = "일지 준비 중"
        self._calendar_message = "캘린더 확인 중"
        self._seen_operational_alerts: dict[tuple, dict[str, Any]] = {}
        self._closing = False
        self._journal_close_requested = False

    def _make_market_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(
            parent, text="주요 시장 · 정규장 참고 상태", padding=(6, 3)
        )
        panel.pack(fill="x", pady=(6, 2))
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        self.market_clock = tk.StringVar(value="KST 시각 확인 중")
        ttk.Label(controls, textvariable=self.market_clock, foreground=GOLD).pack(
            side="left"
        )
        self.market_selection = tk.StringVar(value="US")
        ttk.Combobox(
            controls,
            textvariable=self.market_selection,
            values=MARKET_CODES,
            width=5,
            state="readonly",
        ).pack(side="left", padx=6)
        self.report_button = ttk.Button(
            controls, text="당일 일지/로그 내보내기", command=self.export_daily_report
        )
        self.report_button.pack(side="right")
        ttk.Button(controls, text="메모 작성", command=self.add_trader_note).pack(
            side="right", padx=4
        )
        ttk.Button(
            controls, text="캘린더 새로고침", command=self._load_market_calendar
        ).pack(side="right", padx=4)
        badges = ttk.Frame(panel)
        badges.pack(fill="x", pady=3)
        self.market_badges: dict[str, tk.Label] = {}
        for index, code in enumerate(MARKET_CODES):
            label = tk.Label(
                badges,
                text=f"● {code} 확인 --:--",
                bg=SURFACE,
                fg=MUTED,
                font=("맑은 고딕", 9),
                padx=3,
                pady=2,
                cursor="hand2",
                anchor="w",
            )
            label.grid(row=index // 9, column=index % 9, sticky="ew", padx=1, pady=1)
            label.bind(
                "<Button-1>",
                lambda event, selected=code: self.market_selection.set(selected),
            )
            badges.columnconfigure(index % 9, weight=1)
            self.market_badges[code] = label
        self.market_detail = tk.StringVar()
        ttk.Label(
            panel,
            textvariable=self.market_detail,
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w")
        self.utility_state = tk.StringVar(value="일지·캘린더 준비 중")
        ttk.Label(
            panel,
            textvariable=self.utility_state,
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w")

    def _load_market_calendar(self) -> None:
        if self.market_loader and self.market_loader.is_alive():
            return
        self._calendar_reload = time.monotonic()
        output = self.utility_messages
        override = self.operations_directory / "market_calendar_overrides.json"

        def load() -> None:
            try:
                calendar = MarketCalendar.load(override)
                output.put({"type": "market_ready", "calendar": calendar})
            except Exception as exc:
                output.put({"type": "market_error", "message": str(exc)})

        self.market_loader = threading.Thread(
            target=load, name="시장 캘린더", daemon=True
        )
        self.market_loader.start()

    def _poll_utilities(self) -> None:
        if not hasattr(self, "utility_messages"):
            return
        for _ in range(20):
            try:
                message = self.utility_messages.get_nowait()
            except queue.Empty:
                break
            kind = message["type"]
            if kind == "market_ready":
                self.market_calendar = message["calendar"]
                self._calendar_message = (
                    "로컬 캘린더 · 대표시장 참고 · 장전=정규장 전 60분 준비 구간"
                )
                self._market_tick = 0.0
            elif kind == "market_error":
                self.market_calendar = None
                self.market_statuses.clear()
                self._market_tick = 0.0
                self._calendar_message = "캘린더 오류 · 휴장 여부 확인 필요"
                self._append("시장 캘린더 오류: " + message["message"])
            elif kind == "journal_ready":
                self._journal_message = "일지 기록 중"
            elif kind == "journal_error":
                self._journal_message = "일지 기록 오류 · 누락 여부 확인 필요"
                self._append(message["message"])
            elif kind in {"report_done", "report_error"}:
                self.report_button.configure(state="normal")
                if kind == "report_done":
                    self._append(f"당일 보고서 저장 완료: {Path(message['path']).name}")
                    if not self._closing:
                        text = (
                            f"일지 {message['events']}건 · "
                            f"Ack 감사 {message['audits']}건\n" + message["path"]
                        )
                        if message["warnings"]:
                            warning = text + "\n\n" + "\n".join(message["warnings"])
                            self.root.after_idle(
                                lambda value=warning: messagebox.showwarning(
                                    "보고서 저장 · 확인 사항 있음", value
                                )
                            )
                        else:
                            self.root.after_idle(
                                lambda value=text: messagebox.showinfo(
                                    "당일 보고서 저장 완료", value
                                )
                            )
                elif not self._closing:
                    self.root.after_idle(
                        lambda value=message["message"]: messagebox.showerror(
                            "당일 보고서 저장 오류", value
                        )
                    )
        self._update_market_panel()

    def _update_market_panel(self, force: bool = False) -> None:
        if not hasattr(self, "market_badges"):
            return
        tick = time.monotonic()
        if not force and tick - self._market_tick < 1:
            return
        self._market_tick = tick
        now = datetime.now(timezone.utc)
        self.market_clock.set(
            now.astimezone(DESK_TIMEZONE).strftime("KST %Y-%m-%d %H:%M:%S")
        )
        calendar = self.market_calendar
        if calendar is not None:
            try:
                self.market_statuses = {
                    code: calendar.status(code, now) for code in MARKET_CODES
                }
            except Exception:
                LOGGER.exception("시장 상태 계산 실패")
                self.market_calendar = None
                self.market_statuses.clear()
                self._calendar_message = "시장 상태 계산 오류 · 일정 확인 필요"
        for code, badge in self.market_badges.items():
            status = self.market_statuses.get(code)
            state = status.state if status else "Unknown"
            clock = (
                status.local_time[11:16] if status and status.local_time else "--:--"
            )
            badge.configure(
                text=f"● {code} {STATE_LABELS[state]} {clock}", fg=STATE_COLORS[state]
            )
        selected = self.market_statuses.get(self.market_selection.get())
        if selected:
            self.market_detail.set(
                f"{selected.code} {selected.exchange} · 현지 {selected.local_time} · "
                f"{selected.detail}"
                + (
                    f" · 다음 휴장 {selected.upcoming_holiday}"
                    if selected.upcoming_holiday
                    else ""
                )
            )
        else:
            self.market_detail.set("거래소별 휴장 일정·현지 시각 확인 필요")
        self.utility_state.set(self._journal_message + " · " + self._calendar_message)
        if not self._closing and tick - self._calendar_reload >= 300:
            self._load_market_calendar()

    def _journal_event(self, kind: str, payload: dict[str, Any]) -> bool:
        journal = getattr(self, "journal", None)
        if journal is None:
            return False
        return journal.record(kind, self.env.get(), self.team.get().strip(), payload)

    def _annotate_market_rows(
        self, rows: list[dict[str, Any]], routes: list[dict[str, Any]]
    ) -> None:
        if not hasattr(self, "market_calendar"):
            return
        for row in [*rows, *routes]:
            if self.market_calendar is not None:
                row.update(self.market_calendar.order_market(row, self.market_statuses))
            else:
                row.update(
                    market_code=row.get("iso_country", ""),
                    market_status="확인 필요",
                    market_warning="",
                    market_basis="미확인",
                    market_exchange="",
                )

    def _track_operational_events(self, rows: list[dict[str, Any]]) -> None:
        if not hasattr(self, "_seen_operational_alerts") or self._closing:
            return
        current: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            kinds = [*row.get("alert_types", ())]
            if row.get("market_warning"):
                kinds.append(row["market_warning"])
            for kind in kinds:
                key = (row["seq"], kind)
                current[key] = {
                    "sequence": row["seq"],
                    "alert": kind,
                    "account": row["account"],
                    "ticker": row["ticker"],
                    "instruction": row["instruction"],
                    "iso_country": row["iso_country"],
                }
                if key not in self._seen_operational_alerts:
                    self._journal_event("Alert 표시 시작", current[key])
        for key, previous in self._seen_operational_alerts.items():
            if key not in current:
                self._journal_event("Alert 표시 해제", previous)
        self._seen_operational_alerts = current

    def add_trader_note(self) -> None:
        selected = None
        tab = self.nb.tab(self.nb.select(), "text")
        if tab == "Routes":
            choice = self.tabs[tab]["tree"].selection()
            if choice:
                try:
                    sequence = int(choice[0].split(":")[0])
                    selected = next(
                        (
                            row
                            for row in self.book.full_view()
                            if row["seq"] == sequence
                        ),
                        None,
                    )
                except ValueError:
                    pass
        dialog = tk.Toplevel(self.root)
        dialog.title("트레이더 메모")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                f"Order {selected['seq']} · {selected['ticker']}"
                if selected
                else "데스크 공통 메모"
            ),
        ).pack(anchor="w")
        ttk.Label(frame, text="작성자").pack(anchor="w", pady=(8, 0))
        author = tk.StringVar(value=os.getenv("USERNAME", ""))
        ttk.Entry(frame, textvariable=author).pack(fill="x")
        text = tk.Text(
            frame, width=72, height=10, bg=SURFACE, fg="white", insertbackground=GOLD
        )
        text.pack(fill="both", expand=True, pady=8)

        def save() -> None:
            note = text.get("1.0", "end-1c").strip()
            if not note or not author.get().strip():
                messagebox.showerror(
                    "메모 확인", "작성자와 메모 내용을 입력하십시오", parent=dialog
                )
                return
            payload = {
                "note": note,
                "author": author.get().strip(),
                "sequence": selected["seq"] if selected else None,
                "account": selected["account"] if selected else "",
                "ticker": selected["ticker"] if selected else "",
            }
            if self._journal_event("메모", payload):
                self._append("트레이더 메모 저장 접수 · " + author.get().strip())
                dialog.destroy()
            else:
                messagebox.showerror(
                    "메모 저장 오류",
                    "일지 상태와 저장 공간을 확인하십시오",
                    parent=dialog,
                )

        ttk.Button(frame, text="메모 저장", command=save, style="Primary.TButton").pack(
            anchor="e"
        )

    def export_daily_report(self) -> None:
        path = filedialog.asksaveasfilename(
            title="당일 일지/로그 내보내기",
            defaultextension=".xlsx",
            filetypes=[("Excel 보고서", "*.xlsx")],
            initialfile=f"EMSX_{self.env.get()}_당일일지_{desk_today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        instant = datetime.now(timezone.utc)
        snapshot_day = instant.astimezone(DESK_TIMEZONE).date()
        self._update_market_panel(force=True)
        a, b = self._thresholds()
        orders = [
            row
            for row in self.book.full_view(a, b)
            if recent_order({"EMSX_DATE": row["trade_date"]}, today=snapshot_day)
        ]
        identifiers = {row["seq"] for row in orders}
        routes = [row for row in self.book.route_view() if row["seq"] in identifiers]
        self._annotate_market_rows(orders, routes)
        audit = self.operations_directory / "ack_audit.jsonl"
        try:
            size = audit.stat().st_size if audit.exists() else 0
            request = ReportRequest(
                Path(path),
                str(snapshot_day),
                self.env.get(),
                self.team.get().strip(),
                orders,
                routes,
                [asdict(status) for status in self.market_statuses.values()],
                audit,
                size,
                instant.isoformat(),
                self.market_calendar.source
                if self.market_calendar
                else "캘린더 미확인",
                self.journal.error,
            )
            if not self.journal.request_report(request):
                raise RuntimeError(
                    "보고서를 만들 수 없습니다. 일지 상태를 확인하십시오"
                )
            self.report_button.configure(state="disabled")
            self._append("당일 보고서 생성 접수")
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("당일 보고서 저장 오류", str(exc))
