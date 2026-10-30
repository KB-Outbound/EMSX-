"""거래 표 중심 화면과 필요할 때 여는 운영 설정을 구성한다."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from desk_tables import PinnedTreeview, ROUTE_COLUMNS, ROUTE_PRIMARY_COLUMNS
from kb_theme import BACKGROUND, TEXT, configure_rows, apply_theme


class DeskViews(ttk.Frame):
    """탭 막대 없이 하나의 작업 화면만 표시한다."""

    def __init__(self, parent: tk.Misc, selected: tk.StringVar) -> None:
        super().__init__(parent)
        self._pages: dict[str, tuple[ttk.Frame, str]] = {}
        self._active = ""
        self._selected = selected
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    def add(self, child: ttk.Frame, text: str) -> None:
        key = str(child)
        self._pages[key] = (child, text)
        child.grid(row=0, column=0, sticky="nsew")
        child.grid_remove()
        if not self._active:
            self.select(key)

    def tabs(self) -> tuple[str, ...]:
        return tuple(self._pages)

    def tab(self, key: str, option: str) -> str:
        if option != "text":
            raise ValueError("지원하지 않는 화면 속성입니다")
        return self._pages[str(key)][1]

    def select(self, key: str | None = None) -> str:
        if key is None:
            return self._active
        key = str(key)
        page, label = self._pages[key]
        if self._active:
            self._pages[self._active][0].grid_remove()
        page.grid()
        self._active = key
        self._selected.set("자동 Ack" if label == "자동 수신확인(Ack)" else label)
        return key


class DeskLayoutMixin:
    def _make_ui(self) -> None:
        apply_theme(self.root)
        self.workspace = ttk.Frame(self.root, padding=8)
        self.workspace.pack(fill="both", expand=True)
        self.connection_state = tk.StringVar(value="● 대기")
        self.visible_counts = tk.StringVar(value="Route 0 · Unrouted 0")
        self.risk_counts = tk.StringVar(value="Alerts 0")
        self.operation_notice = tk.StringVar()
        self.view_name = tk.StringVar(value="Routes")
        self.detail_columns = tk.BooleanVar(value=False)

        self.header = ttk.Frame(self.workspace)
        self.header.pack(fill="x", pady=(0, 6))
        self.header.columnconfigure(0, weight=1)
        ttk.Label(
            self.header, textvariable=self.environment_header, style="Header.TLabel"
        ).grid(row=0, column=0, sticky="w")
        self.status_badge = ttk.Label(
            self.header, textvariable=self.connection_state, style="Connection.TLabel"
        )
        self.status_badge.grid(row=0, column=1, padx=(8, 12))
        self._make_market_panel(self.header)

        self.toolbar = ttk.Frame(self.workspace)
        self.toolbar.pack(fill="x", pady=(0, 6))
        self.start_btn = ttk.Button(
            self.toolbar,
            text="모니터링 시작",
            command=self.start,
            style="Primary.TButton",
        )
        self.start_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttk.Button(
            self.toolbar, text="중지", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(0, 8))
        self.arm_btn = ttk.Button(
            self.toolbar,
            text="자동 Ack 해제",
            command=self._toggle_auto_ack,
            state="disabled",
        )
        self.arm_btn.pack(side="left", padx=(0, 12))
        selector = ttk.Menubutton(self.toolbar, textvariable=self.view_name)
        selector.pack(side="left", padx=(0, 4))
        self.view_menu = tk.Menu(selector, tearoff=False)
        selector.configure(menu=self.view_menu)
        ttk.Button(self.toolbar, text="설정", command=self._show_settings).pack(
            side="left"
        )
        self.report_button = ttk.Button(
            self.toolbar, text="일지 내보내기", command=self.export_daily_report
        )
        self.report_button.pack(side="right")
        ttk.Button(self.toolbar, text="메모", command=self.add_trader_note).pack(
            side="right", padx=4
        )

        self.filters = ttk.Frame(self.workspace)
        self.filters.pack(fill="x", pady=(0, 6))
        for index, (label, variable, width) in enumerate(
            (
                ("Account", self.account_filter, 12),
                ("Ticker", self.search, 16),
                ("Basket Name", self.basket_filter, 16),
            )
        ):
            ttk.Label(self.filters, text=label, style="Muted.TLabel").grid(
                row=0, column=index * 2, padx=(0 if index == 0 else 10, 4)
            )
            ttk.Entry(self.filters, textvariable=variable, width=width).grid(
                row=0, column=index * 2 + 1, sticky="ew"
            )
            self.filters.columnconfigure(index * 2 + 1, weight=1)
        ttk.Combobox(
            self.filters,
            textvariable=self.date_filter,
            values=("오늘·전일", "오늘"),
            width=8,
            state="readonly",
        ).grid(row=0, column=6, padx=(10, 4))
        ttk.Checkbutton(self.filters, text="Alerts", variable=self.only_alerts).grid(
            row=0, column=7
        )
        ttk.Combobox(
            self.filters,
            textvariable=self.order_sort,
            values=("최신순", "과거순", "Basket별 최신순"),
            width=13,
            state="readonly",
        ).grid(row=0, column=8, padx=(4, 0))

        self.nb = DeskViews(self.workspace, self.view_name)
        self.nb.pack(fill="both", expand=True)
        self._make_views()
        for page in self.nb.tabs():
            label = self.nb.tab(page, "text")
            self.view_menu.add_command(
                label="자동 Ack" if label == "자동 수신확인(Ack)" else label,
                command=lambda selected=page: self.nb.select(selected),
            )
        self.view_menu.add_separator()
        self.view_menu.add_checkbutton(
            label="상세 열 표시",
            variable=self.detail_columns,
            command=self._set_detail_columns,
        )
        self.view_menu.add_command(label="현재 화면 CSV 저장", command=self.export)

        self.footer = ttk.Frame(self.workspace)
        self.footer.pack(fill="x", pady=(5, 0))
        ttk.Label(
            self.footer, textvariable=self.visible_counts, style="Muted.TLabel"
        ).pack(side="left")
        self.alert_badge = ttk.Label(
            self.footer, textvariable=self.risk_counts, style="Badge.TLabel"
        )
        self.alert_badge.pack(side="left", padx=14)
        self.notice_button = ttk.Button(
            self.footer,
            textvariable=self.operation_notice,
            command=self._show_connection_details,
        )
        self._make_settings()
        self._last_render = 0.0

    def _make_views(self) -> None:
        for label, columns in (
            ("Routes", ROUTE_COLUMNS),
            (
                "Brokers",
                (
                    ("broker", "Broker", 150),
                    ("routes", "Routes", 100),
                    ("routed", "Routed Qty", 140),
                    ("filled", "Filled Qty", 140),
                    ("remaining", "Remaining", 140),
                    ("fill_pct", "Fill %", 110),
                ),
            ),
        ):
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=label)
            if label == "Routes":
                tree = PinnedTreeview(
                    frame,
                    columns,
                    primary_columns=ROUTE_PRIMARY_COLUMNS,
                    additional_columns=("filled", "remain", "broker", "avg_price"),
                )
                tree.pack(fill="both", expand=True)
            else:
                tree = ttk.Treeview(
                    frame, columns=[col[0] for col in columns], show="headings"
                )
                for key, heading, width in columns:
                    tree.heading(key, text=heading)
                    tree.column(
                        key,
                        width=width,
                        minwidth=80,
                        stretch=True,
                        anchor="w" if key == "broker" else "e",
                    )
                configure_rows(tree)
                scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
                tree.configure(yscrollcommand=scroll.set)
                scroll.pack(side="right", fill="y")
                tree.pack(fill="both", expand=True)
            self.tabs[label] = {"columns": [col[0] for col in columns], "tree": tree}

        ack = ttk.Frame(self.nb)
        self.nb.add(ack, text="자동 수신확인(Ack)")
        ack_columns = (
            ("create_time", "Create Time", 150),
            ("basket_name", "Basket Name", 120),
            ("account", "Account", 180),
            ("ticker", "Ticker", 220),
            ("side", "Side", 48),
            ("qty", "Shares", 82),
            ("line_status", "Line Status", 90),
            ("instruction", "Instruction", 118),
            ("ack_state", "Ack 처리 상태", 168),
            ("ack_detail", "사유 / 응답", 300),
        )
        self.ack_tree = PinnedTreeview(
            ack,
            ack_columns,
            primary_columns=(
                "account",
                "ticker",
                "basket_name",
                "side",
                "qty",
                "instruction",
                "ack_state",
            ),
        )
        self.ack_tree.pack(fill="both", expand=True)
        log_frame = ttk.Frame(self.nb)
        self.nb.add(log_frame, text="Event Log")
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            background=BACKGROUND,
            foreground=TEXT,
            insertbackground=TEXT,
            selectbackground="#353B42",
            relief="flat",
            font=("Consolas", 9),
        )
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _make_settings(self) -> None:
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.withdraw()
        self.settings_window.title("운영 설정")
        self.settings_window.transient(self.root)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.settings_window.withdraw)
        body = ttk.Frame(self.settings_window, padding=16)
        body.pack(fill="both", expand=True)
        self.env_ctrl = ttk.Combobox(
            body,
            textvariable=self.env,
            values=("DEMO", "BETA", "PROD"),
            width=18,
            state="readonly",
        )
        self.team_ctrl = ttk.Entry(body, textvariable=self.team, width=22)
        fields = (
            ("실행 환경", self.env_ctrl),
            ("구독 팀", self.team_ctrl),
            ("Broker", ttk.Entry(body, textvariable=self.broker_filter, width=22)),
            (
                "Route 지연 (분)",
                ttk.Entry(body, textvariable=self.unrouted_min, width=8),
            ),
            (
                "No-Fill 정체 (분)",
                ttk.Entry(body, textvariable=self.nofill_min, width=8),
            ),
            (
                "ADV 기간",
                ttk.Combobox(
                    body,
                    textvariable=self.adv_period,
                    values=("20D", "5D"),
                    state="readonly",
                    width=8,
                ),
            ),
        )
        for row, (label, widget) in enumerate(fields):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 24), pady=6
            )
            widget.grid(row=row, column=1, sticky="w", pady=6)
        ttk.Checkbutton(body, text="알림음", variable=self.sound_enabled).grid(
            row=6, column=0, sticky="w"
        )
        ttk.Button(body, text="알림음 확인", command=self._test_alarm).grid(
            row=6, column=1, sticky="w", pady=6
        )
        for row, (label, command) in enumerate(
            (
                ("연결·기록 상태", self._show_connection_details),
                ("자동 Ack 조건", self.show_arm_diagnostics),
                ("설정 파일 위치", self.show_config),
            ),
            start=7,
        ):
            ttk.Button(body, text=label, command=command).grid(
                row=row, column=0, columnspan=2, sticky="ew", pady=3
            )
        self.disarm_btn = ttk.Button(
            body, text="자동 Ack 해제", command=self.disarm_ack, state="disabled"
        )
        self.disarm_btn.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _show_settings(self) -> None:
        self.settings_window.deiconify()
        self.settings_window.lift()

    def _show_connection_details(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "연결·기록 상태",
            "\n\n".join(
                variable.get()
                for variable in (
                    self.status,
                    self.feed_state,
                    self.feed_warning,
                    self.utility_state,
                    self.arm_state,
                )
                if variable.get()
            ),
            parent=self.root,
        )

    def _toggle_auto_ack(self) -> None:
        if self.armed:
            self.disarm_ack()
            self.arm_btn.configure(text="자동 Ack 해제", style="TButton")
        else:
            if self.arm_state.get().startswith("활성화 요청"):
                return
            self.arm_ack()
            if self.arm_state.get().startswith("활성화 요청"):
                self.arm_btn.configure(text="자동 Ack 대기", state="disabled")

    def _set_detail_columns(self) -> None:
        enabled = self.detail_columns.get()
        self.tabs["Routes"]["tree"].set_detail_columns(enabled)
        self.ack_tree.set_detail_columns(enabled)

    def _refresh_desk_status(
        self,
        rows: list[dict[str, Any]],
        routes: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        if not hasattr(self, "connection_state"):
            return
        running = self.worker is not None and self.worker.is_alive()
        broken = bool(self.book.stream_error or self.book.event_gaps)
        if broken:
            state = "● 수신 오류"
        elif not self.monitoring:
            state = "● 중지"
        elif not running:
            state = "● 연결 끊김"
            broken = True
        elif self.book.stream_healthy():
            state = "● DEMO" if self.env.get() == "DEMO" else "● 실시간"
        else:
            state = "● 연결 중"
        self.connection_state.set(state)
        self.status_badge.configure(
            style="Error.Connection.TLabel" if broken else "Connection.TLabel"
        )
        pending = (
            self.arm_state.get().startswith("활성화 요청")
            and self.monitoring
            and running
            and not broken
        )
        self.arm_btn.configure(
            text="자동 Ack 활성"
            if self.armed
            else "자동 Ack 대기"
            if pending
            else "자동 Ack 해제",
            state="normal"
            if self.armed
            else "disabled"
            if pending
            else self.arm_btn.cget("state"),
            style="Active.TButton" if self.armed else "TButton",
        )
        self.visible_counts.set(
            f"Order {stats['orders']:,}  ·  Route {stats['routes']:,}  ·  "
            f"Unrouted {stats['unrouted']:,}"
        )
        unrouted = sum("Unrouted Warning" in row["alert_types"] for row in rows)
        nofill = sum("No-Fill Alert" in row["alert_types"] for row in rows)
        adv = sum(bool(row.get("adv_check")) for row in routes)
        holidays = sum(bool(row.get("market_warning")) for row in rows)
        other = sum(
            bool(set(row["alert_types"]) - {"Unrouted Warning", "No-Fill Alert"})
            for row in rows
        )
        risks = [
            f"{name} {count:,}"
            for name, count in (
                ("Route 지연", unrouted),
                ("No-Fill", nofill),
                ("ADV", adv),
                ("휴장", holidays),
                ("Alerts", other),
            )
            if count
        ]
        self.risk_counts.set("  ·  ".join(risks) if risks else "Alerts 0")
        self.alert_badge.configure(
            style="Risk.Badge.TLabel" if risks else "Badge.TLabel"
        )
        problem = "일지 기록 오류" if self.journal.error else ""
        self.operation_notice.set(problem)
        if problem:
            self.notice_button.pack(side="right")
        else:
            self.notice_button.pack_forget()
