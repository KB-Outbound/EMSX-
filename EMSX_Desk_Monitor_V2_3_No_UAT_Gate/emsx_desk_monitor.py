"""Unified EMSX TEAMVIEW Desk Monitor + guarded E2E Auto ACK pilot.

PROD write operations disabled by default. Not a generic FIX ExecReport gateway.
TEAMVIEW visibility does not imply SellSideAck authorization.
"""

from __future__ import annotations
import csv
import os
import json
from datetime import datetime
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from bloomberg_feed import BloombergWorker
from demo_feed import DemoWorker
from desk_core import DeskBook
from ack_engine import load_config, eligibility, write_authorized

VERSION = "2.3.0"
APP_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "EMSX_Desk_Monitor_HDGB"
ADMIN_CONFIG = APP_DIR / "admin_config.json"
LEDGER_PATH = APP_DIR / "ack_journal.sqlite3"


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EMSX Desk Monitor V2.3 - TEAMVIEW HDGB + E2E ACK")
        self.root.geometry("1330x790")
        self.root.minsize(1000, 650)
        self.messages = queue.Queue(maxsize=10000)
        self.commands = queue.Queue()
        self.book = DeskBook()
        self.admin_config = load_config(ADMIN_CONFIG)
        self.ack_states = {}
        self.armed = False
        self.arm_state = tk.StringVar(value="DISARMED - no ACK transmissions")
        self.arm_blocker = tk.StringVar(value="ARM blocked: monitor not started")
        self.worker = None
        self.env = tk.StringVar(value="DEMO")
        self.team = tk.StringVar(value=self.admin_config["team_name"])
        self.search = tk.StringVar()
        self.account_filter = tk.StringVar()
        self.broker_filter = tk.StringVar()
        self.only_alerts = tk.BooleanVar(value=False)
        self.unrouted_min = tk.StringVar(value="5")
        self.nofill_min = tk.StringVar(value="15")
        self.status = tk.StringVar(value="STOPPED / AUTO ACK OFF")
        self.feed_state = tk.StringVar(value="ORDER: -  |  ROUTE: -  |  SNAPSHOT: -")
        self.counts = tk.StringVar(
            value="Orders 0  |  Routes 0  |  Unrouted 0  |  Alerts 0"
        )
        self.tabs = {}
        self._make_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(250, self._pump)

    def _make_ui(self):
        style = ttk.Style()
        style.configure("Treeview", rowheight=25)
        box = ttk.Frame(self.root, padding=12)
        box.pack(fill="both", expand=True)
        h = ttk.Frame(box)
        h.pack(fill="x")
        ttk.Label(
            h, text="EMSX DESK MONITOR  V2.3", font=("Segoe UI", 17, "bold")
        ).pack(side="left")
        ttk.Label(
            h, text="  TEAMVIEW HDGB  |  Auto ACK OFF by default", foreground="#17734a"
        ).pack(side="left")
        cfg = ttk.Frame(box)
        cfg.pack(fill="x", pady=(12, 8))
        ttk.Label(cfg, text="Environment").pack(side="left")
        self.env_ctrl = ttk.Combobox(
            cfg,
            textvariable=self.env,
            values=["DEMO", "BETA", "PROD"],
            width=8,
            state="readonly",
        )
        self.env_ctrl.pack(side="left", padx=5)
        ttk.Label(cfg, text="TEAMVIEW name").pack(side="left", padx=(12, 0))
        self.team_ctrl = ttk.Entry(cfg, textvariable=self.team, width=22)
        self.team_ctrl.pack(side="left", padx=5)
        self.start_btn = ttk.Button(cfg, text="START MONITOR", command=self.start)
        self.start_btn.pack(side="left", padx=(12, 4))
        self.stop_btn = ttk.Button(
            cfg, text="STOP", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(cfg, text="EXPORT CURRENT TAB CSV", command=self.export).pack(
            side="right"
        )
        ttk.Label(box, textvariable=self.status, foreground="#1b596f").pack(anchor="w")
        ttk.Label(box, textvariable=self.feed_state, foreground="#555").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Separator(box).pack(fill="x", pady=8)
        ttk.Label(box, textvariable=self.counts, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
        filt = ttk.Frame(box)
        filt.pack(fill="x", pady=(0, 8))
        ttk.Label(filt, text="Search").pack(side="left")
        ttk.Entry(filt, textvariable=self.search, width=24).pack(side="left", padx=5)
        ttk.Label(filt, text="Account contains").pack(side="left", padx=(10, 0))
        ttk.Entry(filt, textvariable=self.account_filter, width=17).pack(
            side="left", padx=5
        )
        ttk.Label(filt, text="Broker contains").pack(side="left", padx=(10, 0))
        ttk.Entry(filt, textvariable=self.broker_filter, width=12).pack(
            side="left", padx=5
        )
        ttk.Checkbutton(
            filt, text="Alerts only (order tab)", variable=self.only_alerts
        ).pack(side="left", padx=12)
        x = ttk.Frame(box)
        x.pack(fill="x", pady=(0, 6))
        ttk.Label(x, text="Unrouted check after observed min").pack(side="left")
        ttk.Entry(x, textvariable=self.unrouted_min, width=5).pack(side="left", padx=5)
        ttk.Label(x, text="No fill change after observed min").pack(
            side="left", padx=(15, 0)
        )
        ttk.Entry(x, textvariable=self.nofill_min, width=5).pack(side="left", padx=5)
        ttk.Label(
            x,
            text="Alerts are local observation timers, not original EMSX order age.",
            foreground="#8a4b00",
        ).pack(side="left", padx=16)
        nb = ttk.Notebook(box)
        nb.pack(fill="both", expand=True)
        spec = {
            "Orders": [
                ("seq", "Order#", 80),
                ("account", "Account", 148),
                ("ticker", "Security", 152),
                ("side", "Side", 60),
                ("qty", "Qty", 95),
                ("filled", "Fill", 95),
                ("remain", "Remain", 95),
                ("fill_pct", "Fill %", 70),
                ("status", "Status", 94),
                ("brokers", "Brokers", 105),
                ("routes", "Routes", 55),
                ("warning", "Desk alert", 240),
            ],
            "Routes": [
                ("seq", "Order#", 78),
                ("route_id", "Route#", 66),
                ("account", "Account", 145),
                ("ticker", "Security", 135),
                ("broker", "Broker", 80),
                ("side", "Side", 62),
                ("qty", "Route Qty", 90),
                ("filled", "Fill", 90),
                ("remain", "Remain", 90),
                ("status", "Route Status", 110),
                ("avg_price", "Avg Price", 90),
                ("parent_visible", "Parent order?", 98),
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
            tree.tag_configure("alert", background="#fff1de")
            tree.tag_configure("stale", foreground="#777777")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.tabs[label] = {"columns": [c[0] for c in columns], "tree": tree}
        ack_frame = ttk.Frame(nb, padding=8)
        nb.add(ack_frame, text="Auto ACK (E2E)")
        ttk.Label(
            ack_frame,
            text="TEAMVIEW subscription covers team orders. Auto ACK only for NEW, post-arm ORD-PEND E2E, never FIX NEW/WORKING.",
            foreground="#a14b00",
        ).pack(anchor="w")
        ttk.Label(
            ack_frame,
            text="TEAMVIEW HDGB is NOT a broker-code filter. PROD writes require admin flags and deliberate ARM. Confirm E2E entitlement before use.",
        ).pack(anchor="w")
        h2 = ttk.Frame(ack_frame)
        h2.pack(fill="x", pady=8)
        ttk.Label(h2, textvariable=self.arm_state, font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=(0, 18)
        )
        self.arm_btn = ttk.Button(
            h2, text="ARM AUTO ACK", command=self.arm_ack, state="disabled"
        )
        self.arm_btn.pack(side="left", padx=4)
        self.disarm_btn = ttk.Button(
            h2, text="DISARM", command=self.disarm_ack, state="disabled"
        )
        self.disarm_btn.pack(side="left", padx=4)
        ttk.Label(
            ack_frame,
            textvariable=self.arm_blocker,
            foreground="#9b3c24",
            wraplength=1150,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(
            h2, text="WHY ARM DISABLED?", command=self.show_arm_diagnostics
        ).pack(side="right", padx=(0, 12))
        ttk.Button(h2, text="CONFIG FILE LOCATION", command=self.show_config).pack(
            side="right"
        )
        acols = [
            ("seq", "Order#", 95),
            ("account", "Account", 165),
            ("ticker", "Ticker", 172),
            ("side", "Side", 75),
            ("qty", "Qty", 100),
            ("status", "Order Status", 115),
            ("ack_state", "ACK Decision / State", 220),
            ("ack_detail", "Reason / Response", 300),
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
        nb.add(activity, text="Activity / Diagnostics")
        log = tk.Text(
            activity, height=12, wrap="word", font=("Consolas", 10), state="disabled"
        )
        log.pack(fill="both", expand=True)
        self.log_text = log
        self.nb = nb
        ttk.Label(
            box,
            text="Scope: TEAMVIEW orders+routes. ACK is E2E SellSideAck only, not generic FIX 35=8; multiple writers on different PCs are NOT centrally deduplicated.",
            foreground="#8a4b00",
        ).pack(anchor="w", pady=(8, 0))
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
            "Live data, READ ONLY",
            "PROD connects to the real EMSX team feed.\nAUTO ACK initially OFF, and production writes administratively LOCKED by default.\nContinue monitoring?",
        ):
            return
        self.book = DeskBook()
        self.messages = queue.Queue(maxsize=10000)
        self.commands = queue.Queue()
        self.ack_states = {}
        self.armed = False
        self.arm_state.set("DISARMED - no ACK transmissions")
        try:
            self.admin_config = load_config(ADMIN_CONFIG)
        except Exception as e:
            messagebox.showerror("Admin config invalid", str(e))
            return
        if not team:
            messagebox.showerror(
                "TEAMVIEW required", "Enter the tested TEAMVIEW name HDGB."
            )
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
        self.status.set("CONNECTING / AUTO ACK OFF")
        self._append(f"Started {env} team monitoring / TEAMVIEW={team} / AUTO ACK OFF")
        self.worker.start()

    def stop(self) -> None:
        self.disarm_ack()
        if self.worker:
            self.worker.stop()
        self.status.set("STOPPING / AUTO ACK OFF")

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

    def show_config(self):
        messagebox.showinfo(
            "Local config",
            f"Admin flags are stored at:\n{ADMIN_CONFIG}\n\nDefault: writes disabled.\nOnly an administrator may enable BETA/PROD writes after documented validation and entitlement confirmation.",
        )

    def _arm_blockers(self):
        # Diagnostic-only; does not grant authorization, transmit ACKs or relax controls.
        problems = []
        if self.env.get() == "DEMO":
            problems.append("DEMO mode: Auto ACK unavailable")
        if not self.worker or not self.worker.is_alive():
            problems.append(
                "Bloomberg worker not running (START MONITOR / check Activity)"
            )
        for feed, name in [("O", "ORDER"), ("R", "ROUTE")]:
            if self.book.subscriptions[feed] != "STARTED":
                problems.append(f"{name} subscription={self.book.subscriptions[feed]}")
            if not self.book.snapshot_done[feed]:
                problems.append(f"{name} SNAPSHOT NOT DONE")
        if self.book.stream_error:
            problems.append("FEED ERROR: " + self.book.stream_error[:240])
        if self.book.event_gaps:
            problems.append("API SEQUENCE GAP (" + str(len(self.book.event_gaps)) + ")")
        return problems

    def show_arm_diagnostics(self):
        reasons = self._arm_blockers()
        status = (
            "Monitoring stream is ready; administrator write authorization is checked "
            "after ARM is clicked."
            if not reasons
            else "\n".join("- " + x for x in reasons)
        )
        messagebox.showinfo("Auto ACK readiness - diagnostic only", status)

    def arm_ack(self):
        if not self.worker or not self.worker.is_alive() or self.env.get() == "DEMO":
            messagebox.showerror("Not connected", "BETA/PROD connection is required.")
            return
        ok, reason = write_authorized(
            self.env.get(), self.team.get().strip(), self.admin_config
        )
        if not ok:
            messagebox.showerror(
                "ACK administratively locked",
                f"{reason}\n\nTeam visibility does not confer SellSideAck entitlement.\nConfig: {ADMIN_CONFIG}",
            )
            return
        if not self.book.counters()["stream_ok"]:
            messagebox.showerror(
                "Stream not ready",
                "Both Order and Route snapshots must complete with no gaps before arming.",
            )
            return
        if not messagebox.askyesno(
            "CONFIRM ACK WRITE",
            f"Enable ACTUAL SellSideAck requests for new E2E ORD-PEND orders?\n\nEnvironment: {self.env.get()}\nTEAMVIEW: {self.team.get()}\nAccount scope: ALL NONBLANK ACCOUNTS\nMax shares: {self.admin_config['max_shares']}\n\nExisting orders WILL NOT be ACKed. Only one authorized writer may run.\n\nThis changes real order state.",
        ):
            return
        self.commands.put({"action": "arm", "armed": True})
        self.arm_state.set("ARM REQUESTED (await confirmation)")

    def disarm_ack(self):
        self.armed = False
        self.arm_state.set("DISARMED - no new ACK sends")
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
                            "ARMED - new ORD-PEND only"
                            if self.armed
                            else "DISARMED - " + m.get("reason", "")
                        )
                    )
                    self._append(
                        "AUTO ACK "
                        + ("ARMED" if self.armed else "DISARMED")
                        + ": "
                        + m.get("reason", "")
                    )
                elif typ == "ack_disarmed":
                    self.armed = False
                    self.arm_state.set("SAFETY DISARMED")
                    self._append("SAFETY DISARM: " + m.get("reason", ""))
                elif typ == "ack_state":
                    seq = m["sequence"]
                    self.ack_states[seq] = (m.get("state", ""), m.get("detail", ""))
                    if m.get("state") in (
                        "SENT",
                        "API_ERROR",
                        "UNKNOWN",
                        "API_RESPONSE_OK_UNVERIFIED",
                    ):
                        self._append(
                            f"ACK #{seq}: {m.get('state')} {m.get('detail', '')[:120]}"
                        )
                elif typ == "subscription":
                    self.book.subscriptions[m["feed"]] = m["status"]
                    self._append(f"{m['feed']} subscription {m['status']}")
                elif typ == "connected":
                    self._append(m["message"])
                    self.status.set("CONNECTED / SNAPSHOT IN PROGRESS / AUTO ACK OFF")
                elif typ == "error":
                    self.book.stream_error = m["message"]
                    self.status.set("FEED ERROR / AUTO ACK DISARMED")
                    self.armed = False
                    self._append("ERROR: " + m["message"])
                elif typ == "ended":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.env_ctrl.configure(state="readonly")
                    self.team_ctrl.configure(state="normal")
                    if not self.book.stream_error:
                        self.status.set("STOPPED / AUTO ACK OFF")
                    self.armed = False
                    self.arm_btn.configure(state="disabled")
                    self.disarm_btn.configure(state="disabled")
            self._render()
        finally:
            self.root.after(10 if not self.messages.empty() else 50, self._pump)

    def _render(self):
        a, b = self._thresholds()
        rows = self.book.full_view(a, b)
        stats = self.book.counters()
        self.counts.set(
            f"Orders {stats['orders']:,}  |  Routes {stats['routes']:,}  |  Unrouted {stats['unrouted']:,}  |  Desk Alerts {sum(bool(r['warning']) for r in rows):,}"
        )
        p = self.book

        def label(feed):
            return f"{p.subscriptions[feed]} / {'SNAPSHOT DONE' if p.snapshot_done[feed] else 'INITIALIZING'}"

        warning = (
            f"  |  API SEQ GAPS {len(p.event_gaps)} (reconnect required)"
            if p.event_gaps
            else ""
        )
        self.feed_state.set(
            "ORDER: " + label("O") + "  |  ROUTE: " + label("R") + warning
        )
        if stats["stream_ok"] and self.worker and self.worker.is_alive():
            self.status.set(
                "LIVE TEAMVIEW MONITORING / "
                + ("AUTO ACK ARMED" if self.armed else "AUTO ACK OFF")
            )
        elif p.event_gaps:
            self.status.set("DEGRADED: API SEQUENCE GAP / ACK DISARMED")
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
            # Stable row IDs avoid flicker on desks with many TEAMVIEW orders.
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
            self.arm_blocker.set("ARM BLOCKED: " + " | ".join(blockers))
        elif self.armed:
            self.arm_blocker.set(
                "Monitoring ready; Auto ACK is already ARMED. Verify ACK events separately."
            )
        else:
            self.arm_blocker.set(
                "STREAM READY. ARM checks admin write flags; verify Bloomberg SellSideAck entitlement separately."
            )
        self.arm_btn.configure(
            state="normal" if healthy and not self.armed else "disabled"
        )
        self.disarm_btn.configure(state="normal" if self.armed else "disabled")
        self._render_acks()

    def _render_acks(self):
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
                    "Snapshot / not armed / no ACK sent" if permitted else reason,
                ),
            )
            pending.append(
                (
                    seq,
                    str(f.get("EMSX_ACCOUNT") or ""),
                    str(f.get("EMSX_TICKER") or ""),
                    str(f.get("EMSX_SIDE") or ""),
                    str(f.get("EMSX_AMOUNT") or ""),
                    str(f.get("EMSX_STATUS") or ""),
                    state,
                    detail,
                )
            )
        previous = set(self.ack_tree.get_children())
        for row in pending:
            iid = str(row[0])
            if iid in previous:
                self.ack_tree.item(iid, values=row)
                previous.remove(iid)
            else:
                self.ack_tree.insert("", "end", iid=iid, values=row)
        for obsolete in previous:
            self.ack_tree.delete(obsolete)

    def export(self):
        tab = self.nb.tab(self.nb.select(), "text")
        if tab not in self.tabs:
            messagebox.showinfo(
                "Export", "Choose Orders, Routes, or Brokers tab first."
            )
            return
        if not messagebox.askokcancel(
            "Sensitive data",
            "Export may contain confidential client account and execution data.\nSave only to an approved internal folder. Continue?",
        ):
            return
        path = filedialog.asksaveasfilename(
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
            self._append(f"CSV exported ({tab}): {Path(path).name}")
            messagebox.showinfo(
                "Export completed",
                f"Saved {len(tree.get_children())} visible rows.\n{path}",
            )
        except OSError as exc:
            messagebox.showerror("Export error", str(exc))


def main():
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
