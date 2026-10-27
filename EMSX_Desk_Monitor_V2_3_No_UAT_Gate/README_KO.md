# EMSX Desk Monitor V2 (HDGB TEAMVIEW + E2E Auto ACK)

**개발·검증용 파일럿. 금융거래 프로덕션 운용 승인 제품이 아닙니다.**

## 왜 V2를 만들었나

V1 Desk Monitor는 `TEAMVIEW=HDGB`에서 실제 고객 EMSX 주문이 Order/Route 양쪽에 정상 표시된 반면, 별도 Auto ACK는 개인 구독(`/order?fields=...`)만 사용했습니다. V2는 V1의 **정상 동작한 Order/Route TEAMVIEW 구독 구조를 그대로 사용**하며 Auto ACK 기능을 **같은 Windows 앱의 별도 탭**에 합쳤습니다.

| 요소 | V2 동작 |
|---|---|
| 주문 구독 | `//blp/emapisvc/order;team=HDGB?fields=...` (PROD) |
| 라우트 구독 | `//blp/emapisvc/route;team=HDGB?fields=...` (PROD) |
| 화면 | Orders / Routes / Brokers / Auto ACK (E2E) / Activity |
| 계좌 대상 | 계좌별 허용목록 없이 전체 모니터링. ACK는 기본적으로 빈 계좌 제외 |
| 자동 ACK | **신규 `ORD-PEND` E2E 주문** 중 규칙 통과 건만 |
| 자동 ACK 대상 제외 | 기존 초기 스냅샷, ARM 이전 주문, `NEW`, `WORKING`, 상태 변경만 있는 기존 주문 |
| API 요청 | Bloomberg `SellSideAck` (일반 FIX `ExecutionReport 35=8` 전송 아님) |
| PROD 주문 변경 | **기본 완전 차단**, 관리자 승인 플래그+직접 ARM 필요. UAT 번호 입력 검증은 V2.3부터 없음 |

`HDGB`는 이 프로젝트에서 **TEAMVIEW 이름**입니다. `EMSX_BROKER=HDGB` 필터가 아닙니다. TEAMVIEW에서 주문이 보인다는 것만으로 다른 팀원 소유 주문에 대한 SellSideAck 권한까지 증명되지는 않습니다. Bloomberg EMSX Trade Desk에 사용자 UUID 및 팀 권한을 확인하세요.

## 우선 PROD 읽기 전용으로 실행

프로젝트를 다음 경로에 풀었다고 가정합니다.

```
C:\Users\SP90214\Desktop\EMSX_Desk_Monitor_V2_Integrated
```

CMD에서:

```cmd
cd /d "C:\Users\SP90214\Desktop\EMSX_Desk_Monitor_V2_Integrated"
py -3 emsx_desk_monitor.py
```

1. `Environment` → `PROD` 선택
2. `TEAMVIEW name` → `HDGB` (기본값)
3. `START MONITOR` 클릭
4. `Orders` 및 `Routes`에 기존 Desk Monitor V1과 같은 실제 주문이 보이는지 확인
5. `Auto ACK (E2E)` 탭에서 `ORD-PEND` 상태와 `ACK Decision`을 확인. **ACK는 여전히 OFF**
6. `ORDER: STARTED / SNAPSHOT DONE`, `ROUTE: STARTED / SNAPSHOT DONE` 모두 확인

처음 실행 시 관리자 설정 파일이 자동 생성됩니다.

```
%LOCALAPPDATA%\EMSX_Desk_Monitor_HDGB\admin_config.json
```

기본 내용은 대략 다음과 같습니다. `false`를 그대로 유지하십시오.

```json
{
  "team_name": "HDGB",
  "max_shares": 10000,
  "allowed_sides": ["BUY", "SELL"],
  "require_nonblank_account": true,
  "production_write_enabled": false,
  "production_team_hdgb_approved": false,
  "production_all_accounts_approved": false,
  "beta_write_enabled": false
}
```

## ACK 관련 운용 전 필수 검증

- Bloomberg에서 **E2E 신규 주문으로 발생한 `ORD-PEND`**임을 확인. 일반 FIX 주문의 접수 확인(FIX ExecutionReport)은 구현되어 있지 않음
- `SellSideAck`가 **HDGB TEAMVIEW에서 보이는 다른 팀원 소유 주문**에도 허용되는지 계정별/UUID별 권한 확인
- BETA에서 `new order → ARM → ACK request → API reply → EMSX order status change` UAT 검증. 사용자의 UAT 권한 및 회사 승인 필요
- `NEW/WORKING`, cancel/replace, 대량주문, 빈 계좌, 상태 갱신만 수신, 연결 재시작/끊김, 다중 PC, 중복 이벤트, ACK response timeout 테스트
- **팀 전체에서 단 한 대만 ACK 송신 담당으로 지정**. 다른 팀원은 Monitor(READ-ONLY) 전용. SQLite 로그는 같은 PC의 동일 사용자 저장소 내 중복만 방지하며 중앙 공유 락이 없음
- `_OK_UNVERIFIED`란 Bloomberg `STATUS=0` API 응답만 받았다는 뜻으로 실제 주문 상태 전환까지 보장하지 않음
- 이 프로그램은 감사 및 IT 검토 전에는 PROD에서 ACK를 활성화하지 말 것

PROD를 켰다는 이유만으로 ACK가 전송되는 일은 없습니다. 승인 플래그는 프로그램이 스스로 설정하지 않습니다. 엔타이틀먼트/내부 승인 후 **인가된 관리자**가 설정을 관리하도록 설계했습니다. **V2.3부터 `uat_reference`는 필요하지 않습니다.** 기존 설정 파일에 해당 키가 남아 있어도 무시합니다. BETA/PROD 모두 기본 전송 잠금 상태입니다.

프로그램에서 무작정 전송 잠금을 해제해 테스트하지 마세요. 상태 확인만으로는 실제 ACK 적격성을 입증할 수 없습니다.

## ACK 재시도 및 로그

- ACK 전 `ack_journal.sqlite3`에 `RESERVED` 기록 → API 요청 전송 → API 응답 상태 기록
- 동일 프로그램 사용자 로컬 DB에서 같은 `(environment, team, EMSX_SEQUENCE)` 재전송 금지
- 타임아웃, 연결 중단, 결과 불확실 시 `UNKNOWN`으로 남기고 자동 재시도 금지
- `%LOCALAPPDATA%\EMSX_Desk_Monitor_HDGB\ack_audit.jsonl`에 ARM/DISARM 및 요청/응답 감사 기록
- 기존 **Auto ACK V2는 통합본으로 이전 후 반드시 종료**해야 합니다. 다른 EXE와 함께 ACK 송신 금지
- 로컬 로그/DB는 고객 주문 및 집행 관련 정보일 수 있으므로 승인된 내부 PC에서만 보관

## Windows EXE 빌드 / 동료 배포

인터넷 사용 가능하고 IT가 승인한 **Windows 개발 PC**에서 `build_windows.bat` 실행. PyInstaller/blpapi를 설치하고 오프라인 테스트 통과 후 다음 EXE를 만듭니다.

```
dist\EMSX_Desk_Monitor_HDGB_V2.exe
```

동료 PC는 Python을 설치할 필요가 없지만 Bloomberg Terminal/EMSX API 자격과 필수 런타임이 필요합니다. 팀원에게는 우선 **모니터링 전용 EXE**로 배포하고, 승인된 전용 PC 외에는 운영 전송 플래그를 켜지 마십시오.

## 알려진 한계

1. Bloomberg PROD 실거래 연결·쓰기 권한·E2E `SellSideAck` 최종 체결효과는 제작 환경에서 **검증 불가**. 오프라인 테스트와 DemoWorker의 데이터 흐름을 검증했으며 실제 Tk 화면은 미검증입니다.
2. 사용 가능한 EMSX TEAMVIEW만 구독하며 임의 FIX Gateway 메시지를 모두 조회하거나 ACK하지 않음.
3. 별도 팀원 PC 간 글로벌 중복 전송 차단(중앙 락) 미구현.
4. 계좌 정보가 없는 경우 또는 기본 최대수량 10,000주 초과건은 자동 ACK 제외. 대상 조건은 실제 주문 유형과 내부통제 정책에 따라 조정이 필요함.
5. Bloomberg API의 `STATUS=0` 성공 해석 및 팀원 계좌 ACK 권한은 BETA/UAT 확인 필요.

공식 참고:
- https://emsx-api-doc.readthedocs.io/en/latest/programmable/emsxFeatures.html
- https://emsx-api-doc.readthedocs.io/en/latest/programmable/emsxSubscription.html
- https://emsx-api-doc.readthedocs.io/en/latest/programmable/requestResponseSs.html

## 안정성 개선판

재연결, 동시성 보호, Ack 안전장치, 검증 범위와 기존 설정 이관은
[2.4 변경 안내](CHANGELOG_V2_4_KO.md)를 참조하십시오.
추가 모듈 `synchronization.py`를 기존 실행 파일들과 함께 배포해야 합니다.

## 2.4.1 화면 및 제품 정보

최상위 창 제목은 `KB증권 해외주식영업부 - EMSX 모니터 - 정동화`입니다.
화면 내부 제목과 하단에는 담당자명을 표시하지 않으며 실행 환경과 부서명을 표시합니다.
화면은 다크 테마를 사용합니다.
Order, Account, Ticker, Sequence, Side, Shares, Price, Broker, Status,
Event Log 등 거래·기술 용어와 원본 데이터는 영어로 유지하고 운영 안내는 한국어로 표시합니다.
불필요한 상단·하단 설명과 브랜드성 문구는 제거했습니다.

Bloomberg 구독에 필요한 `TEAMVIEW` API 구문과 기존 `team_name` 설정,
로컬 설정·저널 경로는 유지합니다. 화면에서는 `구독 팀`으로 표시합니다.
내부 Ack 상태 코드는 DB·연동 호환성을 위해 유지하고 화면에서만 번역합니다.
외부 API 응답과 운영체제 예외의 원문은 정확한 장애 분석을 위해 보존합니다.

`build_windows.bat`는 `windows_version_info.txt`를 읽어 제품명과 파일 설명을 설정하며,
결과물은 `dist\EMSX_Monitor.exe`입니다. 파일명은 Windows 배포 편의를 위해 영문을 사용합니다.
`ui_text.py`와 `windows_version_info.txt`도 배포·빌드 폴더에 포함해야 합니다.
Windows 빌드 콘솔은 UTF-8을 사용합니다.


## 2.4.2 데스크 표시 기준 및 필드

- 모든 거래 표와 CSV에서 Sequence 열을 제거했습니다. 내부 `EMSX_SEQUENCE`와
  `API_SEQ_NUM`은 주문 식별, 부분 갱신 병합, Ack 중복 방지와 구독 연속성 검사에 유지합니다.
- Order·Route·자동 수신확인 표의 상태 열은 `Line Status`로 표시합니다.
  원본 `EMSX_STATUS`를 사용하며 Assign, Cancel, Part-filled, Working 등의 표기만 정리합니다.
  별도의 미확인 `EMSX_LINE_STATUS` 필드를 요청하거나 상태를 추정하지 않습니다.
  미지의 상태는 원문을, 미수신 상태는 빈 값으로 표시합니다.
- Instruction은 2.5.0부터 고객 비고 필드 `EMSX_NOTES`를 사용합니다.
  `EMSX_HAND_INSTRUCTION`의 처리 플래그는 표시하지 않습니다.
- 경고 기준은 2.5.0의 Route 지연·Route 후 No Fill 조건을 따릅니다.
- 내부 화면 제목과 하단은 `PROD | KB증권 해외주식영업부 EMSX 모니터` 형식을 사용합니다.
  담당자명은 최상위 창 제목과 제품명·파일 설명에만 남기며 하단 문의 표기는 제거했습니다.

기존 테스트 59개와 필드·상태 전이 테스트 3개, 총 62개를 통과했습니다.
실제 Bloomberg 구독의 신규 필드 제공 여부와 권한은 연결 가능한 승인 환경에서 확인해야 합니다.
실제 Windows 화면 및 실행 파일 빌드는 현재 환경에서 검증하지 않았습니다.


## 2.4.3 KB 색상 체계

`kb_theme.py`에 공통 색상과 구성 요소 스타일을 분리했습니다. 배경은 `#121212`,
표·입력창은 `#1E1E1E`, 기본 강조색은 KB Yellow `#FFCC00`입니다.
금색은 주요 버튼·선택 탭·집계 지표·정상 상태 배지에 적용합니다.
비활성 버튼은 별도 저채도 색상으로 구분하며 기존 활성화 조건을 유지합니다.
오류와 Rejected 상태는 붉은색과 설명 문구로 구분합니다.

표는 32픽셀 행 높이와 교차 배경, 은은한 테두리, 우측 숫자 정렬,
가로·세로 스크롤을 사용합니다. Working·Part-filled·Assign은 금색으로,
종료 상태는 회색으로 표시하고 경고 행은 배경과 점검 문구를 함께 표시합니다.
조회·필터링으로 행 순서나 경고 상태만 바뀌어도 행 강조를 갱신합니다.
운영체제 기본 확인창과 파일 선택창은 운영체제의 모양을 따릅니다.

최상위 창 제목·제품 정보의 담당자명과 담당자명이 없는 내부 환경 제목은 유지합니다.
실행 파일 빌드 시 `kb_theme.py`를 함께 배포하십시오. 기존 설정·DB 변경은 없습니다.
이 변경은 공식 브랜드 가이드 인증이나 실제 Windows 화면 검증을 의미하지 않습니다.


## 2.5.0 날짜·Basket·고객 지시·알람

### 거래일과 생성 시각

일반 실행의 기본 환경은 PROD입니다. 시작 버튼을 누르기 전에는 연결하지 않으며,
PROD 연결 확인창과 기본 전송 차단·수동 자동 Ack 활성화 절차는 유지합니다.
`run_demo.bat` 또는 `python emsx_desk_monitor.py --demo`로 실행하면 DEMO를 선택합니다.

Create Time은 원본 `EMSX_DATE`와 `EMSX_TIME`을 사용합니다. `EMSX_TIME` 숫자는
자정 이후 초로 해석합니다. 원본 FIX 메시지 시각이 제공되지 않으므로 이를 추정하지 않습니다.
시각이 없으면 날짜만 표시하며 날짜가 없거나 잘못된 경우 수신 시각으로 보충하지 않습니다.
서울 현재 날짜와 원본 `EMSX_DATE`를 비교하여 오늘·전일만 표시합니다.
이 기준은 거래소 영업일·전영업일 계산이 아닌 달력 날짜이며,
원본 EMSX 날짜·시각을 임의로 시간대 변환하지 않습니다.
오늘만 보는 필터도 제공하며 자정이 지나면 범위를 다시 계산합니다.
날짜를 확인할 수 없는 주문은 `Date Missing` 집계로 따로 표시합니다.
실제 데스크의 원본 EMSX 거래일·시각 기준이 이 비교 기준과 일치하는지 운영 검증이 필요합니다.

Bloomberg 구독은 순번·부분 갱신의 연속성을 위해 그대로 수신하고 표시 단계에서 제한합니다.
Order·Route·자동 Ack 표와 Broker 집계·CSV에 날짜 범위를 적용합니다.
자동 Ack는 화면 필터와 별개로 오늘·전일 생성일 검증을 전송 직전에 다시 수행합니다.
오래된 날짜·미래·누락·손상 날짜는 전송을 차단하며 이후 부분 갱신만으로 자동 재시도하지 않습니다.

### 고객 비고와 Basket

Instruction은 `EMSX_NOTES` 고객 비고를 원문 표시합니다. TWAP·MOC 등의 비고를 보존하며,
비고 전체가 MAN·AUTO·MANUAL·AUTOMATIC이면 빈 값으로 표시합니다.
처리 방식 필드의 값을 고객 지시로 대체하지 않습니다. 비고 문장 내부 단어를 임의 삭제하지 않습니다.
실제 데스크가 고객 지시를 다른 사용자 정의 필드에 저장한다면 해당 원본 필드 매핑의 추가 확인이 필요합니다.

Basket Name은 `EMSX_BASKET_NAME`입니다. 대소문자를 구분하지 않는 부분 검색과
최신순·과거순·Basket별 최신순 정렬을 지원합니다. 동일 Basket 내에서는 생성 시각 내림차순입니다.
동일 시각에는 내부 주문 번호로 표시 순서를 안정화하며 이것이 FIX 도착 순서를 증명하지는 않습니다.
Route의 생성 시각·Basket·Instruction은 상위 Order 기준입니다.
날짜·Basket·비고의 부분 갱신 누락은 기존 값을 보존하고 명시적 빈 값은 반영합니다.

### Alert 조건과 소리

- Unrouted Warning: `ORD-PEND`가 아닌 NEW·ASSIGN·ASSIGNED·WORKING 상태가 관측된 후,
  Route가 없는 상태로 지정한 분이 경과한 경우입니다. 상태 변경 후부터 관측 시간을 계산합니다.
  최종 Route 삭제 시에는 지연 관측 시간을 다시 시작합니다.
  별도 FIX ACK 이벤트는 받지 않으므로 EMSX 상태를 이용한 대체 판정이며 FIX 수신을 독립적으로 증명하지 않습니다.
- No-Fill Alert: 활성 Route가 있고 Order·Route의 체결량이 모두 확인된 0이며,
  최초 활성 Route 관측 이후 지정한 분이 경과한 경우입니다. 부분 체결 주문은 대상에서 제외합니다.
  누락·손상 체결량을 0으로 간주하지 않습니다. 종료 주문에서는 발생하지 않습니다.
- Route Closed Alert: 모든 Route가 비활성인데 잔량이 남은 상태가 기준 시간을 초과한 경우입니다.
- Ack Pending은 정보성 상태이며 반복 음향 Alert에 포함하지 않습니다.

시각 Alert 배지와 행 강조는 데이터 도착 여부와 무관하게 화면 갱신 때 확인합니다.
활성 경고가 있으면 즉시 알림음을 요청하고 미해소 시 최대 30초에 한 번 반복합니다.
해소 후 재발하면 다시 알립니다. 검색·Account·Broker·Basket 필터는 음향 감시 범위를 줄이지 않고,
Create Time 날짜 필터는 감시 범위에도 적용합니다. 상단 집계도 검색 전 날짜 범위를 기준으로 합니다.
모니터링 중지·수신 오류·순번 단절·초기 수신 미완료 중에는 자동 음향 알림을 중단합니다.

Windows에서는 `winsound.PlaySound`의 비동기 시스템 경고음을 사용하고 실패하면 Tk 알림으로
대체합니다. 다른 운영체제에서는 Tk 알림을 사용합니다. `알림음` 선택으로 음향만 끌 수 있고
`알림음 확인` 버튼으로 수동 재생을 요청할 수 있습니다. 재생 실패는 Event Log에 기록합니다.
OS 음소거·음량·장치·시스템 사운드 설정에 따른 실제 가청 여부는 프로그램이 보장하지 않습니다.
현재 환경에서는 음향 호출·대체 경로를 모의 검증했으며 실제 스피커 출력은 미검증입니다.

### 검증과 배포

총 73개 테스트가 통과했습니다. 날짜 경계·필터·시간 정렬·Basket 그룹·고객 비고·상태 전이·
알림 주기·Windows 비동기 음향 대역·자동 Ack 날짜 차단과 표시 계층 조합을 포함합니다.
표 대역을 사용한 테스트이며 실제 Windows 화면·오디오·Bloomberg 신규 필드 수신을 검증한 것은 아닙니다.
`order_fields.py`, `alerting.py`, 기존 `kb_theme.py`, `ui_text.py`, `synchronization.py`를
함께 배포해야 합니다. 기존 JSON 키와 SQLite 스키마의 변경은 없습니다.
