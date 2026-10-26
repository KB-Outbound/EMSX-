"""읽기 전용 주문·라우트 투영 상태다.

부분 갱신을 병합하며 누적 체결을 중복 합산하지 않는다.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
import time
import threading
from synchronization import synchronized

TERMINAL = {
    "FILLED",
    "COMPLETED",
    "CANCEL",
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
    "DELETED",
}
PENDING = {"NEW", "ORD-PEND", "ASSIGN", "ASSIGNED"}


LINE_STATUS = {
    "ASSIGN": "Assign",
    "ASSIGNED": "Assign",
    "CANCEL": "Cancel",
    "CANCELLED": "Cancel",
    "CANCELED": "Cancel",
    "PARTFILL": "Part-filled",
    "PARTFILLED": "Part-filled",
    "PART-FILLED": "Part-filled",
    "WORKING": "Working",
    "FILLED": "Filled",
    "COMPLETED": "Completed",
    "NEW": "New",
    "ORD-PEND": "Ord-pend",
    "SENT": "Sent",
    "HOLD": "Hold",
    "EXPIRED": "Expired",
    "REJECTED": "Rejected",
    "DELETED": "Deleted",
}


def line_status(value: Any) -> str:
    """EMSX_STATUS를 화면용 Line Status로 표시하며 미지의 값은 보존한다."""
    raw = text(value).strip()
    return LINE_STATUS.get(raw.upper(), raw)


def num(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def text(value: Any) -> str:
    return "" if value is None else str(value)


def safe_pct(filled: Any, amount: Any) -> float:
    return (
        round(min(100.0, 100.0 * num(filled) / num(amount)), 1) if num(amount) else 0.0
    )


@dataclass
class Record:
    fields: dict = field(default_factory=dict)
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_fill_change: float = 0.0
    from_snapshot: bool = False


class DeskBook:
    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._lock = threading.RLock()
        self.now = now or time.monotonic
        self.orders: dict[int, Record] = {}
        self.routes: dict[tuple[int, int], Record] = {}
        self.snapshot_done = {"O": False, "R": False}
        self.subscriptions = {"O": "PENDING", "R": "PENDING"}
        self.seq_last = {"O": None, "R": None}
        self.event_gaps: list[str] = []
        self.activity: list[str] = []
        self.stream_error = ""
        self.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @synchronized
    def notice(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.activity.append(f"{ts}  {message}")
        self.activity = self.activity[-300:]

    @synchronized
    def ingest(
        self, feed: str, status: int, fields: dict, initial: bool = False
    ) -> None:
        if feed not in ("O", "R"):
            return
        # 이벤트 필터 전에 하트비트의 순번도 검사한다.
        # 초기 수신 종료 이벤트의 순번도 누락하지 않는다.
        api_seq = fields.get("API_SEQ_NUM")
        if api_seq is not None:
            try:
                sequence = int(api_seq)
                last = self.seq_last[feed]
                if last is not None and sequence > last + 1:
                    self.event_gaps.append(
                        f"{feed}: sequence gap {last} -> {sequence}, "
                        f"event_status={status}"
                    )
                    self.notice(
                        f"순번 단절 {feed} {last} -> {sequence} (status={status})"
                    )
                if last is not None and sequence < last:
                    self.event_gaps.append(
                        f"{feed}: sequence backwards {last} -> {sequence}, "
                        f"event_status={status}"
                    )
                    self.notice(
                        f"순번 역행 {feed}: {last} -> {sequence} (status={status})"
                    )
                self.seq_last[feed] = (
                    max(last, sequence) if last is not None else sequence
                )
            except (ValueError, TypeError):
                pass
        self.event_gaps = self.event_gaps[-300:]
        if status == 11:
            self.snapshot_done[feed] = True
            self.notice(f"{'ORDER' if feed == 'O' else 'ROUTE'} 초기 데이터 수신 완료")
            return
        if status == 1:
            return
        if status not in (4, 6, 7, 8):
            return
        seq = fields.get("EMSX_SEQUENCE")
        if seq is None:
            return
        try:
            order_id = int(seq)
        except (ValueError, TypeError):
            return
        key = order_id
        if feed == "R":
            try:
                rid = int(fields["EMSX_ROUTE_ID"])
            except (KeyError, TypeError, ValueError):
                return
            # 동일 라우트의 체결 갱신은 별도 라우트로 집계하지 않는다.
            key = (order_id, rid)
        target = self.orders if feed == "O" else self.routes
        if status == 8:
            target.pop(key, None)
            self.notice(f"{feed} {key} 구독에서 삭제됨")
            return
        now = self.now()
        if key not in target:
            target[key] = Record(
                first_seen=now,
                last_fill_change=now,
                from_snapshot=(status == 4 or initial),
            )
            if status == 6:
                self.notice(f"신규 {'order' if feed == 'O' else 'route'}: {key}")
        record = target[key]
        prior_fill = num(record.fields.get("EMSX_FILLED"))
        record.fields.update({k: v for k, v in fields.items() if v is not None})
        record.last_seen = now
        if "EMSX_FILLED" in fields and num(fields["EMSX_FILLED"]) != prior_fill:
            record.last_fill_change = now
            if status != 4:
                self.notice(
                    f"체결 변경 {feed} {key}: "
                    f"{prior_fill} -> {num(fields['EMSX_FILLED'])}"
                )
        self.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @synchronized
    def full_view(
        self, unrouted_minutes: float = 5, nofill_minutes: float = 15
    ) -> list[dict[str, Any]]:
        now = self.now()
        route_index: dict[int, list[Record]] = {}
        for (seq, rid), route in self.routes.items():
            route_index.setdefault(seq, []).append(route)
        rows = []
        for seq, rec in self.orders.items():
            f = rec.fields
            route_records = route_index.get(seq, [])
            broker_codes = sorted(
                set(
                    text(x.fields.get("EMSX_BROKER")).strip()
                    for x in route_records
                    if text(x.fields.get("EMSX_BROKER")).strip()
                )
            )
            qty = num(f.get("EMSX_AMOUNT"))
            filled = num(f.get("EMSX_FILLED"))
            remain = max(0, qty - filled)
            status = text(f.get("EMSX_STATUS")).upper()
            warning = []
            if (
                self.snapshot_done["O"]
                and self.snapshot_done["R"]
                and status not in TERMINAL
            ):
                if status == "ORD-PEND":
                    warning.append("E2E ACK 대기")
                if (
                    not route_records
                    and status in PENDING
                    and now - rec.first_seen >= unrouted_minutes * 60
                ):
                    warning.append("Route 미지정(관측 기준)")
                active_routes = [
                    x
                    for x in route_records
                    if text(x.fields.get("EMSX_STATUS")).upper()
                    in (
                        "SENT",
                        "WORKING",
                        "PARTFILL",
                        "PARTFILLED",
                        "PART-FILLED",
                        "ASSIGN",
                        "ASSIGNED",
                        "HOLD",
                        "CXLREQ",
                        "REPPEN",
                        "REPLACE-PEND",
                    )
                ]
                if (
                    active_routes
                    and 0 < remain
                    and now - max(x.last_fill_change for x in active_routes)
                    >= nofill_minutes * 60
                ):
                    warning.append("미체결 정체(관측 기준)")
                if (
                    route_records
                    and not active_routes
                    and 0 < remain
                    and now - max(x.last_seen for x in route_records)
                    >= unrouted_minutes * 60
                ):
                    warning.append("Route 종료 후 잔량 점검")
            rows.append(
                {
                    "seq": seq,
                    "account": text(f.get("EMSX_ACCOUNT")),
                    "ticker": text(f.get("EMSX_TICKER")),
                    "side": text(f.get("EMSX_SIDE")),
                    "qty": qty,
                    "filled": filled,
                    "remain": remain,
                    "fill_pct": safe_pct(filled, qty),
                    "status": status,
                    "line_status": line_status(f.get("EMSX_STATUS")),
                    "instruction": text(f.get("EMSX_HAND_INSTRUCTION")),
                    "brokers": ",".join(broker_codes),
                    "routes": len(route_records),
                    "warning": " / ".join(warning),
                    "observed_mins": round((now - rec.first_seen) / 60, 1),
                    "snapshot": rec.from_snapshot,
                    "avg_price": text(f.get("EMSX_AVG_PRICE")),
                    "trader": text(f.get("EMSX_TRADER")),
                }
            )
        return sorted(
            rows, key=lambda x: (not bool(x["warning"]), x["account"], x["seq"])
        )

    @synchronized
    def route_view(self) -> list[dict[str, Any]]:
        rows = []
        for (seq, rid), rec in self.routes.items():
            f = rec.fields
            parent = self.orders.get(seq)
            pf = parent.fields if parent else {}
            qty = num(f.get("EMSX_AMOUNT"))
            filled = num(f.get("EMSX_FILLED"))
            rows.append(
                {
                    "seq": seq,
                    "route_id": rid,
                    "broker": text(f.get("EMSX_BROKER")),
                    "account": text(pf.get("EMSX_ACCOUNT")),
                    "ticker": text(pf.get("EMSX_TICKER")),
                    "side": text(pf.get("EMSX_SIDE")),
                    "qty": qty,
                    "filled": filled,
                    "remain": max(0, qty - filled),
                    "status": text(f.get("EMSX_STATUS")),
                    "line_status": line_status(f.get("EMSX_STATUS")),
                    "instruction": text(pf.get("EMSX_HAND_INSTRUCTION")),
                    "avg_price": text(f.get("EMSX_AVG_PRICE")),
                    "parent_missing": parent is None,
                    "parent_visible": "NO" if parent is None else "YES",
                }
            )
        return sorted(rows, key=lambda x: (x["broker"], x["seq"], x["route_id"]))

    @synchronized
    def broker_summary(self) -> list[dict[str, Any]]:
        brokers = {}
        for r in self.route_view():
            broker = r["broker"] or "(UNKNOWN)"
            x = brokers.setdefault(
                broker,
                {
                    "broker": broker,
                    "routes": 0,
                    "routed": 0,
                    "filled": 0,
                    "remaining": 0,
                },
            )
            x["routes"] += 1
            x["routed"] += r["qty"]
            x["filled"] += r["filled"]
            x["remaining"] += r["remain"]
        return [
            {**x, "fill_pct": safe_pct(x["filled"], x["routed"])}
            for x in sorted(brokers.values(), key=lambda a: -a["routed"])
        ]

    @synchronized
    def counters(self) -> dict[str, Any]:
        orders = self.full_view()
        return {
            "orders": len(orders),
            "routes": len(self.routes),
            "alerts": sum(bool(r["warning"]) for r in orders),
            "filled_orders": sum(
                r["status"] in ("FILLED", "COMPLETED")
                or (r["qty"] > 0 and r["remain"] == 0)
                for r in orders
            ),
            "unrouted": sum(
                r["routes"] == 0 and r["status"] not in TERMINAL for r in orders
            ),
            "stream_ok": all(self.snapshot_done.values())
            and all(x == "STARTED" for x in self.subscriptions.values())
            and not self.stream_error
            and not self.event_gaps,
        }
