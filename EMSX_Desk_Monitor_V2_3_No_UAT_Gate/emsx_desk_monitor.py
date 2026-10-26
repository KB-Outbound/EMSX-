"""KB증권 EMSX 모니터 화면과 안전한 작업자 수명 주기를 관리한다."""

from __future__ import annotations
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
from ack_engine import load_config, eligibility, write_authorized

from ui_text import APP_TITLE, DESK_TITLE, localize

VERSION = "2.4.2"
APP_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "EMSX_Desk_Monitor_HDGB"
ADMIN_CONFIG = APP_DIR / "admin_config.json"
LEDGER_PATH = APP_DIR / "ack_journal.sqlite3"


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
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
        self.env = tk.StringVar(value="DEMO")
        self.environment_header = tk.StringVar(value=f"DEMO | {DESK_TITLE}")
        self.team = tk.StringVar(value=self.admin_config["team_name"])
        self.search = tk.StringVar()
        self.account_filter = tk.StringVar()
        self.broker_filter = tk.StringVar()
        self.only_alerts = tk.BooleanVar(value=False)
        self.unrouted_min = tk.StringVar(value="5")
        self.nofill_min = tk.StringVar(value="15")
        self.status = tk.StringVar(value="모니터링 중지 / 자동 수신확인 해제")
        self.feed_state = tk.StringVar(value="Order: -  |  Route: -  |  초기 수신: -")
        self.counts = tk.StringVar(value="Order 0  |  Route 0  |  미배정 0  |  경고 0")
        self.tabs = {}
        self._make_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(250, self._pump)

    def _make_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        self.root.configure(background="#101720")
        style.configure(
            ".", background="#101720", foreground="#e1e7ef", font=("맑은 고딕", 10)
        )
        style.configure("TFrame", background="#101720")
        style.configure("TLabel", background="#101720")
        style.configure("TButton", background="#26364a", padding=(10, 6))
        style.map(
            "TButton",
            background=[("active", "#36516e")],
            foreground=[("disabled", "#8593a5")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#1b2635",
            foreground="#e1e7ef",
            insertcolor="#e1e7ef",
        )
        style.configure("TCombobox", fieldbackground="#1b2635", arrowcolor="#e1e7ef")
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#1b2635")],
            foreground=[("readonly", "#e1e7ef")],
        )
        style.configure("TNotebook", background="#101720", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1b2635", padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", "#304860")])
        style.configure(
            "Treeview",
            rowheight=27,
            background="#172230",
            fieldbackground="#172230",
            foreground="#e1e7ef",
            borderwidth=0,
        )
        style.configure("Treeview.Heading", background="#26364a", foreground="#e1e7ef")
        style.map(
            "Treeview",
            background=[("selected", "#305578")],
            foreground=[("selected", "#ffffff")],
        )
        self.root.option_add("*TCombobox*Listbox.background", "#1b2635")
        self.root.option_add("*TCombobox*Listbox.foreground", "#e1e7ef")
        box = ttk.Frame(self.root, padding=12)
        box.pack(fill="both", expand=True)
        h = ttk.Frame(box)
        h.pack(fill="x")
        ttk.Label(
            h, textvariable=self.environment_header, font=("맑은 고딕", 16, "bold")
        ).pack(side="left")
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
        self.start_btn = ttk.Button(cfg, text="모니터링 시작", command=self.start)
        self.start_btn.pack(side="left", padx=(12, 4))
        self.stop_btn = ttk.Button(
            cfg, text="모니터링 중지", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(cfg, text="현재 탭 CSV 저장", command=self.export).pack(side="right")
        ttk.Label(box, textvariable=self.status, foreground="#87c5e5").pack(anchor="w")
        ttk.Label(box, textvariable=self.feed_state, foreground="#a8b6c8").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Separator(box).pack(fill="x", pady=8)
        ttk.Label(box, textvariable=self.counts, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
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
            filt, text="경고만 표시 (Order)", variable=self.only_alerts
        ).pack(side="left", padx=12)
        x = ttk.Frame(box)
        x.pack(fill="x", pady=(0, 6))
        ttk.Label(x, text="Route 미지정 기준 (분)").pack(side="left")
        ttk.Entry(x, textvariable=self.unrouted_min, width=5).pack(side="left", padx=5)
        ttk.Label(x, text="미체결 정체 기준 (분)").pack(side="left", padx=(15, 0))
        ttk.Entry(x, textvariable=self.nofill_min, width=5).pack(side="left", padx=5)
        ttk.Label(
            x,
            text="※ 경고 기준 시간: 모니터링 개시 시점부터 누적된 관측 시간 기준",
            foreground="#f0b86e",
        ).pack(side="left", padx=16)
        nb = ttk.Notebook(box)
        nb.pack(fill="both", expand=True)
        spec = {
            "Orders": [
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
                ("warning", "점검 사항", 240),
            ],
            "Routes": [
                ("route_id", "Route#", 66),
                ("account", "Account", 145),
                ("ticker", "Ticker", 135),
                ("broker", "Broker", 80),
                ("side", "Side", 62),
                ("qty", "Route Qty", 90),
                ("filled", "Fill", 90),
                ("remain", "Remain", 90),
                ("line_status", "Line Status", 110),
                ("instruction", "Instruction", 150),
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
                    )
                    else "w",
                    stretch=key in ("account", "ticker", "warning"),
                )
            tree.tag_configure("alert", background="#49351f")
            tree.tag_configure("stale", foreground="#9ba8b8")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.tabs[label] = {"columns": [c[0] for c in columns], "tree": tree}
        ack_frame = ttk.Frame(nb, padding=8)
        nb.add(ack_frame, text="자동 수신확인(Ack)")
        ttk.Label(
            ack_frame,
            text="활성화 이후 새로 수신한 E2E ORD-PEND Order만 자동 수신확인합니다. "
            "기존 Order는 처리하지 않습니다.",
            foreground="#f0b86e",
            wraplength=1100,
        ).pack(anchor="w")
        h2 = ttk.Frame(ack_frame)
        h2.pack(fill="x", pady=8)
        ttk.Label(h2, textvariable=self.arm_state, font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=(0, 18)
        )
        self.arm_btn = ttk.Button(
            h2, text="자동 수신확인(Ack) 활성화", command=self.arm_ack, state="disabled"
        )
        self.arm_btn.pack(side="left", padx=4)
        self.disarm_btn = ttk.Button(
            h2, text="자동 수신확인 해제", command=self.disarm_ack, state="disabled"
        )
        self.disarm_btn.pack(side="left", padx=4)
        ttk.Label(
            ack_frame,
            textvariable=self.arm_blocker,
            foreground="#f0b86e",
            wraplength=1150,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(h2, text="활성화 조건 확인", command=self.show_arm_diagnostics).pack(
            side="right", padx=(0, 12)
        )
        ttk.Button(h2, text="설정 파일 위치", command=self.show_config).pack(
            side="right"
        )
        acols = [
            ("account", "Account", 165),
            ("ticker", "Ticker", 172),
            ("side", "Side", 75),
            ("qty", "Shares", 100),
            ("line_status", "Line Status", 115),
            ("instruction", "Instruction", 150),
            ("ack_state", "Ack 처리 상태", 220),
            ("ack_detail", "사유 / 응답", 300),
        ]
        self.ack_tree = ttk.Treeview(
            ack_frame, columns=[c[0] for c in acols], show="headings"
        )
        for col, label, width in acols:
            self.ack_tree.heading(col, text=label)
            self.ack_tree.column(
                col, width=width, stretch=col in ("account", "ticker", "ack_detail")
            )
        ack_scroll = ttk.Scrollbar(
            ack_frame, orient="vertical", command=self.ack_tree.yview
        )
        self.ack_tree.configure(yscrollcommand=ack_scroll.set)
        self.ack_tree.pack(side="left", fill="both", expand=True)
        ack_scroll.pack(side="right", fill="y")
        activity = ttk.Frame(nb, padding=3)
        nb.add(activity, text="Event Log")
        log = tk.Text(
            activity,
            height=12,
            wrap="word",
            font=("맑은 고딕", 10),
            state="disabled",
            background="#101720",
            foreground="#d5dfeb",
            insertbackground="#d5dfeb",
            selectbackground="#305578",
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
        self._append(f"{env} 모니터링 시작 / 구독 팀={team} / 자동 수신확인 해제")
        self.worker.start()

    def stop(self) -> None:
        self.disarm_ack()
        if self.worker:
            self.worker.stop()
        self.status.set("모니터링 종료 중 / 자동 수신확인 해제")

    def close(self) -> None:
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
            f"관리자 설정 파일:\n{ADMIN_CONFIG}\n\n기본값은 전송 차단입니다.\n검증과 전송 권한 확인 후 관리자가 BETA/PROD 전송을 허용해야 합니다.",
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
            f"신규 E2E ORD-PEND Order에 실제 SellSideAck 요청을 전송하시겠습니까?\n\n실행 환경: {self.env.get()}\n구독 팀: {self.team.get()}\nAccount 범위: 값이 있는 모든 Account\n최대 Shares: {self.admin_config['max_shares']}\n\n기존 Order는 처리하지 않습니다. 승인된 전송자 한 대만 실행하십시오.\n실제 Order 상태가 변경됩니다.",
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
                elif typ == "ack_state":
                    seq = m["sequence"]
                    self.ack_states[seq] = (
                        localize(m.get("state", "")),
                        localize(m.get("detail", "")),
                    )
                    if m.get("state") in (
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
                elif typ == "error":
                    self.book.stream_error = m["message"]
                    self.status.set("데이터 수신 오류 / 자동 수신확인 해제")
                    self.armed = False
                    self._append("오류: " + m["message"])
                elif typ == "ended":
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

    def _render(self) -> None:
        self.environment_header.set(f"{self.env.get()} | {DESK_TITLE}")
        a, b = self._thresholds()
        rows = self.book.full_view(a, b)
        stats = self.book.counters()
        self.counts.set(
            f"Order {stats['orders']:,}  |  Route {stats['routes']:,}  |  미배정 {stats['unrouted']:,}  |  경고 {sum(bool(r['warning']) for r in rows):,}"
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
        if stats["stream_ok"] and self.worker and self.worker.is_alive():
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
        items = {
            "Orders": rows,
            "Routes": self.book.route_view(),
            "Brokers": self.book.broker_summary(),
        }
        for name, data in items.items():
            tree = self.tabs[name]["tree"]
            cols = self.tabs[name]["columns"]
            visible = []
            for x in data:
                if name == "Orders" and self.only_alerts.get() and not x["warning"]:
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
                    f"{item.get(k, 0):,.1f}%"
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
                tags = ("alert",) if item.get("warning") else ()
                if iid in stale:
                    stale.remove(iid)
                    if tuple(str(x) for x in tree.item(iid, "values")) != tuple(
                        str(x) for x in cells
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
            f = rec.fields
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
                    str(f.get("EMSX_HAND_INSTRUCTION") or ""),
                    localize(state),
                    localize(detail),
                )
            )
        previous = set(self.ack_tree.get_children())
        for row in pending:
            iid = str(row[0])
            if iid in previous:
                self.ack_tree.item(iid, values=row[1:])
                previous.remove(iid)
            else:
                self.ack_tree.insert("", "end", iid=iid, values=row[1:])
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
                w.writerow(cols)
                for iid in tree.get_children():
                    w.writerow(tree.item(iid, "values"))
            self._append(f"CSV 저장 완료 ({tab}): {Path(path).name}")
            messagebox.showinfo(
                "CSV 저장 완료",
                f"현재 표시된 {len(tree.get_children())}개 행을 저장했습니다.\n{path}",
            )
        except OSError as exc:
            messagebox.showerror("CSV 저장 오류", str(exc))


def main():
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
