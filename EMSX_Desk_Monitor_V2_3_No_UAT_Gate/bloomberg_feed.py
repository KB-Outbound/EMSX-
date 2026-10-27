"""주문·라우트 구독과 안전장치가 적용된 Ack 작업자다.

모든 blpapi 호출은 작업자 스레드에서 수행한다."""

from __future__ import annotations

from typing import Any, Callable, Sequence
import queue
import re
import threading
import random
import time
from pathlib import Path
from ack_engine import AckEngine, AckLedger
from order_fields import recent_order
from diagnostics import LOGGER, configure_diagnostics
from feed_parsing import read_scalar, integer

ORDER_FIELDS = [
    "API_SEQ_NUM",
    "EMSX_SEQUENCE",
    "EMSX_ACCOUNT",
    "EMSX_AMOUNT",
    "EMSX_AVG_PRICE",
    "EMSX_FILLED",
    "EMSX_WORKING",
    "EMSX_SIDE",
    "EMSX_TICKER",
    "EMSX_STATUS",
    "EMSX_ORDER_TYPE",
    "EMSX_NOTES",
    "EMSX_BASKET_NAME",
    "EMSX_TIME",
    "EMSX_TIF",
    "EMSX_TRADER",
    "EMSX_DATE",
]
ROUTE_FIELDS = [
    "API_SEQ_NUM",
    "EMSX_SEQUENCE",
    "EMSX_ROUTE_ID",
    "EMSX_BROKER",
    "EMSX_AMOUNT",
    "EMSX_FILLED",
    "EMSX_WORKING",
    "EMSX_STATUS",
    "EMSX_AVG_PRICE",
    "EMSX_ROUTE_CREATE_DATE",
    "EMSX_ROUTE_CREATE_TIME",
]
OPTIONAL_ORDER_FIELDS = {"EMSX_NOTES", "EMSX_BASKET_NAME", "EMSX_TIME"}
BASE_ORDER_FIELDS = [
    field
    for field in ORDER_FIELDS
    if field not in OPTIONAL_ORDER_FIELDS and field != "EMSX_SEQUENCE"
]
ENRICHMENT_FIELDS = {903: "EMSX_NOTES", 904: "EMSX_BASKET_NAME", 905: "EMSX_TIME"}

INT_FIELDS = {
    "EVENT_STATUS",
    "STATUS",
    "API_SEQ_NUM",
    "EMSX_SEQUENCE",
    "EMSX_ROUTE_ID",
    "EMSX_AMOUNT",
    "EMSX_FILLED",
    "EMSX_WORKING",
    "EMSX_DATE",
    "EMSX_TIME",
    "EMSX_ROUTE_CREATE_DATE",
    "EMSX_ROUTE_CREATE_TIME",
}
FLOAT_FIELDS = {"EMSX_AVG_PRICE"}


def read_field(
    msg: Any, field: str, on_warning: Callable[[str], None] | None = None
) -> Any:
    return read_scalar(msg, field, INT_FIELDS, FLOAT_FIELDS, on_warning)


def team_topic(service: str, kind: str, fields: Sequence[str], team: str = "") -> str:
    if kind not in ("order", "route"):
        raise ValueError("지원하지 않는 구독 유형")
    if team and not re.fullmatch(r"[\w .-]{1,80}", team, re.UNICODE):
        raise ValueError("구독 팀 이름에 허용되지 않은 문자가 있습니다")
    suffix = f";team={team}" if team else ""
    return f"{service}/{kind}{suffix}?fields={','.join(fields)}"


class BloombergWorker(threading.Thread):
    def __init__(
        self,
        environment: str,
        team: str,
        output: queue.Queue,
        commands: queue.Queue | None = None,
        config: dict[str, Any] | None = None,
        ledger_path: str | Path | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.environment = environment
        self.team = team.strip()
        self.output = output
        self.commands = commands or queue.Queue()
        self.config = config or {}
        self.ledger_path = ledger_path
        self.stop_event = threading.Event()
        self.session = None
        self.service = None
        self.api = None
        self.engine = None
        self.ledger = None
        self.started = set()
        self.snapshot_done = {"O": False, "R": False}
        self.seq_last = {"O": None, "R": None}
        self.feed_error = False
        self.reconnect_requested = False
        self.reconnect_initial = 1.0
        self.reconnect_max = 30.0
        self.queue_timeout = 0.1
        self.order_fields = list(BASE_ORDER_FIELDS)
        self.optional_disabled: set[str] = set()
        self.enrichment_enabled = True
        self.enrichment_requested = False
        self.warning_times: dict[str, float] = {}
        self.enrichment_orders: dict[int, dict[str, Any]] = {}
        self.enrichment_seq: dict[int, int] = {}

    def emit(self, typ: str, **kwargs: Any) -> None:
        try:
            self.output.put({"type": typ, **kwargs}, timeout=self.queue_timeout)
        except queue.Full as exc:
            self.feed_error = True
            if self.engine:
                self.engine.inhibit()
            raise RuntimeError("화면 이벤트 큐 과부하: 재동기화 필요") from exc

    def _warn(self, key: str, message: str) -> None:
        """동일 경고의 화면 반복을 제한하며 전송 상태를 변경하지 않는다."""
        now = time.monotonic()
        if now - self.warning_times.get(key, float("-inf")) >= 30:
            self.warning_times[key] = now
            self.emit("warning", message=message)

    def _handle_data_message(self, msg: Any) -> None:
        """파싱과 상태 변경을 분리하여 개별 필드 오류가 세션 예외가 되지 않게 한다."""
        try:
            ids = msg.correlationIds()
            cids = [correlation.value() for correlation in ids]
        except Exception:
            LOGGER.warning("상관 ID 추출 실패", exc_info=True)
            cids = []
        for cid in cids:
            if cid in ENRICHMENT_FIELDS:
                self._handle_enrichment(msg, cid)
        if any(cid in ENRICHMENT_FIELDS for cid in cids) and not any(
            cid in (901, 902) for cid in cids
        ):
            return
        invalid: set[str] = set()

        def read(field: str) -> Any:
            return read_field(msg, field, invalid.add)

        feed = read("MSG_SUB_TYPE")
        if feed not in ("O", "R"):
            try:
                ids = msg.correlationIds()
                feed = {901: "O", 902: "R"}.get(ids[0].value()) if ids else None
            except Exception:
                LOGGER.warning(
                    "구독 식별 실패: 함수=_handle_data_message", exc_info=True
                )
                feed = None
        if feed not in ("O", "R"):
            self._warn(
                "feed",
                "구독 식별 불가 메시지를 건너뛰었습니다. 진단 로그를 확인하십시오",
            )
            return
        status = read("EVENT_STATUS")
        api_seq = read("API_SEQ_NUM")
        if not self._observe_sequence(feed, status, api_seq) or self.feed_error:
            return
        selected = self.order_fields if feed == "O" else ROUTE_FIELDS
        fields = {
            field: value
            for field in dict.fromkeys(["EMSX_SEQUENCE", "EMSX_ROUTE_ID", *selected])
            if (value := read(field)) is not None
        }
        if api_seq is not None:
            fields["API_SEQ_NUM"] = api_seq
        order_id = fields.get("EMSX_SEQUENCE")
        if feed == "O" and order_id is not None:
            fields.update(self.enrichment_orders.get(order_id, {}))
            if status == 8:
                self.enrichment_orders.pop(order_id, None)
        if invalid:
            LOGGER.warning(
                "메시지 파싱 경고: 구독=%s 이벤트=%s 순번=%s 필드=%s",
                feed,
                status,
                api_seq,
                ",".join(sorted(invalid)),
            )
            self._warn(
                "fields-" + feed,
                f"{feed} 일부 필드 파싱 실패: "
                + ", ".join(sorted(invalid))
                + " · 수신은 계속합니다",
            )
        if status is None:
            LOGGER.warning("필수 이벤트 구분 누락: 구독=%s 순번=%s", feed, api_seq)
            if feed == "O" and self.engine:
                self.engine.block_order(
                    fields.get("EMSX_SEQUENCE"), "EVENT_STATUS_INVALID"
                )
            self._warn(
                "status-" + feed,
                f"{feed} 이벤트 구분 누락으로 해당 메시지를 제외했습니다",
            )
            return
        if status == 11:
            self.snapshot_done[feed] = True
            self.emit("data", feed=feed, status=status, fields=fields)
            self.engine.set_ready(self._ready())
            return
        if status == 1:
            self.emit("data", feed=feed, status=status, fields=fields)
            return
        self._commands()
        if self.stop_event.is_set():
            return
        self.emit("data", feed=feed, status=status, fields=fields)
        if feed == "O" and self.engine:
            if invalid:
                self.engine.block_order(
                    fields.get("EMSX_SEQUENCE"), "FIELD_PARSE_WARNING"
                )
            self.engine.set_ready(self._ready())
            self.engine.on_order(status, fields, self.snapshot_done["O"], self._ready())

    def _subscription_reason(self, msg: Any) -> str:
        """원문 메시지 전체 대신 공급자가 제공한 실패 사유만 추출한다."""
        try:
            if msg.hasElement("reason"):
                reason = msg.getElement("reason")
                parts = []
                for key in ("source", "category", "errorCode", "description"):
                    value = read_field(reason, key)
                    if value is not None:
                        parts.append(f"{key}={value}")
                return " | ".join(parts) or "상세 사유 미제공"
        except Exception:
            LOGGER.warning("구독 실패 사유 추출 실패", exc_info=True)
        return str(read_field(msg, "MESSAGE") or read_field(msg, "ERROR_MESSAGE") or "")

    def _handle_enrichment(self, msg: Any, cid: int) -> None:
        """선택 필드는 별도 순번을 사용하고 기본 Order를 생성하거나 Ack하지 않는다."""
        field = ENRICHMENT_FIELDS[cid]
        if field in self.optional_disabled:
            return
        invalid: set[str] = set()
        status = read_field(msg, "EVENT_STATUS", invalid.add)
        sequence = read_field(msg, "API_SEQ_NUM", invalid.add)
        order_id = read_field(msg, "EMSX_SEQUENCE", invalid.add)
        if sequence is not None:
            previous = self.enrichment_seq.get(cid)
            if previous is not None and sequence <= previous:
                return
            if previous is not None and sequence > previous + 1:
                self._warn(
                    "extra-gap-" + field,
                    f"{field} 선택 구독 순번 단절 · 기본 수신 유지",
                )
            self.enrichment_seq[cid] = sequence
        if status not in (4, 6, 7, 8) or order_id is None:
            return
        if status == 8:
            self.enrichment_orders.pop(order_id, None)
            return
        value = read_field(msg, field, invalid.add)
        if invalid:
            self._warn(
                "extra-parse-" + field, f"{field} 선택 필드 파싱 실패 · 기본 수신 유지"
            )
        if value is None:
            return
        if (
            order_id not in self.enrichment_orders
            and len(self.enrichment_orders) >= 100000
        ):
            self._warn("extra-limit", "선택 필드 캐시 한도 초과 · 기본 수신 유지")
            return
        self.enrichment_orders.setdefault(order_id, {})[field] = value
        if self.engine is not None and order_id in self.engine.orders:
            self.emit(
                "data",
                feed="O",
                status=7,
                fields={"EMSX_SEQUENCE": order_id, field: value},
            )

    def _subscribe_enrichment(self, A: Any, service: str) -> None:
        """필드별 독립 구독으로 하나의 거부가 다른 필드나 기본 구독에 전파되지 않게 한다."""
        for cid, field in ENRICHMENT_FIELDS.items():
            try:
                subscriptions = A.SubscriptionList()
                subscriptions.add(
                    topic=team_topic(
                        service, "order", ["API_SEQ_NUM", field], self.team
                    ),
                    correlationId=A.CorrelationId(cid),
                )
                self.session.subscribe(subscriptions)
            except Exception:
                self.optional_disabled.add(field)
                LOGGER.error("선택 필드 구독 요청 실패: 필드=%s", field, exc_info=True)
                self.emit("optional_unavailable", field=field)
                self._warn(
                    "extra-request-" + field,
                    f"{field} 선택 구독 요청 실패 · 기본 수신 유지",
                )

    def stop(self) -> None:
        self.stop_event.set()

    def _notify(self, typ: str, **kwargs: Any) -> None:
        self.emit(typ, **kwargs)

    def _ready(self) -> bool:
        return (
            not self.feed_error
            and self.started == {"O", "R"}
            and all(self.snapshot_done.values())
        )

    def _observe_sequence(
        self, feed: str, status: int | str, api_seq: int | None
    ) -> bool:
        """하트비트와 초기 수신 종료를 포함하여 모든 구독 이벤트의 연속성을 검사한다.

        불연속이면 Ack를 차단하고 새 세션에서 스냅샷을 다시 받아야 한다."""
        if api_seq is None:
            return True
        try:
            api_seq = integer(api_seq)
        except (ValueError, TypeError, OverflowError):
            LOGGER.warning("수신 순번 형식 오류: 구독=%s", feed, exc_info=True)
            self._warn(
                "sequence-" + feed, f"{feed} 순번 형식 확인 필요 · 진단 로그 참조"
            )
            return True
        previous = self.seq_last[feed]
        if previous is not None and (api_seq > previous + 1 or api_seq < previous):
            if self.enrichment_requested:
                self.enrichment_enabled = False
                LOGGER.warning("기본 순번 연속성 손실 후 선택 구독 없이 재동기화합니다")
            self._trip(
                f"API_SEQ_NUM discontinuity {feed}: {previous}->{api_seq} "
                f"(EVENT_STATUS={status}); no ACK allowed"
            )
        self.seq_last[feed] = (
            max(previous, api_seq) if previous is not None else api_seq
        )
        return previous is None or api_seq > previous

    def _send(self, seq: int, cid: int) -> None:
        # 실제 API 전송 경계다. 모든 사전 검증 후에만 호출한다.
        if self.stop_event.is_set() or not self._ready():
            raise RuntimeError("중지 또는 피드 비정상으로 전송 차단")
        request = self.service.createRequest("SellSideAck")
        request.append("EMSX_SEQUENCE", seq)
        self.session.sendRequest(request, correlationId=self.api.CorrelationId(cid))

    def _commands(self) -> None:
        for _ in range(50):
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            if cmd.get("action") == "arm" and self.engine:
                self.engine.set_ready(self._ready())
                self.engine.set_arm(bool(cmd.get("armed")))
            elif cmd.get("action") == "stop":
                self.stop_event.set()
                if self.engine:
                    self.engine.set_arm(False)

    def _trip(self, reason: str) -> None:
        LOGGER.error("수신 안전 차단: %s", reason)
        self.feed_error = True
        self.reconnect_requested = True
        if self.engine:
            self.engine.disarm(reason)
        self.emit("error", message=reason)

    def _process_response(self, event: Any) -> None:
        for msg in event:
            try:
                ids = msg.correlationIds()
                cid = ids[0].value() if ids else None
            except Exception:
                cid = None
            if cid is None:
                continue
            try:
                typ = str(msg.messageType())
            except Exception:
                LOGGER.warning(
                    "Ack 응답 종류 파싱 실패: 상관 ID=%s", cid, exc_info=True
                )
                continue
            status = read_field(msg, "STATUS")
            if status is None:
                LOGGER.warning(
                    "Ack 응답 STATUS 누락: 상관 ID=%s · 타임아웃까지 대기", cid
                )
                self._warn(
                    "ack-response",
                    "Ack 응답 형식을 확인할 수 없어 확정하지 않고 대기합니다",
                )
                continue
            detail = (
                read_field(msg, "MESSAGE") or read_field(msg, "ERROR_MESSAGE") or ""
            )
            self.engine.on_response(cid, typ, status, detail)

    def _cleanup_session(self) -> None:
        """한 자원의 종료 실패가 나머지 자원의 정리를 막지 않도록 한다."""
        for resource in (self.engine, self.session, self.ledger):
            if resource is None:
                continue
            try:
                if resource is self.session:
                    resource.stop()
                else:
                    resource.close()
            except Exception:
                LOGGER.exception("세션 자원 정리 실패")
        self.engine = self.session = self.ledger = self.service = None
        self.enrichment_orders.clear()
        self.enrichment_seq.clear()

    def run(self) -> None:
        if self.ledger_path:
            try:
                path = configure_diagnostics(Path(self.ledger_path).parent)
                self.emit("diagnostics", path=str(path))
            except OSError:
                LOGGER.error("진단 로그 파일 초기화 실패", exc_info=True)
                self._warn(
                    "diagnostic-file",
                    "진단 파일을 열지 못했습니다. 폴더 권한을 확인하십시오",
                )
        try:
            import blpapi
        except ImportError:
            self.emit("error", message="blpapi 설치 및 Bloomberg 런타임 확인 필요")
            self.emit("ended")
            return
        delay = self.reconnect_initial
        try:
            while not self.stop_event.is_set():
                self.started.clear()
                self.snapshot_done = {"O": False, "R": False}
                self.seq_last = {"O": None, "R": None}
                self.feed_error = False
                self.reconnect_requested = False
                self.enrichment_orders.clear()
                self.enrichment_seq.clear()
                self.optional_disabled.clear()
                self.enrichment_requested = False
                started_at = time.monotonic()
                try:
                    while True:
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            break
                        if command.get("action") == "stop":
                            self.stop_event.set()
                    if self.stop_event.is_set():
                        break
                    self.emit("reset")
                    if not self.enrichment_enabled:
                        for field in ENRICHMENT_FIELDS.values():
                            self.optional_disabled.add(field)
                            self.emit("optional_unavailable", field=field)
                        self._warn(
                            "core-only",
                            "구독 호환성을 위해 기본 Order·Route만 복구합니다",
                        )
                    self._run_session(blpapi)
                except Exception as exc:
                    LOGGER.exception("Bloomberg 세션 실패")
                    try:
                        self._trip(f"세션 오류: {type(exc).__name__}: {exc}")
                    except Exception:
                        LOGGER.exception("오류 알림 전달 실패")
                finally:
                    self._cleanup_session()
                if self.stop_event.is_set():
                    break
                if time.monotonic() - started_at >= 60:
                    delay = self.reconnect_initial
                # 대기 중에는 작업자만 쉬며 중지 요청은 즉시 반영한다.
                if self.stop_event.wait(
                    min(self.reconnect_max, delay * random.uniform(1, 1.2))
                ):
                    break
                delay = min(self.reconnect_max, delay * 2)
        finally:
            self._cleanup_session()
            for typ, values in [
                ("ack_armed", {"armed": False, "reason": "세션 종료"}),
                ("ended", {}),
            ]:
                try:
                    self.emit(typ, **values)
                except RuntimeError:
                    LOGGER.error("종료 알림 큐 과부하")

    def _run_session(self, A: Any) -> None:
        if not self.team:
            raise ValueError("승인된 구독 팀 이름이 필요합니다")
        service = (
            "//blp/emapisvc_beta" if self.environment == "BETA" else "//blp/emapisvc"
        )
        opts = A.SessionOptions()
        opts.setConnectTimeout(3000)
        opts.setServerHost("localhost")
        opts.setServerPort(8194)
        self.session = A.Session(opts)
        if not self.session.start():
            raise RuntimeError("Bloomberg localhost:8194 세션 시작 실패")
        if not self.session.openService(service):
            raise RuntimeError(f"EMSX 서비스 열기 실패: {service}")
        self.service = self.session.getService(service)
        self.api = A
        self.ledger = AckLedger(Path(self.ledger_path))
        self.engine = AckEngine(
            self.environment,
            self.team,
            self.config,
            self.ledger,
            self._notify,
            self._send,
            Path(self.ledger_path).with_name("ack_audit.jsonl"),
            date_guard=recent_order,
        )
        order_subs = A.SubscriptionList()
        order_subs.add(
            topic=team_topic(service, "order", self.order_fields, self.team),
            correlationId=A.CorrelationId(901),
        )
        self.session.subscribe(order_subs)
        route_requested = False
        self.emit(
            "connected",
            message=(
                f"{self.environment} / 구독 팀={self.team} "
                "주문·라우트 구독 / 자동 Ack 해제"
            ),
        )
        while not self.stop_event.is_set() and not self.reconnect_requested:
            self._commands()
            if self.stop_event.is_set():
                break
            event = self.session.nextEvent(50)
            typ = event.eventType()
            if typ == A.Event.SUBSCRIPTION_DATA:
                for msg in event:
                    self._handle_data_message(msg)
            elif typ == A.Event.SUBSCRIPTION_STATUS:
                for msg in event:
                    try:
                        ids = msg.correlationIds()
                        cid = ids[0].value() if ids else None
                        feed = {901: "O", 902: "R"}.get(cid)
                    except Exception:
                        cid = None
                        feed = None
                    name = str(msg.messageType())
                    if "SubscriptionStarted" in name:
                        if feed:
                            self.started.add(feed)
                            self.emit("subscription", feed=feed, status="STARTED")
                            if feed == "O" and not route_requested:
                                route_subs = A.SubscriptionList()
                                route_subs.add(
                                    topic=team_topic(
                                        service, "route", ROUTE_FIELDS, self.team
                                    ),
                                    correlationId=A.CorrelationId(902),
                                )
                                self.session.subscribe(route_subs)
                                route_requested = True
                    elif any(
                        tag in name
                        for tag in (
                            "SubscriptionFailure",
                            "SubscriptionTerminated",
                            "SubscriptionStopped",
                        )
                    ):
                        reason = self._subscription_reason(msg)
                        try:
                            request_id = (
                                msg.getRequestId()
                                if hasattr(msg, "getRequestId")
                                else "미제공"
                            )
                        except Exception:
                            request_id = "추출 실패"
                        LOGGER.error(
                            "구독 거부 또는 종료: 환경=%s 구독=%s 상관ID=%s 종류=%s 요청ID=%s 요청필드=%s 사유=%s",
                            self.environment,
                            feed,
                            cid,
                            name,
                            request_id,
                            self.order_fields
                            if feed == "O"
                            else ENRICHMENT_FIELDS.get(cid, ROUTE_FIELDS),
                            reason,
                        )
                        if cid in ENRICHMENT_FIELDS:
                            field = ENRICHMENT_FIELDS[cid]
                            self.optional_disabled.add(field)
                            for cached in self.enrichment_orders.values():
                                cached.pop(field, None)
                            self.emit("optional_unavailable", field=field)
                            self._warn(
                                "extra-failure-" + field,
                                f"{field} 선택 구독 거부: {reason} · 기본 수신 유지",
                            )
                            continue
                        if feed is not None and self.enrichment_requested:
                            self.enrichment_enabled = False
                            LOGGER.warning(
                                "기본 구독 종료 후 다음 연결은 선택 구독 없이 복구합니다"
                            )
                        self._trip(f"{feed or '?'} {name}: {reason}")
                        return
            elif typ in (A.Event.RESPONSE, A.Event.PARTIAL_RESPONSE):
                self._process_response(event)
            elif typ in (A.Event.SESSION_STATUS, A.Event.SERVICE_STATUS, A.Event.ADMIN):
                for msg in event:
                    name = str(msg.messageType())
                    if name == "SlowConsumerWarning":
                        LOGGER.warning(
                            "Bloomberg 처리 지연 경고 · 순번 검사와 수신 유지"
                        )
                        self._warn(
                            "slow-consumer",
                            "Bloomberg 처리 지연 경고 · 데이터 수신을 유지합니다",
                        )
                        continue
                    if name == "SlowConsumerWarningCleared":
                        LOGGER.info("Bloomberg 처리 지연 해소")
                        continue
                    if any(
                        x in name
                        for x in (
                            "SessionTerminated",
                            "SessionConnectionDown",
                            "ServiceOpenFailure",
                            "ServiceClosed",
                            "SessionStartupFailure",
                            "DataLoss",
                        )
                    ):
                        self._trip("Bloomberg " + name)
                        return
            if (
                self._ready()
                and self.enrichment_enabled
                and not self.enrichment_requested
            ):
                self.enrichment_requested = True
                self._subscribe_enrichment(A, service)
            if self.engine:
                self.engine.check_pending()
