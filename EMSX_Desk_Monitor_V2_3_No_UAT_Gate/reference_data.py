"""사용자가 지정한 Bloomberg 필드 ID를 검증하고 참조데이터를 비동기로 수신한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import time
import re
from typing import Any

from diagnostics import LOGGER
from feed_parsing import read_scalar, integer

FIELD_IDS = {
    "HS005": "avg_volume_5d",
    "HS007": "avg_volume_20d",
    "DS290": "iso_country",
    "DS213": "sec_type",
}
EXPECTED_VOLUMES = {"HS005": "VOLUME_AVG_5D", "HS007": "VOLUME_AVG_20D"}
SERVICES = {70001: "//blp/apiflds", 70002: "//blp/refdata"}
FIELD_INFO_CID = 70003


def reference_identifier(value: str) -> str:
    """FIGI는 Bloomberg 식별자 경로로 요청하고 Ticker+YKey는 그대로 사용한다."""
    return (
        "/bbgid/" + value.upper()
        if re.fullmatch(r"BBG[A-Z0-9]{9}", value.upper())
        else value
    )


def elements(element: Any) -> list[Any]:
    return [element.getValueAsElement(index) for index in range(element.numValues())]


def scalar(element: Any, field: str, numeric: bool = False) -> Any:
    return read_scalar(element, field, set(), {field} if numeric else set())


def error_summary(element: Any) -> str:
    """실패 코드와 설명만 남기며 보안·거래 요청 전체를 덤프하지 않는다."""
    return " | ".join(
        f"{key}={value}"
        for key in (
            "source",
            "category",
            "subcategory",
            "code",
            "message",
            "description",
        )
        if (value := scalar(element, key)) is not None
    )


class ReferenceDataFeed:
    def __init__(
        self,
        session: Any,
        api: Any,
        emit: Callable[..., None],
        warn: Callable[[str, str], None],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.api = api
        self.emit = emit
        self.warn = warn
        self.now = now
        self.started = False
        self.opened: set[str] = set()
        self.started_at = 0.0
        self.info_sent = False
        self.info_done = False
        self.info_at = 0.0
        self.info_attempts = 0
        self.info_cid = FIELD_INFO_CID
        self.mnemonics: dict[str, str] = {}
        self.queue: deque[str] = deque()
        self.queued: set[str] = set()
        self.requested: dict[str, str] = {}
        self.cached_at: dict[str, float] = {}
        self.symbols: dict[str, str] = {}
        self.pending: tuple[int, list[str], float] | None = None
        self.values: dict[str, dict[str, Any]] = {}
        self.next_cid = 1000000
        self.refresh_at = 0.0
        self.event_info_seen = False
        self.event_data_seen = False

    def queue_security(self, security: Any) -> None:
        symbol = str(security or "").strip()
        key = symbol.casefold()
        if not symbol or key in self.queued:
            return
        if key in self.cached_at and self.now() - self.cached_at[key] < 300:
            return
        if key not in self.symbols and len(self.symbols) >= 100000:
            self.warn("reference-limit", "참조데이터 종목 한도 초과 · 기본 수신 유지")
            return
        self.symbols[key] = symbol
        self.queue.append(symbol)
        self.queued.add(key)

    def _start(self) -> None:
        self.started = True
        self.started_at = self.now()
        for cid, service in SERVICES.items():
            if service in self.opened:
                continue
            try:
                if not self.session.openServiceAsync(
                    service, self.api.CorrelationId(cid)
                ):
                    self.warn(
                        "ref-open-" + service,
                        f"{service} 참조 서비스 요청 실패 · 기본 수신 유지",
                    )
            except Exception:
                LOGGER.warning(
                    "선택 참조 서비스 열기 실패: 서비스=%s", service, exc_info=True
                )
                self.warn(
                    "ref-open-" + service,
                    f"{service} 참조 서비스 미제공 · 기본 수신 유지",
                )

    def handle_service(self, msg: Any) -> bool:
        try:
            ids = {cid.value() for cid in msg.correlationIds()}
            service = scalar(msg, "serviceName")
            if service not in SERVICES.values():
                service = next((SERVICES[cid] for cid in ids if cid in SERVICES), None)
            if service is None:
                return False
            name = str(msg.messageType())
            if name == "ServiceOpened":
                self.opened.add(service)
                for symbol in list(self.symbols.values()):
                    self.queue_security(symbol)
            elif name in ("ServiceOpenFailure", "ServiceClosed", "ServiceDown"):
                self.opened.discard(service)
                if msg.hasElement("reason"):
                    LOGGER.warning(
                        "참조 서비스 실패: 서비스=%s 사유=%s",
                        service,
                        error_summary(msg.getElement("reason")),
                    )
                self.emit("reference_unavailable")
                self.queue.clear()
                self.queued.clear()
                self.cached_at.clear()
                self.values.clear()
                self.pending = None
                self.warn(
                    "ref-service-" + service,
                    f"{service} 참조 서비스 사용 불가 · 기본 수신 유지",
                )
            return True
        except Exception:
            LOGGER.warning("참조 서비스 상태 파싱 실패", exc_info=True)
            return False

    def tick(self) -> None:
        if not self.started:
            self._start()
        now = self.now()
        if len(self.opened) < 2:
            if now - self.started_at >= 20:
                self.warn(
                    "ref-service-timeout",
                    "참조 서비스 미준비 · 국가·Sec Type·ADV는 확인 전까지 보류합니다",
                )
            if now - self.started_at >= 30:
                self._start()
            return
        if not self.info_sent:
            self.info_attempts += 1
            self.info_cid = FIELD_INFO_CID + self.info_attempts - 1
            try:
                request = self.session.getService("//blp/apiflds").createRequest(
                    "FieldInfoRequest"
                )
                for field in FIELD_IDS:
                    request.getElement("id").appendValue(field)
                self.session.sendRequest(
                    request, correlationId=self.api.CorrelationId(self.info_cid)
                )
                self.info_sent = True
                self.info_at = now
            except Exception:
                LOGGER.error("참조 필드 정보 요청 실패", exc_info=True)
                self.info_sent = True
                self.info_at = now
                self.warn("ref-info", "필드 ID 검증 실패 · ADV 계산을 보류합니다")
            return
        if not self.info_done:
            if now - self.info_at >= 20:
                self.warn(
                    "ref-info-timeout", "필드 정보 응답 지연 · ADV 계산을 보류합니다"
                )
            if now - self.info_at >= 30:
                if self.info_attempts < 3:
                    self.info_sent = False
                else:
                    self.info_done = True
                    self.warn(
                        "ref-info-exhausted",
                        "필드 정보 재시도 한도 도달 · 확인된 필드만 사용합니다",
                    )
            return
        if self.pending:
            if now - self.pending[2] >= 20:
                self.warn(
                    "ref-data-timeout", "참조데이터 응답 시간 초과 · 기본 수신 유지"
                )
                self._finish()
            return
        if now >= self.refresh_at:
            self.refresh_at = now + 30
            for key, received in list(self.cached_at.items()):
                if now - received >= 300:
                    self.queue_security(self.symbols[key])
        if not self.queue or not self.mnemonics:
            return
        symbols = [self.queue.popleft() for _ in range(min(50, len(self.queue)))]
        self.values.clear()
        cid = self.next_cid
        self.next_cid += 1
        self.pending = (cid, symbols, now)
        self.requested = {
            reference_identifier(symbol).casefold(): symbol for symbol in symbols
        }
        self.requested.update({symbol.casefold(): symbol for symbol in symbols})
        try:
            request = self.session.getService("//blp/refdata").createRequest(
                "ReferenceDataRequest"
            )
            for symbol in symbols:
                request.getElement("securities").appendValue(
                    reference_identifier(symbol)
                )
            for mnemonic in self.mnemonics.values():
                request.getElement("fields").appendValue(mnemonic)
            self.session.sendRequest(request, correlationId=self.api.CorrelationId(cid))
        except Exception:
            LOGGER.error("참조데이터 요청 실패", exc_info=True)
            self.warn("ref-request", "참조데이터 요청 실패 · 기본 수신 유지")
            self._finish()

    def _field_info(self, msg: Any) -> None:
        if not msg.hasElement("fieldData"):
            return
        for entry in elements(msg.getElement("fieldData")):
            field_id = scalar(entry, "id")
            if field_id not in FIELD_IDS:
                continue
            if entry.hasElement("fieldError") or not entry.hasElement("fieldInfo"):
                if entry.hasElement("fieldError"):
                    LOGGER.warning(
                        "참조 필드 ID 오류: ID=%s 사유=%s",
                        field_id,
                        error_summary(entry.getElement("fieldError")),
                    )
                self.warn("ref-field-" + field_id, f"{field_id} 필드 ID 확인 실패")
                continue
            info = entry.getElement("fieldInfo")
            mnemonic = scalar(info, "mnemonic")
            if not mnemonic:
                continue
            LOGGER.info(
                "참조 필드 확인: ID=%s mnemonic=%s 설명=%s",
                field_id,
                mnemonic,
                scalar(info, "description"),
            )
            if field_id in EXPECTED_VOLUMES and mnemonic != EXPECTED_VOLUMES[field_id]:
                self.warn(
                    "ref-unit-" + field_id,
                    f"{field_id} 평균 거래량 필드 의미 확인 필요 · 해당 ADV 보류",
                )
                continue
            self.mnemonics[field_id] = mnemonic

    def _data(self, msg: Any) -> None:
        if not msg.hasElement("securityData"):
            return
        for entry in elements(msg.getElement("securityData")):
            security = scalar(entry, "security")
            key = str(security or "").strip().casefold()
            if key not in self.requested:
                try:
                    index = integer(scalar(entry, "sequenceNumber"))
                    if self.pending is None or not 0 <= index < len(self.pending[1]):
                        continue
                    key = self.pending[1][index].casefold()
                except (ValueError, TypeError, OverflowError):
                    self.warn(
                        "ref-alias", "참조 응답 종목을 요청과 연결할 수 없어 제외합니다"
                    )
                    continue
            else:
                key = self.requested[key].casefold()
            output = self.values.setdefault(
                key, {value: None for value in FIELD_IDS.values()}
            )
            if entry.hasElement("securityError"):
                LOGGER.warning(
                    "참조 종목 오류: 사유=%s",
                    error_summary(entry.getElement("securityError")),
                )
                self.warn(
                    "ref-security", "일부 종목의 참조데이터 권한·식별자를 확인하십시오"
                )
                continue
            if entry.hasElement("fieldData"):
                fields = entry.getElement("fieldData")
                for field_id, mnemonic in self.mnemonics.items():
                    if fields.hasElement(mnemonic):
                        output[FIELD_IDS[field_id]] = scalar(
                            fields, mnemonic, field_id in EXPECTED_VOLUMES
                        )
            if entry.hasElement("fieldExceptions"):
                for error in elements(entry.getElement("fieldExceptions")):
                    mnemonic = scalar(error, "fieldId")
                    for field_id, resolved in self.mnemonics.items():
                        if mnemonic in (field_id, resolved):
                            output[FIELD_IDS[field_id]] = None
                    LOGGER.warning(
                        "참조 필드 미제공: 필드=%s 사유=%s",
                        mnemonic,
                        error_summary(error.getElement("errorInfo"))
                        if error.hasElement("errorInfo")
                        else "",
                    )
                    self.warn(
                        "ref-field-error-" + str(mnemonic),
                        f"{mnemonic} 참조 필드 미제공 · 해당 표시 보류",
                    )

    def _finish(self) -> None:
        if self.pending is None:
            return
        for symbol in self.pending[1]:
            key = symbol.casefold()
            self.emit(
                "reference",
                security=symbol,
                fields=self.values.get(
                    key, {value: None for value in FIELD_IDS.values()}
                ),
            )
            self.cached_at[key] = self.now()
            self.queued.discard(key)
        self.pending = None
        self.values.clear()

    def handle_response(self, msg: Any, final: bool) -> bool:
        try:
            ids = {cid.value() for cid in msg.correlationIds()}
        except Exception:
            return False
        info = self.info_cid in ids
        data = self.pending is not None and self.pending[0] in ids
        if not info and not data:
            return False
        try:
            if info:
                self._field_info(msg)
            else:
                self._data(msg)
            if (
                msg.hasElement("responseError")
                or str(msg.messageType()) == "RequestFailure"
            ):
                for key in ("responseError", "reason"):
                    if msg.hasElement(key):
                        LOGGER.warning(
                            "참조 요청 거부: 사유=%s",
                            error_summary(msg.getElement(key)),
                        )
                if data:
                    self.values.clear()
                if info:
                    self.mnemonics.clear()
                self.warn("ref-response", "참조데이터 요청 거부 · 기본 수신 유지")
        except Exception:
            LOGGER.warning("참조데이터 응답 파싱 실패", exc_info=True)
            self.warn("ref-parse", "참조데이터 응답 파싱 실패 · 기본 수신 유지")
        self.event_info_seen |= info
        self.event_data_seen |= data
        if final:
            self.end_event(True)
        return True

    def end_event(self, final: bool) -> None:
        """최종 이벤트의 모든 메시지를 처리한 뒤 결과를 확정한다."""
        if final:
            if self.event_info_seen:
                self.info_done = bool(self.mnemonics) or self.info_attempts >= 3
                for field_id in FIELD_IDS.keys() - self.mnemonics.keys():
                    self.warn(
                        "ref-unresolved-" + field_id,
                        f"{field_id} 필드 확인 불가 · 해당 표시 보류",
                    )
            if self.event_data_seen:
                self._finish()
        self.event_info_seen = False
        self.event_data_seen = False
