"""E2E Ack 판정과 영속 예약 및 감사 기록을 관리한다.

TEAMVIEW 조회 권한은 전송 권한을 증명하지 않는다. 승인된 단일 전송자만 사용한다.
초기 스냅샷은 자동 승인하지 않으며 운영 전송의 기본값은 비활성이다."""

from __future__ import annotations
from typing import Any, Callable, Sequence
from dataclasses import dataclass
from itertools import islice
from uuid import uuid4
import math
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


ACK_BATCH_SIZE = 50
ACK_BATCH_WINDOW_SECONDS = 0.010
ACK_MAX_INFLIGHT = 20


@dataclass(frozen=True)
class PendingAck:
    """한 요청에 속한 주문과 응답 대기 시작 시각을 보관한다."""

    sequences: tuple[int, ...]
    sent_at: float
    batch_id: str


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
        "adv_period": 20,
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
    if (
        not isinstance(cfg["adv_period"], int)
        or isinstance(cfg["adv_period"], bool)
        or cfg["adv_period"] not in (5, 20)
    ):
        raise ValueError("adv_period는 5 또는 20이어야 합니다")
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
        return bool(self.reserve_many(env, team, (seq,)))

    @synchronized
    def reserve_many(
        self, env: str, team: str, sequences: Sequence[int], details: str = ""
    ) -> tuple[int, ...]:
        """한 트랜잭션에서 신규 주문만 예약하며 기존 고유 키는 보존한다."""
        reserved: list[int] = []
        updated_at = utc_now()
        with self.conn:
            for seq in sequences:
                cursor = self.conn.execute(
                    "INSERT INTO ack_intent VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(environment, team, sequence) DO NOTHING",
                    (env, team, seq, "RESERVED", updated_at, details),
                )
                if cursor.rowcount:
                    reserved.append(seq)
        return tuple(reserved)

    @synchronized
    def update(
        self, env: str, team: str, seq: int, status: int | str, details: str = ""
    ) -> None:
        self.update_many(env, team, (seq,), status, details)

    @synchronized
    def update_many(
        self,
        env: str,
        team: str,
        sequences: Sequence[int],
        status: int | str,
        details: str = "",
    ) -> None:
        """배치 전체 상태를 한 번의 영속 트랜잭션으로 변경한다."""
        updated_at = utc_now()
        with self.conn:
            self.conn.executemany(
                """UPDATE ack_intent SET status=?,updated_at=?,details=?
                                 WHERE environment=? AND team=? AND sequence=?""",
                [
                    (status, updated_at, details[:900], env, team, seq)
                    for seq in sequences
                ],
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
        batch_send_func: Callable[[tuple[int, ...], int], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.env = environment
        self.team = team.strip().upper()
        self.config = config
        self.ledger = ledger
        self.notify = notify
        self.send_func = send_func
        self.batch_send_func = batch_send_func
        self.audit_path = Path(audit_path) if audit_path else None
        self.date_guard = date_guard
        self.armed = False
        self.orders: dict[int, dict[str, Any]] = {}
        self.seen: set[int] = set()
        self.new_while_armed: set[int] = set()
        self.queued: dict[int, float] = {}
        self.pending: dict[int, PendingAck] = {}
        self.next_cid = 121000
        self.stream_ready = False
        self.error = False
        self.closed = False
        self.max_tracked_orders = 100000

    @synchronized
    def publish(
        self, seq: int, state: str, detail: str = "", *, batch_id: str = ""
    ) -> None:
        if state in ("BLOCKED", "DUPLICATE_BLOCKED"):
            self.audit("ACK_" + state, seq, detail)
        fields: dict[str, Any] = {"sequence": seq, "state": state, "detail": detail}
        if batch_id and self.batch_send_func is not None:
            fields["batch_id"] = batch_id
        self.notify("ack_state", **fields)

    @synchronized
    def audit(
        self,
        kind: str,
        seq: int | None = None,
        detail: str = "",
        *,
        batch_id: str = "",
        cid: int | None = None,
        sequences: Sequence[int] = (),
    ) -> None:
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
            if batch_id:
                # 주문 목록은 자르지 않아 배치의 모든 구성원을 대조할 수 있다.
                line.update(
                    batch_id=batch_id, correlation_id=cid, sequences=list(sequences)
                )
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self.inhibit()
            raise

    @synchronized
    def inhibit(self) -> None:
        """알림이나 디스크 접근 없이 즉시 추가 전송을 차단한다."""
        self.armed = False
        self.stream_ready = False
        self.error = True
        self.new_while_armed.clear()
        self.queued.clear()

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
            self.queued.clear()
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
        self.queued.clear()
        self.notify(
            "ack_armed", armed=True, reason="Armed for newly arriving E2E orders only"
        )

    @synchronized
    def block_order(self, sequence: Any, reason: str) -> None:
        """불완전 메시지는 해당 주문만 제외하고 전체 활성화는 유지한다."""
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
        self.queued.pop(seq, None)
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
            self.queued.pop(seq, None)
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
        if not ok:
            # 대기 중 취소·변경된 주문은 이후 정상 갱신에도 자동 재진입하지 않는다.
            self.queued.pop(seq, None)
            self.new_while_armed.discard(seq)
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
                if self.batch_send_func is None:
                    self._send_ack(seq)
                elif seq not in self.queued:
                    self.queued[seq] = time.monotonic()
                    self.publish(seq, "QUEUED", "신규 Order 배치 전송 대기")
            else:
                if seq in self.queued:
                    self.publish(seq, "QUEUED", "신규 Order 배치 전송 대기")
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
        """기존 단건 호출 계약을 유지한다."""
        self._submit((seq,))

    @synchronized
    def batch_timeout_ms(self) -> int:
        """배치 기한에 맞춰 수신 대기만 줄이며 0의 무한 대기는 사용하지 않는다."""
        if not self.queued or len(self.pending) >= ACK_MAX_INFLIGHT:
            return 50
        if len(self.queued) >= ACK_BATCH_SIZE:
            return 1
        due = next(iter(self.queued.values())) + ACK_BATCH_WINDOW_SECONDS
        return max(1, min(50, math.ceil((due - time.monotonic()) * 1000)))

    @synchronized
    def flush_batch(self) -> None:
        """수신 루프 한 회에 최대 한 배치를 전송하고 응답을 기다리지 않는다."""
        if self.closed or not self.queued or self.batch_send_func is None:
            return
        if not self.armed or not self.stream_ready or self.error:
            self.queued.clear()
            return
        if len(self.pending) >= ACK_MAX_INFLIGHT:
            return
        if (
            len(self.queued) < ACK_BATCH_SIZE
            and time.monotonic() - next(iter(self.queued.values()))
            < ACK_BATCH_WINDOW_SECONDS
        ):
            return
        sequences = tuple(islice(self.queued, ACK_BATCH_SIZE))
        for seq in sequences:
            self.queued.pop(seq)
        self._submit(sequences)

    @synchronized
    def _batch_audit(
        self, action: str, cid: int, record: PendingAck, detail: str = ""
    ) -> None:
        """배치 목록 전체를 한 번의 파일 동기화로 기록한다."""
        prefix = "ACK_BATCH_" if self.batch_send_func is not None else "ACK_"
        self.audit(
            prefix + action,
            record.sequences[0] if len(record.sequences) == 1 else None,
            detail,
            batch_id=record.batch_id,
            cid=cid,
            sequences=record.sequences,
        )

    @synchronized
    def _batch_notify(
        self, cid: int, record: PendingAck, state: str, detail: str = ""
    ) -> None:
        """화면 큐에 배치 요약만 넣으며 화면 객체에 직접 접근하지 않는다."""
        if self.batch_send_func is not None:
            self.notify(
                "ack_batch",
                batch_id=record.batch_id,
                correlation_id=cid,
                count=len(record.sequences),
                state=state,
                detail=detail,
            )

    @synchronized
    def _submit(self, sequences: tuple[int, ...]) -> None:
        """검증·영속 예약·응답 추적을 먼저 완료한 뒤 API 경계를 한 번 호출한다."""
        ok, reason = write_authorized(self.env, self.team, self.config)
        if not ok or not self.armed or not self.stream_ready or self.error:
            for seq in sequences:
                self.new_while_armed.discard(seq)
                self.publish(seq, "BLOCKED", reason if not ok else "Not armed/ready")
            return
        valid_sequences: list[int] = []
        for seq in sequences:
            order = self.orders.get(seq, {})
            valid, reason = eligibility(order, self.config)
            if self.date_guard is not None and not self.date_guard(order):
                valid, reason = False, "TRADE_DATE_OUT_OF_RANGE"
            if seq not in self.new_while_armed:
                valid, reason = False, "신규 자동 Ack 대상이 아닙니다"
            self.new_while_armed.discard(seq)
            if valid:
                valid_sequences.append(seq)
            else:
                self.publish(seq, "BLOCKED", reason)
        if not valid_sequences:
            return
        cid = self.next_cid
        self.next_cid += 1
        batch_id = uuid4().hex
        details = f"batch_id={batch_id} correlation_id={cid}"
        try:
            reserved = self.ledger.reserve_many(
                self.env, self.team, valid_sequences, details
            )
        except Exception:
            self.inhibit()
            raise
        reserved_set = set(reserved)
        for seq in valid_sequences:
            if seq not in reserved_set:
                self.publish(
                    seq,
                    "DUPLICATE_BLOCKED",
                    "Local journal already contains this sequence",
                )
        if not reserved:
            return
        record = PendingAck(reserved, time.monotonic(), batch_id)
        try:
            self._batch_audit("RESERVED", cid, record, "전송 전 영속 예약 완료")
        except Exception:
            self.inhibit()
            self.ledger.update_many(
                self.env,
                self.team,
                reserved,
                "AUDIT_FAILED",
                details + " 감사 기록 실패로 전송 차단",
            )
            raise
        registered = False
        try:
            self.ledger.update_many(
                self.env, self.team, reserved, "SENT", details + " 응답 대기"
            )
            self.pending[cid] = record
            registered = True
            if self.batch_send_func is not None:
                self.batch_send_func(reserved, cid)
            else:
                self.send_func(reserved[0], cid)
            # 동기 응답 대역에서 이미 확정된 결과를 SENT로 덮어쓰지 않는다.
            if cid not in self.pending:
                return
            self._batch_audit("SENT", cid, record, "API 응답 대기")
            for seq in reserved:
                self.publish(
                    seq,
                    "SENT",
                    "ACK request pending; not yet confirmed",
                    batch_id=batch_id,
                )
            self._batch_notify(cid, record, "SENT")
        except Exception as ex:
            was_armed = self.armed
            self.inhibit()
            if was_armed:
                self.notify("ack_disarmed", reason="Ack 배치 전송 오류: 수동 대조 필요")
            if registered and cid not in self.pending:
                # 이미 수신한 확정 응답은 전송 함수의 후속 예외로 덮어쓰지 않는다.
                self.disarm("ACK transmit uncertain / " + str(ex))
                return
            # DB 상태 변경 실패도 예약된 전체 주문을 불확실 상태로 보존한다.
            self.pending.pop(cid, None)
            self.ledger.update_many(
                self.env, self.team, reserved, "UNKNOWN", details + " " + str(ex)
            )
            self._batch_audit("SEND_UNKNOWN", cid, record, str(ex))
            for seq in reserved:
                self.publish(seq, "UNKNOWN", str(ex), batch_id=batch_id)
            self._batch_notify(cid, record, "UNKNOWN", str(ex))
            self.disarm("ACK transmit uncertain / " + str(ex))

    @synchronized
    def on_response(
        self,
        cid: int,
        msg_type: str,
        status: int | str,
        message: str,
        *,
        final: bool = True,
    ) -> None:
        if cid not in self.pending:
            return
        if msg_type == "SellSideAck" and status == 0:
            if not final:
                # 부분 응답 성공만으로 배치 전체의 성공을 확정하지 않는다.
                self._batch_audit("PARTIAL_RESPONSE", cid, self.pending[cid], message)
                return
            result = "API_RESPONSE_OK_UNVERIFIED"
        else:
            result = "API_ERROR"
        self._finish(
            cid, result, "RESPONSE", f"{msg_type} STATUS={status} MESSAGE={message}"
        )
        if result == "API_ERROR":
            self.disarm("ACK API failed; verify manually")

    @synchronized
    def _finish(self, cid: int, result: str, action: str, detail: str) -> None:
        record = self.pending.get(cid)
        if record is None:
            return
        self.ledger.update_many(
            self.env,
            self.team,
            record.sequences,
            result,
            f"batch_id={record.batch_id} correlation_id={cid} {detail}",
        )
        self._batch_audit(action, cid, record, detail)
        self.pending.pop(cid, None)
        for seq in record.sequences:
            self.publish(seq, result, detail, batch_id=record.batch_id)
        self._batch_notify(cid, record, result, detail)

    @synchronized
    def on_request_failure(self, cid: int, detail: str) -> None:
        """요청 실패 시 배치 전체를 결과 불확실 상태로 남긴다."""
        if cid in self.pending:
            self.disarm("Ack 요청 실패: 수동 대조 필요")
            self._finish(cid, "UNKNOWN", "REQUEST_UNKNOWN", detail)

    @synchronized
    def check_pending(self) -> None:
        for cid, record in list(self.pending.items()):
            if time.monotonic() - record.sent_at >= 20:
                self.disarm("ACK timeout; manual reconciliation required")
                self._finish(
                    cid,
                    "UNKNOWN",
                    "TIMEOUT_UNKNOWN",
                    "ACK outcome uncertain; reconcile in EMSX",
                )

    @synchronized
    def close(self) -> None:
        """불확실한 전송을 보존하고 메모리를 해제한다. 반복 호출도 허용한다."""
        self.inhibit()
        self.closed = True
        failure: Exception | None = None
        try:
            for cid in list(self.pending):
                try:
                    self._finish(
                        cid,
                        "UNKNOWN",
                        "CONNECTION_CLOSED_UNKNOWN",
                        "연결 종료 전에 응답을 받지 못함",
                    )
                except Exception as exc:
                    failure = exc
        finally:
            self.pending.clear()
            self.orders.clear()
            self.seen.clear()
            self.new_while_armed.clear()
            self.queued.clear()
        if failure is not None:
            raise failure
