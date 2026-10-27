"""E2E Ack 판정과 영속 예약 및 감사 기록을 관리한다.

TEAMVIEW 조회 권한은 전송 권한을 증명하지 않는다. 승인된 단일 전송자만 사용한다.
초기 스냅샷은 자동 승인하지 않으며 운영 전송의 기본값은 비활성이다."""

from __future__ import annotations
from typing import Any, Callable
import json
import sqlite3
import threading
import time
import os
from decimal import Decimal, InvalidOperation
from synchronization import synchronized
from diagnostics import LOGGER
from feed_parsing import integer
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_config() -> dict[str, Any]:
    return {
        "team_name": "HDGB",
        "max_shares": 10000,
        "allowed_sides": ["BUY", "SELL"],
        "require_nonblank_account": True,
        "production_write_enabled": False,
        "production_team_hdgb_approved": False,
        "production_all_accounts_approved": False,
        "beta_write_enabled": False,
    }


def load_config(path: Path) -> dict[str, Any]:
    cfg = default_config()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("admin_config.json must be an object")
        for key in cfg:
            if key in data:
                cfg[key] = data[key]
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    if (
        not isinstance(cfg["max_shares"], int)
        or isinstance(cfg["max_shares"], bool)
        or cfg["max_shares"] < 1
    ):
        raise ValueError("max_shares must be a positive integer")
    for name in (
        "production_write_enabled",
        "production_team_hdgb_approved",
        "production_all_accounts_approved",
        "beta_write_enabled",
        "require_nonblank_account",
    ):
        if not isinstance(cfg[name], bool):
            raise ValueError(f"{name} must be true or false")
    if not isinstance(cfg["allowed_sides"], list) or not cfg["allowed_sides"]:
        raise ValueError("allowed_sides must be a nonempty list")
    if not isinstance(cfg["team_name"], str) or not cfg["team_name"].strip():
        raise ValueError("team_name must be set")
    return cfg


def eligibility(order: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str]:
    """계좌 이름의 허용 목록과 별개로 필수 필드와 ORD-PEND 상태를 검증한다.

    이 판정은 E2E 발신자나 Bloomberg 전송 권한을 증명하지 않는다."""
    if str(order.get("EMSX_STATUS", "")).upper() != "ORD-PEND":
        return False, "NOT_ORD_PEND"
    try:
        values = [order.get("EMSX_SEQUENCE"), order.get("EMSX_AMOUNT")]
        parsed = [Decimal(str(value)) for value in values]
        if any(
            not value.is_finite() or value != value.to_integral_value()
            for value in parsed
        ):
            return False, "INVALID_SEQUENCE_OR_QTY"
        seq, qty = map(int, parsed)
    except (TypeError, ValueError, InvalidOperation, OverflowError):
        return False, "INVALID_SEQUENCE_OR_QTY"
    if seq < 1 or qty < 1 or qty > cfg["max_shares"]:
        return False, "INVALID_OR_OVER_LIMIT_QTY"
    if (
        cfg["require_nonblank_account"]
        and not str(order.get("EMSX_ACCOUNT") or "").strip()
    ):
        return False, "ACCOUNT_EMPTY"
    side = str(order.get("EMSX_SIDE") or "").upper().strip()
    if side not in {str(x).upper() for x in cfg["allowed_sides"]}:
        return False, "INVALID_SIDE"
    if not str(order.get("EMSX_TICKER") or "").strip():
        return False, "MISSING_TICKER"
    if not str(order.get("EMSX_ORDER_TYPE") or "").strip():
        return False, "MISSING_ORDER_TYPE"
    if not str(order.get("EMSX_TIF") or "").strip():
        return False, "MISSING_TIF"
    return True, "ELIGIBLE_E2E_STATUS_ONLY"


def write_authorized(
    environment: str, team: str, cfg: dict[str, Any]
) -> tuple[bool, str]:
    if not team or team.upper() != cfg["team_name"].strip().upper():
        return False, "TEAM_MISMATCH"
    if environment == "BETA":
        if not cfg["beta_write_enabled"]:
            return False, "BETA_WRITES_LOCKED"
        return True, "BETA_APPROVED"
    if environment != "PROD":
        return False, "NO_LIVE_WRITES_IN_DEMO"
    if not (
        cfg["production_write_enabled"]
        and cfg["production_team_hdgb_approved"]
        and cfg["production_all_accounts_approved"]
    ):
        return False, "PRODUCTION_WRITES_LOCKED"
    return True, "PROD_ADMIN_FLAGS_PRESENT_NOT_ENTITLEMENT_PROOF"


class AckLedger:
    """동일한 로컬 DB 내 영속 예약이다. 서로 다른 PC의 분산 잠금은 아니다."""

    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS ack_intent (
            environment TEXT NOT NULL,
            team TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (environment, team, sequence))""")
        self.conn.commit()

    @synchronized
    def reserve(self, env: str, team: str, seq: int) -> bool:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO ack_intent VALUES (?, ?, ?, ?, ?, ?)",
                    (env, team, seq, "RESERVED", utc_now(), "reserved before API send"),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    @synchronized
    def update(
        self, env: str, team: str, seq: int, status: int | str, details: str = ""
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE ack_intent SET status=?,updated_at=?,details=?
                                 WHERE environment=? AND team=? AND sequence=?""",
                (status, utc_now(), details[:900], env, team, seq),
            )

    @synchronized
    def get(self, env: str, team: str, seq: int) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM ack_intent "
            "WHERE environment=? AND team=? AND sequence=?",
            (env, team, seq),
        ).fetchone()
        return row[0] if row else None

    @synchronized
    def close(self) -> None:
        self.conn.close()


class AckEngine:
    """복합 상태 전이를 잠금으로 보호하는 자동 Ack 엔진이다."""

    def __init__(
        self,
        environment: str,
        team: str,
        config: dict[str, Any],
        ledger: AckLedger,
        notify: Callable[..., None],
        send_func: Callable[[int, int], None],
        audit_path: Path | None = None,
        date_guard: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.env = environment
        self.team = team.strip().upper()
        self.config = config
        self.ledger = ledger
        self.notify = notify
        self.send_func = send_func
        self.audit_path = Path(audit_path) if audit_path else None
        self.date_guard = date_guard
        self.armed = False
        self.orders = {}
        self.seen = set()
        self.new_while_armed = set()
        self.pending = {}  # 상관 ID에 주문 번호와 단조 시계 전송 시각을 연결한다.
        self.next_cid = 121000
        self.stream_ready = False
        self.error = False
        self.closed = False
        self.max_tracked_orders = 100000

    @synchronized
    def publish(self, seq: int, state: str, detail: str = "") -> None:
        if state in ("BLOCKED", "DUPLICATE_BLOCKED"):
            self.audit("ACK_" + state, seq, detail)
        self.notify("ack_state", sequence=seq, state=state, detail=detail)

    @synchronized
    def audit(self, kind: str, seq: int = None, detail: str = "") -> None:
        if not self.audit_path:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            line = {
                "utc": utc_now(),
                "type": kind,
                "environment": self.env,
                "team": self.team,
                "sequence": seq,
                "detail": str(detail)[:600],
            }
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self.armed = False
            self.stream_ready = False
            self.error = True
            raise

    @synchronized
    def inhibit(self) -> None:
        """알림이나 디스크 접근 없이 즉시 추가 전송을 차단한다."""
        self.armed = False
        self.stream_ready = False
        self.error = True
        self.new_while_armed.clear()

    @synchronized
    def disarm(self, reason: str) -> None:
        was_armed = self.armed
        self.inhibit()
        self.audit("SAFETY_DISARM", detail=reason)
        if was_armed:
            self.notify("ack_disarmed", reason=reason)

    @synchronized
    def set_ready(self, ready: bool) -> None:
        self.stream_ready = ready and not self.error
        if not ready and self.armed:
            self.disarm("피드 준비 상태가 해제됨")

    @synchronized
    def set_arm(self, arm: bool) -> None:
        if self.closed:
            return
        if not arm:
            self.audit("OPERATOR_DISARM")
            self.armed = False
            self.new_while_armed.clear()
            self.notify("ack_armed", armed=False, reason="Operator disarmed")
            return
        permitted, reason = write_authorized(self.env, self.team, self.config)
        if not permitted or not self.stream_ready or self.error:
            self.audit(
                "ARM_DENIED", detail=reason if not permitted else "FEED_NOT_HEALTHY"
            )
            self.notify(
                "ack_armed",
                armed=False,
                reason=reason if not permitted else "FEED_NOT_HEALTHY",
            )
            return
        self.armed = True
        self.audit("OPERATOR_ARM")
        self.new_while_armed.clear()
        self.notify(
            "ack_armed", armed=True, reason="Armed for newly arriving E2E orders only"
        )

    @synchronized
    def block_order(self, sequence: Any, reason: str) -> None:
        """단일 불완전 메시지는 해당 주문만 조회 대상으로 유지하고 전체 활성화는 유지한다."""
        try:
            seq = integer(sequence)
            if seq < 1:
                return
        except (ValueError, TypeError, OverflowError):
            return
        if seq not in self.seen and len(self.seen) >= self.max_tracked_orders:
            self.disarm("주문 추적 한도 초과: 수동 재동기화 필요")
            return
        self.seen.add(seq)
        self.new_while_armed.discard(seq)
        LOGGER.warning("개별 주문 자동 Ack 제외: 주문=%s 사유=%s", seq, reason)

    @synchronized
    def on_order(
        self,
        event_status: int,
        fields: dict[str, Any],
        snapshot_done: bool,
        stream_ok: bool,
    ) -> None:
        if self.closed:
            return
        if not isinstance(fields, dict):
            LOGGER.warning("Ack 주문 입력 형식 오류: 함수=on_order")
            return
        if not stream_ok or not snapshot_done:
            self.set_ready(False)
        if event_status == 8:
            try:
                seq = integer(fields.get("EMSX_SEQUENCE"))
            except (ValueError, TypeError, OverflowError):
                return
            self.orders.pop(seq, None)
            self.new_while_armed.discard(seq)
            return
        if event_status not in (4, 6, 7):
            return
        try:
            seq = integer(fields["EMSX_SEQUENCE"])
        except (KeyError, ValueError, TypeError, OverflowError):
            LOGGER.warning("Ack 주문 식별자 파싱 실패: 함수=on_order", exc_info=True)
            return
        if seq < 1:
            return
        if seq not in self.seen and len(self.seen) >= self.max_tracked_orders:
            self.disarm("주문 추적 한도 초과: 수동 재동기화 필요")
            return
        first_seen = seq not in self.seen
        self.seen.add(seq)
        order = self.orders.setdefault(seq, {"EMSX_SEQUENCE": seq})
        order.update({k: v for k, v in fields.items() if v is not None})
        ok, reason = eligibility(order, self.config)
        if self.date_guard is not None and not self.date_guard(order):
            ok, reason = False, "TRADE_DATE_OUT_OF_RANGE"
        # 필수 필드가 갖춰진 신규 이벤트만 자동 승인한다.
        # 후속 부분 갱신과 초기 스냅샷은 자동 승인하지 않는다.
        new_is_eligible = (
            first_seen
            and event_status == 6
            and snapshot_done
            and stream_ok
            and self.armed
            and self.stream_ready
            and not self.error
            and ok
        )
        if new_is_eligible:
            self.new_while_armed.add(seq)
        if str(order.get("EMSX_STATUS", "")).upper() == "ORD-PEND":
            j = self.ledger.get(self.env, self.team, seq)
            if j:
                self.publish(seq, j, "Already attempted; no automatic retry")
            elif not ok:
                self.publish(seq, "BLOCKED", reason)
            elif seq not in self.new_while_armed:
                self.publish(seq, "OBSERVE_ONLY", "Initial snapshot / pre-arm order")
            elif not (
                snapshot_done
                and stream_ok
                and self.stream_ready
                and self.armed
                and not self.error
            ):
                self.publish(seq, "BLOCKED", "Not healthy/armed")
            elif event_status == 6:
                self._send_ack(seq)
            else:
                self.publish(
                    seq, "OBSERVE_ONLY", "No auto ACK on later dynamic updates"
                )
        else:
            j = self.ledger.get(self.env, self.team, seq)
            if j:
                self.publish(
                    seq, j, "Order status now " + str(order.get("EMSX_STATUS", ""))
                )

    @synchronized
    def _send_ack(self, seq: int) -> None:
        ok, reason = write_authorized(self.env, self.team, self.config)
        if not ok:
            self.publish(seq, "BLOCKED", reason)
            return
        if not self.armed or not self.stream_ready or self.error:
            self.publish(seq, "BLOCKED", "Not armed/ready")
            return
        valid, reason = eligibility(self.orders[seq], self.config)
        if self.date_guard is not None and not self.date_guard(self.orders[seq]):
            valid, reason = False, "TRADE_DATE_OUT_OF_RANGE"
        if not valid:
            self.publish(seq, "BLOCKED", reason)
            return
        if not self.ledger.reserve(self.env, self.team, seq):
            self.publish(
                seq, "DUPLICATE_BLOCKED", "Local journal already contains this sequence"
            )
            return
        try:
            self.audit("ACK_RESERVED", seq)
        except Exception:
            self.armed = False
            self.error = True
            self.stream_ready = False
            self.ledger.update(
                self.env, self.team, seq, "AUDIT_FAILED", "감사 기록 실패로 전송 차단"
            )
            raise
        self.publish(seq, "RESERVED", "Persisted before API request")
        try:
            cid = self.next_cid
            self.next_cid += 1
            self.pending[cid] = (seq, time.monotonic())
            self.ledger.update(self.env, self.team, seq, "SENT", "응답 대기")
            self.send_func(seq, cid)
            if cid not in self.pending:
                return
            self.audit("ACK_SENT", seq)
            self.publish(seq, "SENT", "ACK request pending; not yet confirmed")
        except Exception as ex:
            self.pending.pop(cid, None)
            self.armed = False
            self.error = True
            self.stream_ready = False
            self.ledger.update(self.env, self.team, seq, "UNKNOWN", str(ex))
            self.audit("ACK_SEND_UNKNOWN", seq, str(ex))
            self.publish(seq, "UNKNOWN", str(ex))
            self.disarm("ACK transmit uncertain / " + str(ex))

    @synchronized
    def on_response(
        self, cid: int, msg_type: str, status: int | str, message: str
    ) -> None:
        record = self.pending.pop(cid, None)
        if not record:
            return
        seq, _ = record
        if msg_type == "SellSideAck" and status == 0:
            result = "API_RESPONSE_OK_UNVERIFIED"
        else:
            result = "API_ERROR"
        self.ledger.update(
            self.env,
            self.team,
            seq,
            result,
            f"{msg_type} STATUS={status} MESSAGE={message}",
        )
        self.audit("ACK_RESPONSE", seq, f"{msg_type} STATUS={status} {message}")
        self.publish(seq, result, f"API {msg_type}: STATUS={status}, {message}")
        if result == "API_ERROR":
            self.disarm("ACK API failed; verify manually")

    @synchronized
    def check_pending(self) -> None:
        import time

        for cid, (seq, sent) in list(self.pending.items()):
            if time.monotonic() - sent >= 20:
                self.pending.pop(cid, None)
                self.ledger.update(
                    self.env, self.team, seq, "UNKNOWN", "No response within 20 seconds"
                )
                self.audit("ACK_TIMEOUT_UNKNOWN", seq)
                self.publish(seq, "UNKNOWN", "ACK outcome uncertain; reconcile in EMSX")
                self.disarm("ACK timeout; manual reconciliation required")

    @synchronized
    def close(self) -> None:
        """불확실한 전송을 보존하고 메모리를 해제한다. 반복 호출도 허용한다."""
        self.inhibit()
        self.closed = True
        failure: Exception | None = None
        try:
            for seq, _ in list(self.pending.values()):
                try:
                    self.ledger.update(
                        self.env,
                        self.team,
                        seq,
                        "UNKNOWN",
                        "연결 종료 전에 응답을 받지 못함",
                    )
                    self.audit("ACK_CONNECTION_CLOSED_UNKNOWN", seq)
                except Exception as exc:
                    failure = exc
        finally:
            self.pending.clear()
            self.orders.clear()
            self.seen.clear()
            self.new_while_armed.clear()
        if failure is not None:
            raise failure
