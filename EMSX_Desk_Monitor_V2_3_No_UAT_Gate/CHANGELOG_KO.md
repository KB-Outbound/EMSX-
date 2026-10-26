# V2 주요 변경 사항

- 기존 V1 Desk Monitor의 Order/Route + TEAMVIEW 구독 구조 유지
- `HDGB`를 기본 TEAMVIEW 이름으로 설정 (`EMSX_BROKER` 필터 아님)
- `Auto ACK (E2E)` 탭 추가
- 계좌별 Allowlist 입력 제거. `ORD-PEND` E2E 주문만 평가하며 빈 계좌·기본 한도 초과건은 차단
- **쓰기 기능 기본 잠금**, BETA/PROD 각각 관리자 플래그 + ARM 확인 필요
- 연결·구독·API 시퀀스 에러 발생 시 Fail-closed
- 초기 스냅샷·ARM 이전 기존 주문 자동 ACK 금지
- 로컬 SQLite 중복 전송 방지 및 JSONL 감사 기록, 다른 PC 간 중앙 중복방지 기능은 미구현
- TEAMVIEW 수신과 `SellSideAck` 권한은 별도 검증 필요
- 주문/라우트 테이블 갱신 시 안정된 행 ID를 사용하여 대량 주문 환경에서 깜빡임 완화
