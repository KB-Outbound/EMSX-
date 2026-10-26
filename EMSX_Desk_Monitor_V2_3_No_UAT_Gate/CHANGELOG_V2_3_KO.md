# V2.3 변경사항: UAT Reference 필수 입력 제거

- `write_authorized()`에서 `UAT_REFERENCE_REQUIRED` 조건을 삭제했습니다.
- `uat_reference` 키를 새 기본 설정 및 설정 예시에서 제거했습니다.
- **기존 `%LOCALAPPDATA%\EMSX_Desk_Monitor_HDGB\admin_config.json`에 `uat_reference`가 남아 있어도 문제없이 실행됩니다.** 해당 키는 무시합니다.
- 아래의 별도 승인 안전장치는 그대로 유지합니다.
  - `production_write_enabled`, `production_team_hdgb_approved`, `production_all_accounts_approved`: 기본 false
  - TEAMVIEW 일치, 스트림 정상 / 스냅샷 완료 / 시퀀스 gap 없음
  - 사용자의 명시적 ARM 확인, 기존 주문 제외, ORD-PEND만 평가
  - 주문 정보 검증, 주문수량 제한, 로컬 중복 방지 및 감사 로그
- **주의**: 관리자 플래그가 있다고 Bloomberg 실제 SellSideAck 권한까지 확보되는 것은 아닙니다.
- 변경은 애플리케이션 코드이며 Windows에 기존 배포된 EXE에는 자동 반영되지 않습니다. EXE 재빌드가 필요합니다.
