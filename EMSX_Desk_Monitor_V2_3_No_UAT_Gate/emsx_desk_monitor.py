"""KB증권 EMSX 모니터 화면과 안전한 작업자 수명 주기를 관리한다."""

from __future__ import annotations
import argparse
import sys
import csv
import os
from datetime import datetime
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from bloomberg_feed import BloombergWorker
from demo_feed import DemoWorker
from desk_core import DeskBook, line_status
from order_fields import recent_order, sort_orders, client_instruction, creation_fields
from alerting import AlertTracker, play_alarm
from risk_rules import format_adv
from resource_paths import asset_path
from ack_engine import load_config, eligibility, write_authorized

from ui_text import APP_TITLE, DESK_TITLE, localize
from kb_theme import (
    BACKGROUND,
    TEXT,
    GOLD,
    apply_theme,
    configure_rows,
    row_tags,
)

VERSION = "2.6.8"
APP_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "EMSX_Desk_Monitor_HDGB"
ADMIN_CONFIG = APP_DIR / "admin_config.json"
LEDGER_PATH = APP_DIR / "ack_journal.sqlite3"


class MonitorApp:
    def __init__(self, root: tk.Tk, environment: str = "PROD") -> None:
        self.root = root
        self.root.title(APP_TITLE)
        if sys.platform == "win32" and asset_path("emsx_monitor.ico").is_file():
            try:
                self.root.iconbitmap(default=str(asset_path("emsx_monitor.ico")))
            except tk.TclError:
                pass
        self.root.geometry("1330x790")
        self.root.minsize(1000, 650)
        self.messages = queue.Queue(maxsize=10000)
        self.commands = queue.Queue()
        self.book = DeskBook()
        self.admin_config = load_config(ADMIN_CONFIG)
        self.ack_states = {}
        self.armed = False
        self.arm_state = tk.StringVar(value="자동 수신확인 해제 · 전송 없음")
        self.arm_blocker = tk.StringVar(value="활성화 불가: 모니터링을 시작하십시오")
        self.worker = None
        self.monitoring = False
        self.env = tk.StringVar(value=environment)
        self.environment_header = tk.StringVar(value=f"{environment} | {DESK_TITLE}")
        self.team = tk.StringVar(value=self.admin_config["team_name"])
        self.search = tk.StringVar()
        self.account_filter = tk.StringVar()
        self.broker_filter = tk.StringVar()
        self.only_alerts = tk.BooleanVar(value=False)
        self.basket_filter = tk.StringVar()
        self.date_filter = tk.StringVar(value="오늘·전일")
        self.order_sort = tk.StringVar(value="최신순")
        self.sound_enabled = tk.BooleanVar(value=True)
        self.adv_period = tk.StringVar(
            value=str(self.admin_config.get("adv_period", 20)) + "D"
        )
        self.alert_tracker = AlertTracker()
        self.eligible_ids: set[int] = set()
        self.alert_summary = tk.StringVar(value="Alerts 0")
        self.unrouted_min = tk.StringVar(value="5")
        self.nofill_min = tk.StringVar(value="15")
        self.status = tk.StringVar(value="모니터링 중지 / 자동 수신확인 해제")
        self.feed_warning = tk.StringVar()
        self.unavailable_fields: set[str] = set()
        self.feed_state = tk.StringVar(value="Order: -  |  Route: -  |  초기 수신: -")
        self.counts = tk.StringVar(
            value="Order 0  |  Route 0  |  Unrouted 0  |  Alerts 0"
        )
        self.tabs = {}
        self._make_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(250, self._pump)

    def _make_ui(self) -> None:
        apply_theme(self.root)
        box = ttk.Frame(self.root, padding=12)
        box.pack(fill="both", expand=True)
        h = ttk.Frame(box)
        h.pack(fill="x")
        ttk.Label(h, textvariable=self.environment_header, style="Header.TLabel").pack(
            side="left"
        )
        cfg = ttk.Frame(box)
        cfg.pack(fill="x", pady=(12, 8))
        ttk.Label(cfg, text="실행 환경").pack(side="left")
        self.env_ctrl = ttk.Combobox(
            cfg,
            textvariable=self.env,
            values=["DEMO", "BETA", "PROD"],
            width=8,
            state="readonly",
        )
        self.env_ctrl.pack(side="left", padx=5)
        ttk.Label(cfg, text="구독 팀").pack(side="left", padx=(12, 0))
        self.team_ctrl = ttk.Entry(cfg, textvariable=self.team, width=22)
        self.team_ctrl.pack(side="left", padx=5)
        self.start_btn = ttk.Button(
            cfg, text="모니터링 시작", command=self.start, style="Primary.TButton"
        )
        self.start_btn.pack(side="left", padx=(12, 4))
        self.stop_btn = ttk.Button(
            cfg, text="모니터링 중지", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(cfg, text="현재 탭 CSV 저장", command=self.export).pack(side="right")
        self.status_badge = ttk.Label(
            box, textvariable=self.status, style="Badge.TLabel"
        )
        self.status_badge.pack(anchor="w", pady=(4, 2))
        self.alert_badge = ttk.Label(
            box, textvariable=self.alert_summary, style="Badge.TLabel"
        )
        self.alert_badge.pack(anchor="w", pady=(0, 2))
        ttk.Label(box, textvariable=self.feed_state, style="Muted.TLabel").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Label(box, textvariable=self.feed_warning, style="Muted.TLabel").pack(
            anchor="w"
        )
        ttk.Separator(box).pack(fill="x", pady=8)
        ttk.Label(
            box, textvariable=self.counts, style="Metric.TLabel", wraplength=1000
        ).pack(anchor="w", pady=(0, 8))
        filt = ttk.Frame(box)
        filt.pack(fill="x", pady=(0, 8))
        ttk.Label(filt, text="검색").pack(side="left")
        ttk.Entry(filt, textvariable=self.search, width=24).pack(side="left", padx=5)
        ttk.Label(filt, text="Account 검색").pack(side="left", padx=(10, 0))
        ttk.Entry(filt, textvariable=self.account_filter, width=17).pack(
            side="left", padx=5
        )
        ttk.Label(filt, text="Broker 검색").pack(side="left", padx=(10, 0))
        ttk.Entry(filt, textvariable=self.broker_filter, width=12).pack(
            side="left", padx=5
        )
        ttk.Checkbutton(
            filt, text="Alerts만 표시 (Order)", variable=self.only_alerts
        ).pack(side="left", padx=12)
        scope = ttk.Frame(box)
        scope.pack(fill="x", pady=(0, 8))
        ttk.Label(scope, text="Create Time (Trade Date)").pack(side="left")
        ttk.Combobox(
            scope,
            textvariable=self.date_filter,
            values=["오늘·전일", "오늘"],
            width=10,
            state="readonly",
        ).pack(side="left", padx=6)
        ttk.Label(scope, text="Basket Name 검색").pack(side="left", padx=(12, 0))
        ttk.Entry(scope, textvariable=self.basket_filter, width=22).pack(
            side="left", padx=6
        )
        ttk.Label(scope, text="정렬").pack(side="left", padx=(12, 0))
        ttk.Combobox(
            scope,
            textvariable=self.order_sort,
            values=["최신순", "과거순", "Basket별 최신순"],
            width=17,
            state="readonly",
        ).pack(side="left", padx=6)
        options = ttk.Frame(box)
        options.pack(fill="x", pady=(0, 8))
        ttk.Label(options, text="ADV 기준").pack(side="left", padx=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.adv_period,
            values=["20D", "5D"],
            width=5,
            state="readonly",
        ).pack(side="left", padx=4)
        ttk.Checkbutton(options, text="알림음", variable=self.sound_enabled).pack(
            side="left", padx=8
        )
        ttk.Button(options, text="알림음 확인", command=self._test_alarm).pack(
            side="right"
        )
        ttk.Label(
            options, text="ADV = Route 수량 / 평균 거래량 × 100", style="Muted.TLabel"
        ).pack(side="left", padx=12)
        x = ttk.Frame(box)
        x.pack(fill="x", pady=(0, 6))
        ttk.Label(x, text="FIX 수신 후 라우트 미실행 (Route 지연, 분)").pack(
            side="left"
        )
        ttk.Entry(x, textvariable=self.unrouted_min, width=5).pack(side="left", padx=5)
        ttk.Label(x, text="라우트 완료 후 체결 대기 (No-Fill 정체, 분)").pack(
            side="left", padx=(15, 0)
        )
        ttk.Entry(x, textvariable=self.nofill_min, width=5).pack(side="left", padx=5)
        ttk.Label(
            box,
            text=(
                "※ Route 지연·No-Fill: 지정 12개 발행국 ISO 기준 · "
                "MOC 포함 시 No-Fill 제외 · FIX 수신: EMSX 상태 판정 · 관측 시간 기준"
            ),
            foreground=GOLD,
            wraplength=950,
        ).pack(anchor="w", pady=(0, 6))
        nb = ttk.Notebook(box)
        nb.pack(fill="both", expand=True)
        spec = {
            "Orders": [
                ("create_time", "Create Time", 165),
                ("basket_name", "Basket Name", 140),
                ("account", "Account", 148),
                ("ticker", "Ticker", 152),
                ("side", "Side", 60),
                ("qty", "Shares", 95),
                ("filled", "Fill", 95),
                ("remain", "Remain", 95),
                ("fill_pct", "Fill %", 70),
                ("line_status", "Line Status", 110),
                ("instruction", "Instruction", 150),
                ("brokers", "Brokers", 105),
                ("routes", "Routes", 55),
                ("warning", "Alerts", 240),
            ],
            "Routes": [
                ("route_id", "Route#", 66),
                ("create_time", "Create Time", 165),
                ("basket_name", "Basket Name", 140),
                ("account", "Account", 145),
                ("ticker", "Ticker", 135),
                ("broker", "Broker", 80),
                ("side", "Side", 62),
                ("qty", "Route Qty", 90),
                ("filled", "Fill", 90),
                ("remain", "Remain", 90),
                ("line_status", "Line Status", 110),
                ("instruction", "Instruction", 150),
                ("iso_country", "ISO Country", 90),
                ("sec_type", "Sec Type", 120),
                ("adv_pct", "ADV (20D)", 100),
                ("adv_reason", "ADV Check", 190),
                ("avg_price", "Avg Price", 90),
                ("parent_visible", "상위 Order 확인", 98),
            ],
            "Brokers": [
                ("broker", "Broker", 150),
                ("routes", "Routes", 100),
                ("routed", "Routed Qty", 140),
                ("filled", "Filled Qty", 140),
                ("remaining", "Remaining", 140),
                ("fill_pct", "Fill %", 110),
            ],
        }
        for label, columns in spec.items():
            frame = ttk.Frame(nb, padding=3)
            nb.add(frame, text=label)
            tree = ttk.Treeview(
                frame,
                columns=[col[0] for col in columns],
                show="headings",
                selectmode="browse",
            )
            for key, head, width in columns:
                tree.heading(key, text=head)
                tree.column(
                    key,
                    width=width,
                    anchor="e"
                    if key
                    in (
                        "qty",
                        "filled",
                        "remain",
                        "fill_pct",
                        "routed",
                        "remaining",
                        "routes",
                        "route_id",
                        "adv_pct",
                        "avg_price",
                    )
                    else "w",
                    stretch=key in ("account", "ticker", "warning"),
                )
            configure_rows(tree)
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=sb.set, xscrollcommand=horizontal.set)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            tree.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            self.tabs[label] = {"columns": [c[0] for c in columns], "tree": tree}
        ack_frame = ttk.Frame(nb, padding=8)
        nb.add(ack_frame, text="자동 수신확인(Ack)")
        ttk.Label(
            ack_frame,
            text="활성화 이후 새로 수신한 E2E ORD-PEND Order만 자동 수신확인합니다. "
            "기존 Order는 처리하지 않습니다.",
            foreground=GOLD,
            wraplength=1100,
        ).pack(anchor="w")
        h2 = ttk.Frame(ack_frame)
        h2.pack(fill="x", pady=8)
        ttk.Label(h2, textvariable=self.arm_state, style="Badge.TLabel").pack(
            side="left", padx=(0, 18)
        )
        self.arm_btn = ttk.Button(
            h2,
            text="자동 Ack",
            command=self.arm_ack,
            state="disabled",
            style="Primary.TButton",
        )
        self.arm_btn.pack(side="left", padx=4)
        self.disarm_btn = ttk.Button(
            h2, text="자동 수신확인 해제", command=self.disarm_ack, state="disabled"
        )
        self.disarm_btn.pack(side="left", padx=4)
        ttk.Label(
            ack_frame,
            textvariable=self.arm_blocker,
            foreground=GOLD,
            wraplength=1150,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(h2, text="활성화 조건 확인", command=self.show_arm_diagnostics).pack(
            side="right", padx=(0, 12)
        )
        ttk.Button(h2, text="설정 파일 위치", command=self.show_config).pack(
            side="right"
        )
        acols = [
            ("create_time", "Create Time", 165),
            ("basket_name", "Basket Name", 140),
            ("account", "Account", 165),
            ("ticker", "Ticker", 172),
            ("side", "Side", 75),
            ("qty", "Shares", 100),
            ("line_status", "Line Status", 115),
            ("instruction", "Instruction", 150),
            ("ack_state", "Ack 처리 상태", 220),
            ("ack_detail", "사유 / 응답", 300),
        ]
        ack_table = ttk.Frame(ack_frame)
        ack_table.pack(fill="both", expand=True)
        ack_table.rowconfigure(0, weight=1)
        ack_table.columnconfigure(0, weight=1)
        self.ack_tree = ttk.Treeview(
            ack_table, columns=[c[0] for c in acols], show="headings"
        )
        configure_rows(self.ack_tree)
        for col, label, width in acols:
            self.ack_tree.heading(col, text=label)
            self.ack_tree.column(
                col,
                width=width,
                anchor="e" if col == "qty" else "w",
                stretch=col in ("account", "ticker", "ack_detail"),
            )
        ack_scroll = ttk.Scrollbar(
            ack_table, orient="vertical", command=self.ack_tree.yview
        )
        ack_horizontal = ttk.Scrollbar(
            ack_table, orient="horizontal", command=self.ack_tree.xview
        )
        self.ack_tree.configure(
            yscrollcommand=ack_scroll.set, xscrollcommand=ack_horizontal.set
        )
        self.ack_tree.grid(row=0, column=0, sticky="nsew")
        ack_scroll.grid(row=0, column=1, sticky="ns")
        ack_horizontal.grid(row=1, column=0, sticky="ew")
        activity = ttk.Frame(nb, padding=3)
        nb.add(activity, text="Event Log")
        log = tk.Text(
            activity,
            height=12,
            wrap="word",
            font=("맑은 고딕", 10),
            state="disabled",
            background=BACKGROUND,
            foreground=TEXT,
            insertbackground=GOLD,
            selectbackground="#51451E",
            relief="flat",
        )
        log.pack(fill="both", expand=True)
        self.log_text = log
        self.nb = nb
        footer = ttk.Frame(box)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, textvariable=self.environment_header).pack(side="right")
        self._last_render = 0.0

    def _thresholds(self):
        def one(x, default):
            try:
                return max(1, min(1440, int(x)))
            except ValueError:
                return default

        return one(self.unrouted_min.get(), 5), one(self.nofill_min.get(), 15)

    def _append(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert(
            "end", datetime.now().strftime("%H:%M:%S") + " " + line + "\n"
        )
        if int(self.log_text.index("end-1c").split(".")[0]) > 500:
            self.log_text.delete("1.0", "100.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        env = self.env.get()
        team = self.team.get().strip()
        if env == "PROD" and not messagebox.askokcancel(
            "운영 환경 모니터링",
            "PROD는 실제 EMSX 데이터에 연결합니다.\n자동 수신확인은 해제 상태로 시작하며 운영 전송은 기본적으로 차단됩니다.\n모니터링을 시작하시겠습니까?",
        ):
            return
        self.book = DeskBook()
        self.messages = queue.Queue(maxsize=10000)
        self.commands = queue.Queue()
        self.ack_states = {}
        self.alert_tracker = AlertTracker()
        self.armed = False
        self.arm_state.set("자동 수신확인 해제 · 전송 없음")
        try:
            self.admin_config = load_config(ADMIN_CONFIG)
        except Exception as e:
            messagebox.showerror("관리자 설정 오류", str(e))
            return
        if not team:
            messagebox.showerror("구독 팀 입력 필요", "Enter the tested 구독 팀 HDGB.")
            return
        self.worker = (
            DemoWorker(self.messages)
            if env == "DEMO"
            else BloombergWorker(
                env, team, self.messages, self.commands, self.admin_config, LEDGER_PATH
            )
        )
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.env_ctrl.configure(state="disabled")
        self.team_ctrl.configure(state="disabled")
        self.status.set("연결 중 / 자동 수신확인 해제")
        self._append(
            f"버전 {VERSION} / {env} 모니터링 시작 / "
            f"구독 팀={team} / 자동 수신확인 해제"
        )
        self.monitoring = True
        self.worker.start()

    def stop(self) -> None:
        self.monitoring = False
        self.disarm_ack()
        if self.worker:
            self.worker.stop()
        self.status.set("모니터링 종료 중 / 자동 수신확인 해제")

    def close(self) -> None:
        self.monitoring = False
        self.disarm_ack()
        if self.worker:
            self.worker.stop()
        self._finish_close()

    def _finish_close(self) -> None:
        """화면을 막지 않고 작업자의 저널 저장과 세션 정리를 기다린다."""
        if self.worker and self.worker.is_alive():
            self.root.after(50, self._finish_close)
            return
        self.root.destroy()

    def show_config(self) -> None:
        messagebox.showinfo(
            "설정 파일 위치",
            f"관리자 설정 파일:\n{ADMIN_CONFIG}\n진단 로그:\n{APP_DIR / 'feed_diagnostics.log'}\n\n기본값은 전송 차단입니다.\n검증과 전송 권한 확인 후 관리자가 BETA/PROD 전송을 허용해야 합니다.",
        )

    def _arm_blockers(self) -> list[str]:
        # 상태 안내만 수행하며 권한이나 전송 조건을 변경하지 않는다.
        problems = []
        if self.env.get() == "DEMO":
            problems.append("DEMO에서는 자동 수신확인을 사용할 수 없습니다")
        if not self.worker or not self.worker.is_alive():
            problems.append(
                "Bloomberg 연결 작업자가 실행되지 않았습니다. 모니터링 시작 후 Event Log를 확인하십시오"
            )
        for feed, name in [("O", "ORDER"), ("R", "ROUTE")]:
            if self.book.subscriptions[feed] != "STARTED":
                problems.append(
                    f"{name} 구독: {localize(self.book.subscriptions[feed])}"
                )
            if not self.book.snapshot_done[feed]:
                problems.append(f"{name} 초기 수신 미완료")
        if self.book.stream_error:
            problems.append("데이터 수신 오류: " + self.book.stream_error[:240])
        if self.book.event_gaps:
            problems.append("Sequence 단절 (" + str(len(self.book.event_gaps)) + ")")
        return problems

    def show_arm_diagnostics(self) -> None:
        reasons = self._arm_blockers()
        status = (
            "데이터 수신 준비가 완료되었습니다. "
            "자동 수신확인 활성화 시 관리자 전송 허용 여부를 검사합니다."
            if not reasons
            else "\n".join("- " + x for x in reasons)
        )
        messagebox.showinfo("자동 수신확인 활성화 조건", status)

    def arm_ack(self) -> None:
        if not self.worker or not self.worker.is_alive() or self.env.get() == "DEMO":
            messagebox.showerror("연결 필요", "BETA 또는 PROD 연결이 필요합니다.")
            return
        ok, reason = write_authorized(
            self.env.get(), self.team.get().strip(), self.admin_config
        )
        if not ok:
            messagebox.showerror(
                "관리자 설정에 의해 전송 차단",
                f"{localize(reason)}\n\n조회 권한과 SellSideAck 전송 권한은 별도입니다.\n설정 파일: {ADMIN_CONFIG}",
            )
            return
        if not self.book.counters()["stream_ok"]:
            messagebox.showerror(
                "데이터 수신 준비 미완료",
                "Order와 Route의 초기 수신이 완료되고 Sequence 단절이 없어야 합니다.",
            )
            return
        if not messagebox.askyesno(
            "자동 수신확인 전송 확인",
            f"신규 E2E ORD-PEND Order에 실제 SellSideAck 요청을 전송하시겠습니까?\n\n실행 환경: {self.env.get()}\n구독 팀: {self.team.get()}\nAccount 범위: 값이 있는 모든 Account\n생성일 범위: 서울 기준 오늘·전일 (화면 필터와 별도)\n최대 Shares: {self.admin_config['max_shares']}\n\n기존 Order는 처리하지 않습니다. 승인된 전송자 한 대만 실행하십시오.\n실제 Order 상태가 변경됩니다.",
        ):
            return
        self.commands.put({"action": "arm", "armed": True})
        self.arm_state.set("활성화 요청 중 · 응답 대기")

    def disarm_ack(self) -> None:
        self.armed = False
        self.arm_state.set("자동 수신확인 해제 · 신규 전송 차단")
        if self.worker and self.worker.is_alive() and self.env.get() != "DEMO":
            self.commands.put({"action": "arm", "armed": False})

    def _pump(self) -> None:
        deadline = time.monotonic() + 0.02
        try:
            for _ in range(2000):
                if time.monotonic() >= deadline:
                    break
                try:
                    m = self.messages.get_nowait()
                except queue.Empty:
                    break
                typ = m["type"]
                if typ == "reset":
                    self.book = DeskBook()
                    self.alert_tracker = AlertTracker()
                    self.feed_warning.set("")
                    self.unavailable_fields.clear()
                    self.armed = False
                    self.arm_state.set("재연결 / 자동 Ack 해제")
                elif typ == "data":
                    before = len(self.book.activity)
                    self.book.ingest(m["feed"], m["status"], m["fields"])
                    for line in self.book.activity[before:]:
                        self._append(line)
                elif typ == "ack_armed":
                    self.armed = bool(m.get("armed"))
                    self.arm_state.set(
                        (
                            "자동 수신확인 활성화 · 신규 ORD-PEND만 처리"
                            if self.armed
                            else "자동 수신확인 해제 · " + localize(m.get("reason", ""))
                        )
                    )
                    self._append(
                        "자동 수신확인 "
                        + ("활성화" if self.armed else "해제")
                        + ": "
                        + localize(m.get("reason", ""))
                    )
                elif typ == "ack_disarmed":
                    self.armed = False
                    self.arm_state.set("안전장치에 의해 자동 수신확인 해제")
                    self._append("안전 해제: " + localize(m.get("reason", "")))
                elif typ == "ack_batch":
                    result = (
                        localize(m.get("state", "")),
                        localize(m.get("detail", "")),
                    )
                    self.ack_states.update(
                        dict.fromkeys(m.get("sequences", ()), result)
                    )
                    self._append(
                        f"Ack 배치 {m['batch_id'][:12]}: "
                        f"Order {m['count']}개 단일 요청 / "
                        f"{localize(m.get('state', ''))} "
                        f"{localize(m.get('detail', ''))[:120]}"
                    )
                elif typ == "ack_state":
                    seq = m["sequence"]
                    self.ack_states[seq] = (
                        localize(m.get("state", "")),
                        localize(m.get("detail", "")),
                    )
                    if not m.get("batch_id") and m.get("state") in (
                        "SENT",
                        "API_ERROR",
                        "UNKNOWN",
                        "API_RESPONSE_OK_UNVERIFIED",
                    ):
                        self._append(
                            f"Ack #{seq}: {localize(m.get('state', ''))} "
                            f"{localize(m.get('detail', ''))[:120]}"
                        )
                elif typ == "subscription":
                    self.book.subscriptions[m["feed"]] = m["status"]
                    self._append(f"{m['feed']} 구독 {localize(m['status'])}")
                elif typ == "connected":
                    self._append(localize(m["message"]))
                    self.status.set("연결됨 / 초기 데이터 수신 중 / 자동 수신확인 해제")
                elif typ == "reference_unavailable":
                    self.book.invalidate_reference()
                elif typ == "reference":
                    self.book.update_reference(m["security"], m["fields"])
                elif typ == "warning":
                    self._append("수신 경고: " + m["message"])
                elif typ == "diagnostics":
                    self._append("진단 로그 파일: " + m["path"])
                elif typ == "optional_unavailable":
                    self.unavailable_fields.add(m["field"])
                    self.book.clear_optional_field(m["field"])
                    self.feed_warning.set(
                        "선택 필드 미제공: "
                        + ", ".join(sorted(self.unavailable_fields))
                    )
                elif typ == "error":
                    self.book.stream_error = m["message"]
                    self.status.set("데이터 수신 오류 / 자동 수신확인 해제")
                    self.armed = False
                    self._append("오류: " + m["message"])
                elif typ == "ended":
                    self.monitoring = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.env_ctrl.configure(state="readonly")
                    self.team_ctrl.configure(state="normal")
                    if not self.book.stream_error:
                        self.status.set("모니터링 중지 / 자동 수신확인 해제")
                    self.armed = False
                    self.arm_btn.configure(state="disabled")
                    self.disarm_btn.configure(state="disabled")
            self._render()
        finally:
            self.root.after(10 if not self.messages.empty() else 50, self._pump)

    def _update_alarms(self, rows: list[dict]) -> None:
        """검색 필터와 무관하게 날짜 범위 내 경고를 집계하고 알림을 반복한다."""
        unrouted = sum("Unrouted Warning" in row["alert_types"] for row in rows)
        nofill = sum("No-Fill Alert" in row["alert_types"] for row in rows)
        total = sum(bool(row["alert_types"]) for row in rows)
        self.alert_summary.set(
            f"Unrouted Warning {unrouted} | No-Fill Alert {nofill} | Alerts {total}"
        )
        self.alert_badge.configure(
            style=(
                "Danger.Badge.TLabel"
                if total and int(time.monotonic()) % 2 == 0
                else "Badge.TLabel"
            )
        )
        audio_ready = (
            self.sound_enabled.get()
            and self.monitoring
            and self.worker is not None
            and self.worker.is_alive()
            and self.book.stream_healthy()
        )
        if self.alert_tracker.update(rows, audio_ready):
            self._test_alarm()

    def _test_alarm(self) -> None:
        """음향 장치가 차단되어도 시각 경고는 계속 유지한다."""
        try:
            play_alarm(self.root.bell)
        except Exception as exc:
            self._append(f"알림음 재생 실패: {exc}")
        else:
            self._append("알림음 재생 요청 · 시스템 음량과 음소거 설정을 확인하십시오")

    def _render(self) -> None:
        self.environment_header.set(f"{self.env.get()} | {DESK_TITLE}")
        self.status_badge.configure(
            style=(
                "Danger.Badge.TLabel"
                if self.book.stream_error or self.book.event_gaps
                else "Badge.TLabel"
            )
        )
        a, b = self._thresholds()
        all_rows = self.book.full_view(a, b)
        rows = [
            row
            for row in all_rows
            if recent_order(
                {"EMSX_DATE": row["trade_date"]},
                today_only=self.date_filter.get() == "오늘",
            )
        ]
        rows = sort_orders(
            rows,
            basket_first=self.order_sort.get() == "Basket별 최신순",
            oldest_first=self.order_sort.get() == "과거순",
        )
        self.eligible_ids = {row["seq"] for row in rows}
        period = 5 if self.adv_period.get() == "5D" else 20
        self.tabs["Routes"]["tree"].heading("adv_pct", text=f"ADV ({period}D)")
        routes = [
            row
            for row in self.book.route_view(period)
            if row["seq"] in self.eligible_ids
        ]
        routes = sorted(
            routes,
            key=lambda row: (row["create_sort"], row["seq"], row["route_id"]),
            reverse=self.order_sort.get() != "과거순",
        )
        if self.order_sort.get() == "Basket별 최신순":
            routes.sort(key=lambda row: row["basket_name"].casefold())
        stats = self.book.counters(rows, len(routes))
        self._update_alarms(rows)
        missing_dates = sum(not row["trade_date"] for row in all_rows)
        missing_regions = sum(not row["iso_country"] for row in rows)
        self.counts.set(
            f"Order {stats['orders']:,}  |  Route {stats['routes']:,}  |  Unrouted {stats['unrouted']:,}  |  Alerts {sum(bool(r['alert_types']) for r in rows):,}  |  Date Missing {missing_dates:,}  |  Region Missing {missing_regions:,}"
        )
        p = self.book

        def label(feed):
            return f"{localize(p.subscriptions[feed])} / {'초기 수신 완료' if p.snapshot_done[feed] else '초기 수신 중'}"

        warning = (
            f"  |  Sequence 단절 {len(p.event_gaps)}건 (재연결 필요)"
            if p.event_gaps
            else ""
        )
        self.feed_state.set(
            "Order: " + label("O") + "  |  Route: " + label("R") + warning
        )
        if (
            stats["stream_ok"]
            and self.monitoring
            and self.worker
            and self.worker.is_alive()
        ):
            self.status.set(
                "실시간 모니터링 / "
                + ("자동 수신확인 활성화" if self.armed else "자동 수신확인 해제")
            )
        elif p.event_gaps:
            self.status.set("Sequence 단절 / 자동 수신확인 해제")
            self.disarm_ack()
        q = self.search.get().strip().upper()
        account = self.account_filter.get().strip().upper()
        broker = self.broker_filter.get().strip().upper()
        basket = self.basket_filter.get().strip().casefold()
        items = {
            "Orders": rows,
            "Routes": routes,
            "Brokers": self.book.broker_summary(
                [row for row in routes if basket in row["basket_name"].casefold()]
            ),
        }
        for name, data in items.items():
            tree = self.tabs[name]["tree"]
            cols = self.tabs[name]["columns"]
            visible = []
            for x in data:
                if name != "Brokers" and basket not in x["basket_name"].casefold():
                    continue
                if name == "Orders" and self.only_alerts.get() and not x["alert_types"]:
                    continue
                if (
                    account
                    and account not in str(x.get("account", "")).upper()
                    and name != "Brokers"
                ):
                    continue
                broker_str = str(x.get("brokers", x.get("broker", ""))).upper()
                if broker and broker not in broker_str:
                    continue
                if q and q not in " ".join(str(x.get(k, "")) for k in cols).upper():
                    continue
                visible.append(x)
            # 고정된 행 식별자로 대량 Order 표시 시 깜박임을 줄인다.
            stale = set(tree.get_children())
            for idx, item in enumerate(visible):
                cells = [
                    format_adv(item.get(k))
                    if k == "adv_pct"
                    else f"{item.get(k, 0):,.1f}%"
                    if k == "fill_pct"
                    else f"{item[k]:,}"
                    if k in ("qty", "filled", "remain", "routed", "remaining")
                    and isinstance(item.get(k), int)
                    else item.get(k, "")
                    for k in cols
                ]
                iid = (
                    str(item["seq"])
                    if name == "Orders"
                    else f"{item['seq']}:{item['route_id']}"
                    if name == "Routes"
                    else str(item["broker"])
                )
                tags = row_tags(
                    idx,
                    item.get("line_status", ""),
                    bool(item.get("warning")),
                    bool(item.get("adv_check")),
                )
                if iid in stale:
                    stale.remove(iid)
                    if (
                        tuple(str(x) for x in tree.item(iid, "values"))
                        != tuple(str(x) for x in cells)
                        or tuple(tree.item(iid, "tags")) != tags
                    ):
                        tree.item(iid, values=cells, tags=tags)
                    tree.move(iid, "", idx)
                else:
                    tree.insert("", "end", iid=iid, values=cells, tags=tags)
            for iid in stale:
                tree.delete(iid)
        self._visible = items
        healthy = (
            stats["stream_ok"]
            and self.monitoring
            and self.worker
            and self.worker.is_alive()
            and self.env.get() != "DEMO"
        )
        blockers = self._arm_blockers()
        if blockers:
            self.arm_blocker.set("활성화 불가: " + " | ".join(blockers))
        elif self.armed:
            self.arm_blocker.set(
                "모니터링 준비 완료 · 자동 수신확인 활성화 상태입니다. 개별 처리 결과를 확인하십시오."
            )
        else:
            self.arm_blocker.set(
                "데이터 수신 준비 완료 · 활성화 시 관리자 설정을 확인합니다. SellSideAck 전송 권한은 별도 확인이 필요합니다."
            )
        self.arm_btn.configure(
            state="normal" if healthy and not self.armed else "disabled"
        )
        self.disarm_btn.configure(state="normal" if self.armed else "disabled")
        self._render_acks()

    def _render_acks(self) -> None:
        pending = []
        for seq, rec in self.book.orders.items():
            if seq not in self.eligible_ids:
                continue
            f = rec.fields
            if (
                self.basket_filter.get().strip().casefold()
                not in str(f.get("EMSX_BASKET_NAME") or "").casefold()
            ):
                continue
            if str(f.get("EMSX_STATUS", "")).upper() != "ORD-PEND":
                continue
            permitted, reason = eligibility(
                {**f, "EMSX_SEQUENCE": seq}, self.admin_config
            )
            state, detail = self.ack_states.get(
                seq,
                (
                    "OBSERVE_ONLY" if permitted else "BLOCKED",
                    "초기 수신 또는 비활성 상태 · 전송 없음" if permitted else reason,
                ),
            )
            pending.append(
                (
                    seq,
                    str(f.get("EMSX_ACCOUNT") or ""),
                    str(f.get("EMSX_TICKER") or ""),
                    str(f.get("EMSX_SIDE") or ""),
                    str(f.get("EMSX_AMOUNT") or ""),
                    line_status(f.get("EMSX_STATUS")),
                    client_instruction(f),
                    localize(state),
                    localize(detail),
                    creation_fields(f)["create_time"],
                    str(f.get("EMSX_BASKET_NAME") or ""),
                )
            )
        pending.sort(
            key=lambda row: (row[-2], row[0]), reverse=self.order_sort.get() != "과거순"
        )
        if self.order_sort.get() == "Basket별 최신순":
            pending.sort(key=lambda row: row[-1].casefold())
        previous = set(self.ack_tree.get_children())
        for index, row in enumerate(pending):
            iid = str(row[0])
            tags = row_tags(index, row[5])
            if iid in previous:
                self.ack_tree.item(
                    iid, values=(row[-2], row[-1], *row[1:-2]), tags=tags
                )
                previous.remove(iid)
                self.ack_tree.move(iid, "", index)
            else:
                self.ack_tree.insert(
                    "", "end", iid=iid, values=(row[-2], row[-1], *row[1:-2]), tags=tags
                )
        for obsolete in previous:
            self.ack_tree.delete(obsolete)

    def export(self) -> None:
        tab = self.nb.tab(self.nb.select(), "text")
        if tab not in self.tabs:
            messagebox.showinfo(
                "CSV 저장", "Order, Route 또는 Broker 탭을 선택하십시오."
            )
            return
        if not messagebox.askokcancel(
            "기밀 데이터 저장 안내",
            "CSV에 고객 Account 및 체결 정보가 포함될 수 있습니다.\n승인된 내부 폴더에만 저장하십시오. 계속하시겠습니까?",
        ):
            return
        path = filedialog.asksaveasfilename(
            title="현재 탭 CSV 저장",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"EMSX_{tab}_{datetime.now():%Y%m%d_%H%M%S}.csv",
        )
        if not path:
            return
        cols = self.tabs[tab]["columns"]
        tree = self.tabs[tab]["tree"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                headers = list(cols)
                if tab == "Routes" and "adv_pct" in headers:
                    # 실제 표시 중인 기간을 기록하여 선택 직후의 화면 갱신 지연에도 맞춘다.
                    period = 5 if tree.heading("adv_pct", "text") == "ADV (5D)" else 20
                    headers[headers.index("adv_pct")] = f"adv_{period}d_pct"
                w.writerow(headers)
                for iid in tree.get_children():
                    w.writerow(tree.item(iid, "values"))
            self._append(f"CSV 저장 완료 ({tab}): {Path(path).name}")
            messagebox.showinfo(
                "CSV 저장 완료",
                f"현재 표시된 {len(tree.get_children())}개 행을 저장했습니다.\n{path}",
            )
        except OSError as exc:
            messagebox.showerror("CSV 저장 오류", str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="KB증권 EMSX 모니터", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="도움말을 표시합니다")
    parser.add_argument(
        "--demo", action="store_true", help="외부 연결 없는 모의 환경을 선택합니다"
    )
    parser.add_argument(
        "--self-test",
        metavar="보고서",
        help="외부 연결 없이 번들 구성 요소를 검사합니다",
    )
    args = parser.parse_args()
    if args.self_test:
        from standalone_check import run_check

        sys.exit(run_check(args.self_test))
    root = tk.Tk()
    MonitorApp(root, environment="DEMO" if args.demo else "PROD")
    root.mainloop()


if __name__ == "__main__":
    main()
