"""주문·라우트 구독과 안전장치가 적용된 Ack 작업자다.

모든 blpapi 호출은 작업자 스레드에서 수행한다."""

from __future__ import annotations

from typing import Any
import queue
import re
import threading
import logging
import random
import time
from pathlib import Path
from ack_engine import AckEngine, AckLedger

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
    "EMSX_HAND_INSTRUCTION",
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
INT_FIELDS = {
    "API_SEQ_NUM",
    "EMSX_SEQUENCE",
    "EMSX_ROUTE_ID",
    "EMSX_AMOUNT",
    "EMSX_FILLED",
    "EMSX_WORKING",
    "EMSX_DATE",
    "EMSX_ROUTE_CREATE_DATE",
    "EMSX_ROUTE_CREATE_TIME",
}
FLOAT_FIELDS = {"EMSX_AVG_PRICE"}


def read_field(msg: Any, field: str) -> Any:
    try:
        if not msg.hasElement(field):
            return None
        if field in INT_FIELDS:
            return msg.getElementAsInteger(field)
        if field in FLOAT_FIELDS:
            return msg.getElementAsFloat(field)
        return msg.getElementAsString(field)
    except Exception:
        return None


def team_topic(service: str, kind: str, fields: dict[str, Any], team: str = "") -> str:
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
        self.reconnect_initial = 1.0
        self.reconnect_max = 30.0
        self.queue_timeout = 0.1

    def emit(self, typ: str, **kwargs: Any) -> None:
        try:
            self.output.put({"type": typ, **kwargs}, timeout=self.queue_timeout)
        except queue.Full as exc:
            self.feed_error = True
            if self.engine:
                self.engine.inhibit()
            raise RuntimeError("화면 이벤트 큐 과부하: 재동기화 필요") from exc

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
        previous = self.seq_last[feed]
        if previous is not None and (api_seq > previous + 1 or api_seq < previous):
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
        self.feed_error = True
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
            typ = str(msg.messageType())
            status = read_field(msg, "STATUS")
            # 응답 STATUS는 일반 구독 필드와 달리 정수로 읽는다.
            try:
                status = msg.getElementAsInteger("STATUS")
            except Exception:
                pass
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
                logging.exception("세션 자원 정리 실패")
        self.engine = self.session = self.ledger = self.service = None

    def run(self) -> None:
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
                    self._run_session(blpapi)
                except Exception as exc:
                    logging.exception("Bloomberg 세션 실패")
                    try:
                        self._trip(f"세션 오류: {type(exc).__name__}: {exc}")
                    except Exception:
                        logging.exception("오류 알림 전달 실패")
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
                    logging.error("종료 알림 큐 과부하")

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
        )
        order_subs = A.SubscriptionList()
        order_subs.add(
            topic=team_topic(service, "order", ORDER_FIELDS, self.team),
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
        while not self.stop_event.is_set():
            self._commands()
            event = self.session.nextEvent(50)
            typ = event.eventType()
            if typ == A.Event.SUBSCRIPTION_DATA:
                for msg in event:
                    feed = read_field(msg, "MSG_SUB_TYPE")
                    if feed not in ("O", "R"):
                        try:
                            ids = msg.correlationIds()
                            feed = (
                                {901: "O", 902: "R"}.get(ids[0].value())
                                if ids
                                else None
                            )
                            if feed is None:
                                continue
                        except Exception:
                            continue
                    try:
                        status = msg.getElementAsInteger("EVENT_STATUS")
                    except Exception:
                        continue
                    # 하트비트와 초기 수신 종료도 순번을 증가시킨다.
                    # 이들을 무시하면 허위 순번 단절이 발생한다.
                    api_seq = read_field(msg, "API_SEQ_NUM")
                    fresh = self._observe_sequence(feed, status, api_seq)
                    if not fresh:
                        continue
                    if status == 11:
                        self.snapshot_done[feed] = True
                        fields = {"API_SEQ_NUM": api_seq} if api_seq is not None else {}
                        self.emit("data", feed=feed, status=11, fields=fields)
                        self.engine.set_ready(self._ready())
                        continue
                    if status == 1:
                        fields = {"API_SEQ_NUM": api_seq} if api_seq is not None else {}
                        self.emit("data", feed=feed, status=1, fields=fields)
                        continue
                    selected = ORDER_FIELDS if feed == "O" else ROUTE_FIELDS
                    fields = {
                        k: v
                        for k in ["EMSX_SEQUENCE", "EMSX_ROUTE_ID"] + selected
                        if (v := read_field(msg, k)) is not None
                    }
                    # 부분 응답에서도 순번 메타데이터를 유지한다.
                    if api_seq is not None:
                        fields["API_SEQ_NUM"] = api_seq
                    self._commands()
                    if self.stop_event.is_set():
                        return
                    # 주문 순서를 유지하고 전송 직전 중지 명령을 반영한다.
                    self.emit("data", feed=feed, status=status, fields=fields)
                    if feed == "O" and self.engine:
                        self.engine.set_ready(self._ready())
                        self.engine.on_order(
                            status, fields, self.snapshot_done["O"], self._ready()
                        )
            elif typ == A.Event.SUBSCRIPTION_STATUS:
                for msg in event:
                    try:
                        ids = msg.correlationIds()
                        feed = {901: "O", 902: "R"}.get(ids[0].value()) if ids else None
                    except Exception:
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
                        self._trip(f"{feed or '?'} {name}: {str(msg)[:250]}")
                        return
            elif typ in (A.Event.RESPONSE, A.Event.PARTIAL_RESPONSE):
                self._process_response(event)
            elif typ in (A.Event.SESSION_STATUS, A.Event.SERVICE_STATUS, A.Event.ADMIN):
                for msg in event:
                    name = str(msg.messageType())
                    if any(
                        x in name
                        for x in (
                            "SessionTerminated",
                            "SessionConnectionDown",
                            "ServiceOpenFailure",
                            "ServiceClosed",
                            "SessionStartupFailure",
                            "SlowConsumerWarning",
                            "SessionConnectionDown",
                            "SessionTerminated",
                        )
                    ):
                        self._trip("Bloomberg " + name)
                        return
            if self.engine:
                self.engine.check_pending()
