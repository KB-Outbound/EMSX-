# EMSX Desk Monitor V2.2: API_SEQ_NUM 시퀀스 진단 수정

## 어떤 증상을 수정하나요?

`ORDER: STARTED / SNAPSHOT DONE`, `ROUTE: STARTED / SNAPSHOT DONE`인데
`API SEQ GAPS N (reconnect required)` 때문에 `ARM AUTO ACK`가 비활성화되는 증상을 분석합니다.

V2.1 이전의 시퀀스 검사 코드는 `EVENT_STATUS=4/6/7/8`만 계산하므로,
`EVENT_STATUS=1`(Heartbeat) 또는 `11`(Initial Paint End)에도 시퀀스 값이 붙는 경우
정상 증가를 '누락'으로 오판할 수 있습니다.

V2.2는 *API_SEQ_NUM이 실제 포함된 모든 EMSX 구독 메시지*를 시퀀스 검사에 반영합니다.
번호가 누락되거나 역행하는 경우 **기존과 동일하게 AUTO ACK를 차단**합니다.

**주의:** 기존 화면의 `API SEQ GAPS 3`은 세 차례의 번호 점프가 감지되었다는 뜻이지,
누락된 주문 건수가 3건이라는 의미가 아닙니다. 실제 누락 또는 정상 이벤트 무시로 인한
오탐인지는 진단 로그로 확인해야 합니다.

## 실행

1. 기존 프로그램 종료 (STOP 후 프로세스 종료). PROD Auto ACK는 OFF로 유지.
2. V2.2 전체 폴더를 별도 위치에 압축 해제합니다.
3. CMD에서 아래를 실행합니다.

```cmd
cd /d "C:\Users\SP90214\Desktop\EMSX_Desk_Monitor_V2_2_SequenceFix"
py -3 emsx_desk_monitor.py
```

4. `PROD` + `TEAMVIEW=HDGB` → `START MONITOR`.
5. `ORDER` 및 `ROUTE`가 `STARTED / SNAPSHOT DONE`인지 확인.
6. `Activity / Diagnostics`에서 `WARNING: sequence gap O/R old -> new (status=N)` 또는
   `ERROR: API_SEQ_NUM discontinuity ... (EVENT_STATUS=N)` 메시지 있는지 확인.
7. 오류 메시지가 나오면 자동 ACK는 OFF로 두고 API 연결 상태와 Bloomberg EMSX Trade Desk를 통해 조사하세요.

### 지표가 정상이라면

- `API SEQ GAPS`가 없고 `STREAM READY`이면 구독 상태 검증을 통과한 것입니다.
- 이것만으로 ACK가 허가되는 것은 아닙니다. 별도의 E2E 권한, BETA UAT, 관리자 승인,
  사내 통제 확인이 있어야 하며, E2E `SellSideAck`는 일반 FIX ACK가 아닙니다.

## 빌드

`build_windows.bat` 실행 → `dist\EMSX_Desk_Monitor_HDGB_V2_2.exe` 생성 시도.
Windows에서 Python, blpapi, PyInstaller를 설치할 수 있는 회사 승인 빌드 PC 필요.

## 수정 내역

- `bloomberg_feed.py`: 이벤트 종류와 무관하게 포함된 API_SEQ_NUM을 검사. O/R 개별 추적.
- `desk_core.py`: GUI 데이터 처리 단계도 Heartbeat/초기 스냅샷 종료 이벤트의 번호를 추적.
- 실제 시퀀스 불연속/역행은 여전히 fail-closed (ACK 차단).
- `tests/test_sequence_monitor.py`: 정상 Heartbeat/초기 스냅샷 종료/실제 누락/역행 등 테스트 추가.

**검증 수준:** 오프라인 단위 테스트만 통과했습니다. Bloomberg PROD에서 시퀀스 갭이
실제 해소되는지는 확인되지 않았습니다. 실거래 ACK 전송은 실행하지 않았습니다.
