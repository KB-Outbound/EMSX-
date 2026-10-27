"""화면 표시용 번역과 제품 식별자를 정의한다. API 값은 변경하지 않는다."""

DESK_TITLE = "KB증권 해외주식영업부 EMSX 모니터"
APP_TITLE = "KB증권 해외주식영업부 - EMSX 모니터 - 정동화"

TEXT = {
    "TRADE_DATE_OUT_OF_RANGE": (
        "생성일이 오늘·전일 범위에 없거나 확인되지 않아 전송을 차단했습니다"
    ),
    "STARTED": "구독 정상",
    "PENDING": "대기 중",
    "RESERVED": "전송 예약",
    "QUEUED": "배치 전송 대기",
    "SENT": "전송됨 · 응답 대기",
    "UNKNOWN": "결과 불확실 · 수동 확인 필요",
    "BLOCKED": "처리 차단",
    "DUPLICATE_BLOCKED": "중복 처리 차단",
    "OBSERVE_ONLY": "조회 전용",
    "API_ERROR": "API 오류",
    "AUDIT_FAILED": "감사 기록 실패",
    "API_RESPONSE_OK_UNVERIFIED": "API 응답 정상 · 최종 상태 확인 필요",
    "NOT_ORD_PEND": "ORD-PEND 상태가 아닙니다",
    "INVALID_SEQUENCE_OR_QTY": "Sequence 또는 Shares가 올바르지 않습니다",
    "INVALID_OR_OVER_LIMIT_QTY": (
        "Shares가 유효하지 않거나 한도를 초과했습니다"
    ),
    "ACCOUNT_EMPTY": "Account가 비어 있습니다",
    "INVALID_SIDE": "허용되지 않은 Side입니다",
    "MISSING_TICKER": "Ticker가 없습니다",
    "MISSING_ORDER_TYPE": "Order 유형이 없습니다",
    "MISSING_TIF": "TIF가 없습니다",
    "TEAM_MISMATCH": "승인된 구독 팀과 일치하지 않습니다",
    "BETA_WRITES_LOCKED": "BETA 전송이 차단되어 있습니다",
    "PRODUCTION_WRITES_LOCKED": "PROD 전송이 차단되어 있습니다",
    "NO_LIVE_WRITES_IN_DEMO": "DEMO에서는 실제 전송할 수 없습니다",
    "FEED_NOT_HEALTHY": "데이터 수신 상태가 정상적이지 않습니다",
    "Operator disarmed": "운영자가 자동 수신확인을 해제했습니다",
    "Armed for newly arriving E2E orders only": (
        "신규 E2E Order만 자동 수신확인합니다"
    ),
    "Already attempted; no automatic retry": (
        "이미 처리한 Order로 자동 재시도하지 않습니다"
    ),
    "Initial snapshot / pre-arm order": "초기 수신 또는 활성화 이전 Order",
    "Not healthy/armed": (
        "데이터 수신 비정상 또는 자동 수신확인 해제 상태"
    ),
    "Not armed/ready": "자동 수신확인 해제 또는 준비 미완료",
    "No auto ACK on later dynamic updates": (
        "후속 갱신에서는 자동 수신확인하지 않습니다"
    ),
    "Local journal already contains this sequence": (
        "저널에 이미 기록된 Sequence입니다"
    ),
    "Persisted before API request": (
        "API 요청 전에 영속 예약을 저장했습니다"
    ),
    "ACK request pending; not yet confirmed": (
        "Ack 요청 전송 후 응답을 기다리고 있습니다"
    ),
    "ACK API failed; verify manually": (
        "Ack API 오류로 수동 확인이 필요합니다"
    ),
    "ACK outcome uncertain; reconcile in EMSX": (
        "결과가 불확실합니다. EMSX에서 대조하십시오"
    ),
    "ACK timeout; manual reconciliation required": (
        "Ack 응답 시간 초과로 수동 대조가 필요합니다"
    ),
    "DEMO: synthetic data, no Bloomberg connection": (
        "DEMO: 모의 데이터 사용 · Bloomberg 연결 없음"
    ),
    "Session ended": "세션 종료",
    "ELIGIBLE_E2E_STATUS_ONLY": "E2E 상태 기준 충족",
    "BETA_APPROVED": "BETA 전송 허용",
    "PROD_ADMIN_FLAGS_PRESENT_NOT_ENTITLEMENT_PROOF": (
        "PROD 관리자 설정 충족 · 전송 권한은 별도 확인"
    ),
}


def localize(value: object) -> str:
    """알려진 내부 상태를 번역하고 외부 API 원문은 진단을 위해 보존한다."""
    text = str(value)
    if text in TEXT:
        return TEXT[text]
    for prefix, translated in (
        ("Order status now ", "현재 Order Status: "),
        ("ACK transmit uncertain / ", "Ack 전송 결과 불확실 / "),
    ):
        if text.startswith(prefix):
            return translated + text[len(prefix) :]
    return text
