# EMSX Desk Monitor V2 (HDGB TEAMVIEW + E2E Auto ACK)

현재 버전은 **2.7.2**입니다. 시장 상태·휴장 안내·당일 Excel 보고서는
[운영 편의 기능 안내](OPERATIONS_KO.md)를 참조하십시오. 자동 Ack의 현재 실행 기준은
[전체 대기 Order 단일 요청 전송](#266-전체-대기-order-단일-요청-전송)을 적용하십시오.
시간 경고는 [지정 국가 검증과 MOC 예외](#268-지정-국가-검증과-moc-no-fill-예외)를 적용합니다.

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

2.6.0부터 Unrouted Warning·No-Fill Alert에는 아래의 지정 국가 제한을 추가 적용합니다.

- Unrouted Warning: `ORD-PEND`가 아닌 NEW·ASSIGN·ASSIGNED·WORKING 상태가 관측된 후,
  Route가 없는 상태로 지정한 분이 경과한 경우입니다. 상태 변경 후부터 관측 시간을 계산합니다.
  최종 Route 삭제 시에는 지연 관측 시간을 다시 시작합니다.
  별도 FIX ACK 이벤트는 받지 않으므로 EMSX 상태를 이용한 대체 판정이며 FIX 수신을 독립적으로 증명하지 않습니다.
- No-Fill Alert: 활성 Route가 있고 Order·Route의 체결량이 모두 확인된 0이며,
  최초 활성 Route 관측 이후 지정한 분이 경과한 경우입니다. 부분 체결 주문은 대상에서 제외합니다.
  누락·손상 체결량을 0으로 간주하지 않습니다. 종료 주문에서는 발생하지 않습니다.
  2.6.8부터 고객 Instruction에 대소문자와 관계없이 `MOC`가 포함되면 검사 대상에서 제외합니다.
- Route Closed Alert: 모든 Route가 비활성인데 잔량이 남은 상태가 기준 시간을 초과한 경우입니다.
- Ack Pending은 정보성 상태이며 반복 음향 Alert에 포함하지 않습니다.

시각 Alert 배지와 행 강조는 데이터 도착 여부와 무관하게 화면 갱신 때 확인합니다.
활성 경고가 있으면 즉시 알림음을 요청하고 미해소 시 최대 30초에 한 번 반복합니다.
해소 후 재발하면 다시 알립니다. 검색·Account·Broker·Basket 필터는 음향 감시 범위를 줄이지 않고,
Create Time 날짜 필터는 감시 범위에도 적용합니다. 상단 집계도 검색 전 날짜 범위를 기준으로 합니다.
모니터링 중지·수신 오류·순번 단절·초기 수신 미완료 중에는 자동 음향 알림을 중단합니다.

Windows에서는 2.6.0부터 `winsound.PlaySound`로 전용 두 음 반복 경보음을 비동기 재생하며
실패하면 Tk 알림으로 대체합니다. 다른 운영체제에서는 Tk 알림을 사용합니다. `알림음` 선택으로 음향만 끌 수 있고
`알림음 확인` 버튼으로 수동 재생을 요청할 수 있습니다. 재생 실패는 Event Log에 기록합니다.
OS 음소거·음량·장치·시스템 사운드 설정에 따른 실제 가청 여부는 프로그램이 보장하지 않습니다.
현재 환경에서는 음향 호출·대체 경로를 모의 검증했으며 실제 스피커 출력은 미검증입니다.

### 검증과 배포

총 73개 테스트가 통과했습니다. 날짜 경계·필터·시간 정렬·Basket 그룹·고객 비고·상태 전이·
알림 주기·Windows 비동기 음향 대역·자동 Ack 날짜 차단과 표시 계층 조합을 포함합니다.
표 대역을 사용한 테스트이며 실제 Windows 화면·오디오·Bloomberg 신규 필드 수신을 검증한 것은 아닙니다.
`order_fields.py`, `alerting.py`, 기존 `kb_theme.py`, `ui_text.py`, `synchronization.py`를
함께 배포해야 합니다. 기존 JSON 키와 SQLite 스키마의 변경은 없습니다.


## 2.5.1 초기 구독 거부와 필드 파싱 복구

### 확인된 오류 단계

제공된 PROD·BETA 로그는 Order 구독이 `SubscriptionFailure`, `errorCode=11`로
거부됨을 보여 줍니다. 원래 코드가 메시지 전체를 250자로 잘라 핵심 `reason.description`이
누락되었습니다. 이 코드만으로 특정 필드 오류나 권한 문제를 확정하지 않습니다.
DEMO 성공은 실제 Bloomberg 구독 권한·필드 호환성을 검증하지 않습니다.

### 기본 구독과 선택 필드 격리

초기 Order 요청을 원본의 기본 필드 집합으로 되돌렸습니다. Order 식별자는 서비스가
기본으로 제공하는 `EMSX_SEQUENCE`를 계속 읽습니다. 날짜 검증에 필요한 `EMSX_DATE`는 유지합니다.
Order·Route 기본 구독이 시작되고 두 초기 수신이 완료된 후에만
Instruction(`EMSX_NOTES`), Basket(`EMSX_BASKET_NAME`), 시각(`EMSX_TIME`)을 각각
독립된 선택 구독으로 요청합니다. 선택 필드 하나의 거부는 해당 열만 미제공 처리하고,
기본 구독의 준비 상태나 자동 Ack 활성화 상태를 변경하지 않습니다.

선택 구독 순번은 기본 Order·Route 순번 검사와 분리합니다. 선택 갱신만으로
기본 Order를 만들거나 자동 Ack를 실행하지 않으며 기본 Order와 식별자가 일치할 때만
화면에 병합합니다. 지원되지 않는 필드는 다른 필드 값으로 대체하지 않고 빈 값과 안내로 표시합니다.
선택 필드 캐시는 100,000개 주문으로 제한하고 종료·재연결 시 비웁니다.

환경이 병렬 구독을 지원하는지는 실제 Bloomberg에서 확인해야 합니다. 선택 구독 요청 후
기본 구독이 종료되거나 기본 순번 연속성을 잃으면 다음 연결에서는 선택 구독을 생략하여 기본 Order·Route 복구를 우선합니다.
선택 구독은 사용자가 모니터링을 새로 시작할 때 다시 시도합니다.
기본 구독 자체의 권한·서비스 오류는 무시하지 않습니다.

### 파싱 경고와 안전 차단

- 누락·명시적 null은 정상적인 부분 갱신으로 처리하고 기존 값을 유지합니다.
- 정수 문자열, 날짜·시각 객체를 처리하며 소수·비유한 값·범위 밖 식별자를 정수로 절삭하지 않습니다.
- 필드 변환 실패는 해당 값을 제외하고 함수·필드명·스택과 구독·이벤트 문맥을 기록합니다.
  화면에는 별도 수신 경고로 표시하며 전체 수신 오류로 전환하지 않습니다.
- 필수 값이 손상된 주문은 해당 Order의 자동 Ack만 제외합니다. 후속 갱신·재전송을
  새 주문으로 오인하여 자동 처리하지 않습니다. 다른 정상 주문의 활성화 상태는 유지합니다.
- SlowConsumerWarning은 경고로 기록하고 수신을 유지합니다. 실제 순번 유실·DataLoss,
  기본 구독 종료·연결 단절, 저널·감사 저장 실패, 전송 결과 불확실은 기존 안전 차단을 유지합니다.
- Ack 응답 형식을 확인할 수 없으면 성공·실패로 단정하지 않고 정상 응답 또는 기존 타임아웃까지
  기다립니다. 결과가 불확실해진 요청은 자동 재전송하지 않습니다.

시작 시 자동 Ack 해제는 정상적인 기본 안전 설정입니다. 이번 수정은 이를 자동으로 활성화하지 않습니다.

### 진단 로그 확인과 배포

설정·저널 폴더의 `feed_diagnostics.log`에 UTF-8 순환 로그를 저장합니다.
일반 Windows 경로는 `%LOCALAPPDATA%\EMSX_Desk_Monitor_HDGB\feed_diagnostics.log`이며
`설정 파일 위치` 안내창에서도 경로를 확인할 수 있습니다.
파일당 2MB와 백업 4개로 크기를 제한합니다. 구독 실패는 환경·상관 ID·요청 ID·요청 필드와
`source`, `category`, `errorCode`, `description`을 자르지 않고 기록합니다.
실제 예외에는 스택을 포함하며 고객 주문 메시지 전체나 인증 값을 의도적으로 덤프하지 않습니다.
공급자의 실패 설명이나 예외에 업무 정보가 포함될 수 있으므로 내부 정책에 따라 파일을 취급하십시오.

총 85개 테스트가 통과했습니다. 제공된 장애 형태의 모의 구독 거부, 장문 사유 보존,
선택 필드 거부·파싱 실패 중 기본 준비 상태와 활성화 유지, 독립 순번·부분 갱신,
전체 안전 차단 보존과 순환 파일 기록을 포함합니다.
실제 PROD·BETA 재연결과 신규 필드 제공 여부는 현재 환경에서 검증하지 않았습니다.

업데이트 시 `diagnostics.py`, `feed_parsing.py`를 포함한 전체 소스를 교체하고
`build_windows.bat`로 실행 파일을 다시 빌드하십시오. 기존 설정·DB 스키마 변경은 없습니다.
실행 중인 기존 프로그램을 정상 종료하고 새 빌드로 모니터링을 시작하십시오.
기본 Order 구독이 계속 거부되면 새 로그의 전체 `reason.description`을 기준으로
필드 호환성·TEAMVIEW 권한·서비스 상태를 추가로 확인할 수 있습니다.


## 2.6.0 Routes ADV·지정 국가 Alert·전용 경보음

### 참조 필드와 계산 기준

사용자가 제공한 Bloomberg 필드 ID를 EMSX 구독 필드로 넣지 않고 별도 참조 서비스로 조회합니다.

| 용도 | 필드 ID | 사용 방식 |
| --- | --- | --- |
| 5일 평균 거래량 | HS005 | 필드 정보에서 `VOLUME_AVG_5D`를 확인한 경우만 ADV 분모로 사용 |
| 20일 평균 거래량 | HS007 | 필드 정보에서 `VOLUME_AVG_20D`를 확인한 경우만 ADV 분모로 사용 |
| 발행국 ISO 코드 | DS290 | 반환된 코드로 지정 국가 여부 판정 |
| Sec Type | DS213 | 반환된 종목 유형 표시·강조 판정 |

기본 Order·Route 초기 수신이 완료된 후 `//blp/apiflds`의 FieldInfoRequest로 ID와 mnemonic을
확인합니다. 검증된 mnemonic으로만 `//blp/refdata`에 ReferenceDataRequest를 보냅니다.
HS005·HS007이 이미 백분율인 필드 등 다른 의미로 반환되면 거래량으로 가정하여 이중 계산하지 않고
해당 ADV를 미제공 처리하며 진단 로그에 기록합니다. 실제 필드 정보·권한은 배포 환경에서 확인해야 합니다.

**ADV(%) = 해당 Route의 전체 Shares ÷ 해당 기간 평균 거래량 × 100**입니다.
상위 Order 수량이나 체결·잔량으로 대체하지 않습니다. Routes에 ISO Country, Sec Type,
ADV, ADV Check 열을 추가했습니다. 화면의 ADV 기준은 20D가 기본이며 5D로 전환할 수 있습니다.
기존 관리자 JSON에 선택적으로 `"adv_period": 5` 또는 `20`을 지정하여 시작값을 정할 수 있습니다.
기존 JSON이 해당 키를 갖고 있지 않아도 20D 기본값으로 읽고 기존 전송 플래그는 그대로 유지합니다.
Routes CSV는 실제 표시된 기간에 맞춰 `adv_5d_pct` 또는 `adv_20d_pct` 머리글을 기록합니다.

국가·Sec Type·거래량은 상위 Order의 Ticker+YKey 또는 FIGI로 조회하고,
각 Route의 수량에 동일한 종목 평균 거래량을 적용합니다. FIGI는 `/bbgid/` 경로로 요청하며,
응답의 보안 식별자가 표준 이름으로 바뀌어도 요청 순번을 확인하여 원래 종목에 연결합니다.
기존 오늘·전일 범위의 종목만 요청하고, 50개씩 묶어 조회하며 5분 동안 캐시합니다.
서로 다른 참조 종목은 최대 100,000개로 제한합니다.

참조 서비스의 응답·오류·상관 ID는 Ack 응답·기본 구독 순번과 분리합니다.
참조 서비스가 없거나 권한·필드 오류가 발생해도 기본 수신과 Ack 활성화 상태를 변경하지 않습니다.
선택 서비스 미준비는 30초 간격으로 다시 열기를 시도하고 필드 정보 시간 초과는 서로 다른 요청 ID로
최대 3회 재시도합니다. 종목 데이터 요청은 20초 시간 초과 시 미제공으로 처리합니다.
참조 서비스 단절·응답 오류 시 이전 값을 현재 값으로 쓰지 않고 무효화합니다.

### ADV 행 강조

선택한 기간의 ADV를 원래 정밀도로 판정하며 다음 중 하나 이상이면 해당 Route를 금색으로 강조합니다.

- Instruction이 MOC이고 ADV가 2% 이상
- Sec Type이 Common Stock이고 ADV가 10% 이상

MOC와 Common Stock 조건은 독립적입니다. 공백·대소문자는 정리하지만
`TWAP MOC` 등 복합 비고를 임의로 MOC로 판정하지 않습니다.
국가 제한은 ADV 강조에 적용하지 않습니다. 판정 사유는 ADV Check에 표시하며
ADV 강조만으로 음향 Alert를 발생시키지는 않습니다.

평균 거래량 누락·0·비유한 값과 잘못된 수량은 ADV를 `—`로 표시하고 강조하지 않습니다.
표시는 소수점 최대 4자리까지 내림하여 1.99999%를 2% 이상처럼 보이게 하지 않습니다.
거절·실행 Alert가 ADV보다 우선하며 ADV Check 사유는 계속 확인할 수 있습니다.
배경 태그는 하나만 적용하여 교차 행 배경이 강조색을 덮지 않도록 했습니다.

### 지정 국가의 Route·No-Fill Alert

Unrouted Warning·No-Fill Alert는 DS290 국가코드가
**CN, HK, ID, IN, JP, NZ, MY, SG, TH, TW, VN, AU** 중 하나인 경우에만 적용합니다.
KR을 포함한 그 밖의 국가는 두 시간 경고의 대상이 아닙니다.
DS290은 요청한 발행국 필드 기준이며 Ticker로 거래소 국가를 추정하지 않습니다.
국가코드 미제공·잘못된 형식은 해당 시간 경고를 보류하고 상단 Region Missing으로 집계합니다.
2.6.8부터 두 시간 경고를 하나의 국가 검증 안에서 계산하고 MOC No-Fill 예외를 적용합니다.
Ack Pending 정보와 Route Closed Alert의 기존 별도 기준은 유지합니다.

화면 문구는 `FIX 수신 후 라우트 미실행 (Route 지연, 분)`과
`라우트 완료 후 체결 대기 (No-Fill 정체, 분)`으로 변경했습니다.
FIX 수신 판정은 기존 EMSX 상태 기반이며 독립적인 FIX ACK 원문을 수신했다고 주장하지 않습니다.
관측 기준 시간·체결 0 검증·종료 상태 제외·알림 반복과 중지 시 음향 차단은 유지합니다.

### 전용 경보음과 배포

`assets/desk_alert.wav`는 880Hz와 1320Hz 두 음을 번갈아 세 번 반복하는 약 1.8초 PCM 경보음입니다.
16비트 소스 진폭은 최대값의 약 88%이며 시작·끝에 짧은 음량 변화를 넣어 클릭 잡음을 줄였습니다.
Windows에서는 파일 재생을 비동기로 요청하며 기존 30초 반복 제한을 유지합니다.
시스템 음량·음소거·장치 설정은 강제로 변경하지 않습니다. 실제 청취 음량은 해당 설정에 따릅니다.
파일 누락·재생 오류 시 Tk 알림으로 대체하며 `알림음 확인`으로 실제 장치에서 확인할 수 있습니다.

`build_windows.bat`에 경보음의 PyInstaller 포함 옵션을 추가했습니다.
`risk_rules.py`, `reference_data.py`, `assets/desk_alert.wav`를 포함한 전체 소스를 교체하고
새 Windows 실행 파일을 빌드하십시오. 기존 DB·전송 승인 설정의 변경은 필요하지 않습니다.
DEMO는 아시아 종목과 합성 참조데이터를 제공하므로 실제 Bloomberg 없이 ADV 표시를 확인할 수 있습니다.

총 **107개 테스트**와 Python 컴파일·수정 모듈 정적 검사를 통과했습니다.
ADV 경계값·Route 수량·기간 전환·국가 12개와 비대상 국가·필드 ID 검증·분할 응답·캐시·권한 오류·
FIGI 식별자·음향 파일 형식·강조 우선순위·기존 Ack 회귀를 포함합니다.
실제 Bloomberg의 필드 응답·참조데이터 권한, Windows 화면과 스피커 출력은 현재 환경에서 미검증입니다.


## 2.6.1 독립 실행 파일·폐쇄망 배포

새 출력 파일은 `KB증권 해외주식영업부 EMSX 모니터.exe`입니다.
[오프라인 배포 안내](OFFLINE_DEPLOY_KO.md)에 의존성 반입, DLL 검증, 빌드와 Python 없는 PC의 검사 절차를 정리했습니다.
`build_windows.bat`는 온라인 설치 없이 `EMSX_Monitor.spec`로 빌드합니다.
경보음과 BLPAPI DLL을 포함하며 완성된 실행 파일의 `--self-test`가 실패하면 빌드를 실패 처리합니다.
Windows 실행 파일은 현재 Linux 작업 환경에서 생성·검증하지 않았습니다.


## 2.6.2 Windows CMD 빌드 실행기 수정

배치 파일은 ASCII·CRLF로 교체하고 빌드 처리를 `windows_build.py`로 옮겼습니다.
프로젝트 폴더에서 `python windows_build.py download`를 완료한 후,
`python windows_build.py prepare`, `python windows_build.py build`를 각각 별도 명령으로 실행합니다.
다운로드는 연결 가능한 준비 PC에서만 수행하며, 설치는 로컬 wheelhouse만 사용합니다.
BLPAPI는 Bloomberg 공식 저장소에서 받습니다. 상세 절차는 [오프라인 배포 안내](OFFLINE_DEPLOY_KO.md)를 참조하십시오.


## 2.6.3 Windows 파일 인코딩 보완

CP949 환경에서 UTF-8 파일을 읽는 빌드 전 테스트 오류를 수정했습니다.
파일 인코딩을 명시하고 빌드 도구의 자식 Python에 UTF-8 모드를 전달합니다.
모의 세션 테스트 로그와 실제 연결 오류를 구분하도록 정리했으며 117개 테스트를 통과했습니다.
기존 다운로드·설치가 성공했다면 빌드만 다시 실행하면 됩니다.


## 2.6.4 BLPAPI 보조 확장 파일명 보존

SDK의 `ffiutils.*.pyd` 검색과 PyInstaller 파일명 배치가 맞지 않아 발생한
번들 가져오기 실패에 대응했습니다. 정상 SDK가 로드한 원본 ABI 파일명을 Analysis 이후에도 보존합니다.
자체 검사 진입을 UI보다 앞당기고 검사 단계·원래 오류를 보고서와 콘솔에 남깁니다.
기존 다운로드·설치가 끝난 PC에서는 수정 소스로 빌드만 다시 수행하면 됩니다.
128개 회귀 테스트를 통과했으며 새 Windows 실행 파일 자체 검사 통과는 재빌드 후 확인해야 합니다.


## 2.6.5 자동 Ack 배치 전송

아래 내용은 2.6.5 구현 이력입니다. 현재 2.6.6은 50개 분할과 단건 대체 전송을 제거했습니다.
현재 실행 기준과 내부 콜백 이관은 다음 2.6.6 절을 적용하십시오.

동시에 도착한 적격 신규 Order를 10밀리초 수집 창으로 모아 최대 50개씩
하나의 `SellSideAck` 요청의 `EMSX_SEQUENCE` 배열에 담습니다.
50개가 모이면 수집 창 종료를 기다리지 않습니다. 요청을 전송한 뒤 개별 응답을 기다리지 않고
수신 루프로 돌아가므로 여러 배치가 동시에 응답 대기 상태가 될 수 있습니다.
한 회전당 요청 하나, 최대 20개 요청의 응답 대기로 제한하여 수신·중지 명령 처리 기회를 유지합니다.
이는 프로그램의 부하 제한이며 Bloomberg 서비스의 공식 허용 건수를 뜻하지 않습니다.
10밀리초는 수집 기준으로, 디스크·API·수신 처리 시간을 포함한 지연 상한은 아닙니다.

배치 큐·상관 ID·응답 추적은 동일한 재진입 잠금으로 보호합니다.
전송 직전에 상태·수량·Account·생성일·관리자 전송 권한을 다시 검증하고,
취소·삭제·파싱 오류·조건 불충족 Order를 배치에서 제외합니다.
초기 스냅샷·활성화 이전 Order·후속 갱신을 자동 Ack 대상으로 새로 편입하지 않습니다.
중지·해제·연결 장애 시 아직 전송하지 않은 큐를 폐기하며 재활성화로 되살리지 않습니다.

예약과 상태 변경은 배치별 SQLite 트랜잭션으로 저장합니다.
`ack_audit.jsonl`의 `ACK_BATCH_RESERVED`, `ACK_BATCH_SENT`, `ACK_BATCH_RESPONSE` 등에는
`batch_id`, `correlation_id`, 전체 `sequences` 목록이 기록됩니다.
목록은 잘리지 않으며 감사 파일 동기화도 Order별 반복 대신 배치 단계별로 수행합니다.
개별 Order의 최종 처리 상태는 기존 저널 고유 키로 조회할 수 있고,
Event Log에는 배치 ID 앞 12자리·건수·상태를 요약합니다.
실제 API 호출과 DB·파일 작업은 Bloomberg 작업자 스레드에서 수행하며 Tk 객체에 접근하지 않습니다.

부분 성공 응답만으로 배치 전체를 성공 처리하지 않습니다.
최종 `STATUS=0`도 기존과 동일하게 `API_RESPONSE_OK_UNVERIFIED`이며 EMSX 상태 변경을 보증하지 않습니다.
오류 응답은 배치 구성원 전체를 수동 확인 대상으로 남깁니다.
전송 예외·요청 실패·20초 응답 시간 초과·응답 전 연결 종료는 해당 배치를
`UNKNOWN`으로 보존하고 자동 Ack를 해제하며 자동 재전송하지 않습니다.
배치 내 일부 Order가 실제로 처리되었을 수 있으므로 EMSX에서 개별 결과를 대조하십시오.

기존 설정 키·기본 비활성 상태·DB 스키마와 단건 `send_func(sequence, cid)` 계약을 유지합니다.
배치 전송 콜백을 지정하지 않는 기존 엔진 사용자는 기존 단건 동작을 유지합니다.
추가 의존성이나 DB 초기화는 필요하지 않습니다. 전체 소스를 교체하고,
기존 빌드 환경에서 `python windows_build.py build`로 새 실행 파일을 만드십시오.

배치·동시 중복·500개 동시 유입·취소·권한 변경·영속 예약 충돌·감사 실패·부분 응답·
타임아웃·작업자 스레드와 중지 명령을 포함하여 총 154개 테스트를 통과했습니다.
실제 Bloomberg 배치 처리와 Windows 실행 파일 동작은 이 Linux 환경에서 검증하지 않았습니다.


## 2.6.6 전체 대기 Order 단일 요청 전송

`on_order`는 적격 신규 Order를 큐에 수집하며 API를 호출하지 않습니다.
수집 종료 시 잠금 안에서 대기 목록 **전체**를 분리하고 전송 직전 검증·영속 예약을 수행한 뒤,
살아 있는 신규 예약 목록 전체를 하나의 `SellSideAck` 요청에 담습니다.
`EMSX_SEQUENCE` 배열을 채우는 루프가 끝난 다음 `sendRequest`를 **한 번** 호출합니다.
건수에 따른 50개 분할, Order별 전송, 단건 콜백 대체 경로는 제거했습니다.

이미 도착한 Bloomberg 이벤트는 `tryNextEvent`로 먼저 반영합니다.
새 Order가 마지막으로 추가된 뒤 50밀리초의 수신 공백을 수집 기준으로 삼습니다.
지속적으로 도착하는 스트림은 첫 대기 Order의 250밀리초 시점에 수집을 종료하여 무기한 대기를 막습니다.
이벤트 연속 처리도 회전당 25밀리초로 제한하여 중지·응답·참조 서비스 처리를 유지합니다.
이 시간은 스케줄링 기준이며 네이티브 요청 구성·디스크·서비스 처리 지연의 상한을 보장하지 않습니다.
앞으로 도착할 Order까지 하나의 요청에 포함하지는 않습니다. 전송 시점의 적격 대기 목록 전체가 한 요청입니다.
다음 수집분은 이전 요청의 응답을 기다리지 않고 제출할 수 있으며 최대 미응답 요청 20개 제한은 유지합니다.

수집·목록 분리·영속 예약·상관 ID 등록·API 제출은 동일한 `threading.RLock`으로 보호합니다.
전송 전 취소·삭제·손상·권한 변경·연결 단절의 기존 차단 조건과,
불확실한 배치 전체의 `UNKNOWN` 보존·자동 재전송 금지는 유지합니다.
서비스가 큰 배열을 거부하더라도 개별 Order로 다시 전송하지 않으며 수동 대조 대상으로 남깁니다.

화면에는 전송·응답 단계마다 구성원 전체를 담은 큐 메시지 하나를 전달합니다.
화면 처리도 해당 Order 상태를 한꺼번에 갱신하고 Event Log 한 줄로 요약합니다.
DB와 감사 파일의 배치 ID·상관 ID·전체 Order 목록 기록은 유지합니다.
`feed_diagnostics.log`에는 실제 API 경계에서 다음과 같은 INFO 기록이 남습니다.
현재 진단 설정에서도 보이도록 DEBUG 대신 INFO 수준으로 기록합니다.

```text
[배치 Ack] 일괄 전송 대상 확정: 수집=500 적격=500 신규예약=500 batch_id=... CID=121000
[배치 Ack] Order 500개를 단일 요청으로 전송 시작: sendRequest=1 CID=121000
[배치 Ack] Order 500개 단일 요청 제출 완료: sendRequest=1 CID=121000 · API 응답 대기
```

제출 완료는 API 요청 함수가 반환했다는 의미이며 Bloomberg의 처리 완료·EMSX 상태 전환을 보증하지 않습니다.
진단 파일 경로는 `%LOCALAPPDATA%\EMSX_Desk_Monitor_HDGB\feed_diagnostics.log`입니다.
모니터링 시작 Event Log의 `버전 2.6.6`으로 새 실행 파일 사용 여부를 확인할 수 있습니다.

관리자 설정·저널 DB·의존성은 그대로 사용합니다. 내부에서 `AckEngine`을 직접 생성하는 코드는
`batch_send_func(sequences, cid)`를 지정하고 수신 루프에서 `flush_batch()`를 호출해야 합니다.
이전 `send_func` 생성자 인수는 읽을 수 있지만 전송에 사용하지 않으며,
배치 콜백이 없으면 단건으로 대체하지 않고 자동 Ack를 차단합니다.
기존 안전성 테스트는 수집과 제출 단계를 명시하도록 이관했으며 차단·중복·감사·응답 검증을 유지합니다.

총 165개 테스트를 통과했습니다. 500개의 개별 수신 이벤트가 모의 BLPAPI의
`sendRequest` 한 번으로 이어지는 전체 경로, 30밀리초 간격의 분리 수신,
동시 수집·전송 잠금, 전송 전 취소·연결 단절과 화면 일괄 갱신을 포함합니다.
실제 Bloomberg 배치 수락과 Windows 실행 파일 검증은 대상 PC에서 별도로 확인해야 합니다.
전체 소스를 교체한 뒤 `python windows_build.py build`로 재빌드하십시오.


## 2.6.7 Windows 진단 파일 해제

Windows 빌드 전 테스트에서 작업자 종료 후에도 `feed_diagnostics.log`가 열린 채 남아
임시 폴더 삭제가 `WinError 32`로 실패하는 문제를 수정했습니다.
진단 로그의 소유 범위를 작업자 수명에 연결하여 정상 종료·SDK 가져오기 실패·초기 알림 오류·
예외 종료에서도 해당 작업자가 연 파일 핸들러를 Logger에서 제거하고 닫습니다.
재연결 동안은 로그를 유지하고, 같은 경로를 공유하면 마지막 사용자가 종료될 때 해제합니다.
외부에서 미리 등록한 핸들러는 해당 소유자가 관리하며 작업자 종료가 임의로 닫지 않습니다.
종료 직후 지연된 로그 호출이 이미 닫힌 파일을 다시 여는 것도 방지합니다.

기존 배치 Ack 동작·설정·DB·의존성은 그대로 사용합니다.
전체 수정 소스를 교체한 뒤 `python windows_build.py build`만 다시 실행하십시오.
모니터링 시작 Event Log에는 `버전 2.6.7`이 표시됩니다.

총 172개 테스트를 통과했습니다. 파일 삭제 성공 여부와 함께 핸들러의 실제 스트림 닫힘을
검사하여 Linux에서 열린 파일 삭제가 허용되는 경우에도 누락을 찾도록 보완했습니다.
Windows의 동일 빌드 명령과 완성된 실행 파일 자체 검사 결과는 대상 PC에서 확인해야 합니다.


## 2.6.8 지정 국가 검증과 MOC No-Fill 예외

Route 지연(`Unrouted Warning`)과 라우트 후 미체결 정체(`No-Fill Alert`)는
같은 국가 검증을 통과한 주문에서만 계산합니다. 허용 코드는 정확히
**CN, HK, ID, IN, JP, NZ, MY, SG, TH, TW, VN, AU**입니다.
그 밖의 국가·누락·손상 값은 두 시간 경고에서 제외합니다.
참조데이터 수신 시와 경고 계산 시 같은 정규화 함수를 적용합니다.
문자열 여부·ASCII 영문 두 자리 형식을 먼저 확인하고 앞뒤 공백과 영문 대소문자를 정규화합니다.
국가명을 코드로 추정하거나 복수 코드에서 일부를 추출하지 않으며,
유니코드 대문자 변환으로 잘못된 입력이 허용 코드가 되는 것도 차단합니다.

고객 Instruction은 기존 `EMSX_NOTES` 기준입니다.
`MOC`, `moc`, `TWAP; MOC`, `고객 지시: MOC 종가 집행`처럼 어디에든 `MOC`가 포함되면
라우트 후 체결 0·경과 시간 검사에 들어가지 않고 No-Fill 시각·음향 경고에서 제외합니다.
아시아 MOC 주문에 Route가 없으면 기존 Route 지연 기준은 적용합니다.
부분 갱신은 기존 Instruction을 보존하며, 새로 MOC가 수신되면 현재 No-Fill 경고를 해제합니다.
MOC가 제거되면 현재 상태와 기존 Route 관측 경과 시간으로 다시 판정합니다.
ADV 강조는 별도 규칙으로 유지하며 MOC의 No-Fill 제외와 독립적으로 표시될 수 있습니다.

기존 코드에서도 정상적인 US·GB 등 비대상 코드는 두 시간 경고에서 제외하고 있었습니다.
이번 제보의 실제 Order 예시는 없어 해당 사건의 원인은 확정하지 않았습니다.
`Route Closed Alert`는 Route가 모두 비활성인 별도 경고이며 이번 두 시간 경고 제한의 대상이 아닙니다.
국가 원본 DS290은 발행국 기준으로, 거래소·상장시장 지역과 다를 수 있습니다.
이번 수정은 지정한 발행국 코드 기준을 유지하며 Ticker에서 거래시장 국가를 추정하지 않습니다.
화면 안내에도 발행국 기준과 MOC 예외를 표시합니다.

총 184개 테스트를 통과했습니다. 영문 두 자리 676개 조합의 허용 목록 판정,
12개 허용 국가와 MOC 복합 지시, 국가 정정·참조 무효화·Ticker 변경,
MOC 수신 후 시각·음향 경고 해제와 부분 갱신 보존을 포함합니다.
설정·DB·의존성 이관은 필요하지 않습니다. 전체 소스를 교체하고
`python windows_build.py build`로 실행 파일을 재빌드하십시오.
시작 Event Log에는 `버전 2.6.8`이 표시됩니다. 실제 Bloomberg 응답과 Windows 실행 파일은 대상 PC에서 확인해야 합니다.


## 2.7.0 시장 상태·휴장 안내·일일 보고서

- 상단 26개 대표 시장의 현지·KST 시각, 개장·장전·점심·장 종료·휴장·미확인 상태를 초당 갱신합니다. 로컬 시간표에 DST·조기 폐장·점심시간을 반영합니다.
- Orders·Routes에 Market·장 상태·휴장 확인 배지를 추가했습니다. 종목별 시장 지정이 없으면 기존 DS290 발행국의 대표 거래소를 사용합니다.
- 데스크 일지를 별도 SQLite에 비동기로 보존하고 트레이더 메모 입력을 추가했습니다. 화면 최근 500줄 제한과 별도로 수신·Ack·경고 전환·메모를 기록합니다.
- 당일 일지/로그 내보내기로 요약·Order·Route·Event Log·자동 Ack 감사·메모·시장 상태의 7개 시트를 가진 Excel 파일을 생성합니다. 작성 중 새 일지 저장을 유지하고 원문·긴 ID·긴 메모를 보존합니다.
- 기본 캘린더는 대부분 2025–2027년, CN·SG·IN은 2025–2026년입니다. VN 기본 휴장 일정과 지원 기간 밖은 확인 필요로 표시하며 로컬 날짜별 지정을 지원합니다. 시장 상태는 배포 시간표 참고 정보로 실시간 거래소 운영 상태를 조회하지 않습니다.
- 새 런타임 패키지 설치는 필요하지 않습니다. 전체 소스·`assets/market_sessions.json`을 교체하고 기존 `python windows_build.py build`로 빌드하십시오.
- 총 212개 테스트와 별도 Excel 리더 검증을 통과했습니다. 실제 Windows 화면·Excel·Bloomberg와 최신 임시 휴장 공지는 대상 환경에서 확인해야 합니다.

시장별 범위, ADR 매핑, 로컬 지정 JSON, 일지 보관과 내보내기 경계는
[운영 편의 기능 안내](OPERATIONS_KO.md)에 정리했습니다.

## 2.7.1 실행 상태와 휴장 판정 분리

- 일반적인 장 종료·장전·점심시간·일정 미확인은 시장 정보로만 표시합니다. 달력으로 확인된 휴장일·주말에만 주문별 휴장 안내를 표시하여 정상 거래일의 일괄 강조를 방지합니다.
- Partial fill·No fill 및 기존 Alerts·ADV 강조를 휴장 판정 근거로 사용하지 않습니다. Cancel 등 종료 상태와 잔량 0인 완료 주문에는 휴장 배지를 표시하지 않습니다.
- 주문별 안내의 판정 기준 접미사를 제거하고 GUI·Excel 열 이름을 휴장 확인으로 통일했습니다. 상태·문서에는 장 종료·휴장 용어를 사용합니다.
- 기존 설정·DB·wheelhouse·런타임 의존성은 유지합니다. 전체 소스를 교체한 뒤 `python windows_build.py build`로 재빌드하십시오.
- 총 219개 테스트를 통과했습니다. 정상 거래일의 장 종료·점심·미확인 일정, 실제 휴장일·주말, Cancel 변형, Order·Route 강조와 실제 체결 상태 전환을 검증했습니다. 실제 Windows UI·실행 파일·Bloomberg는 대상 PC 확인이 필요합니다.

## 2.7.2 Routes 중심 화면과 Account·Ticker 고정

- 상단 Orders 탭을 제거하고 Routes를 기본 화면으로 지정했습니다. 탭 순서는 Routes, Brokers, 자동 수신확인(Ack), Event Log입니다.
- Routes와 자동 Ack 표의 Account·Ticker를 왼쪽에 고정했습니다. 글꼴 기준의 최소 너비와 현재 값의 글자 폭을 반영하며, 창 크기를 바꾸거나 상세 열을 가로 이동해도 식별 열은 유지됩니다.
- 상세 열은 Broker·Side·수량·Line Status·Instruction·ADV 순으로 우선 배치했습니다. 가로 스크롤은 상세 영역에만 적용하고 모두 들어가면 숨깁니다. 매우 긴 Account·Ticker는 마우스를 올리면 원문을 확인할 수 있습니다.
- Order 수신·자동 Ack·경고·감사·일일 보고서는 계속 동작합니다. Route의 Alerts 열에 상위 Order 경고를 표시하며 Alerts 필터는 Route 경고·휴장 안내·ADV 강조에 적용합니다. Route가 없는 주문은 상단 Unrouted·Alerts 집계와 당일 Order 보고서에서 확인합니다.
- 기존 설정·DB·wheelhouse는 보존합니다. 새 `desk_tables.py`를 포함한 전체 소스를 교체한 뒤 `python windows_build.py build`로 재빌드하십시오.
- 총 226개 테스트와 정적 검사를 통과했습니다. Linux 가상 화면의 실제 Tk 표에서 1,000·1,330·1,920픽셀 창 폭, 고정 열·가로/세로 이동·선택·행 갱신·CSV를 확인했습니다. 실제 Windows UI·실행 파일·Bloomberg는 대상 PC에서 확인해야 합니다.
