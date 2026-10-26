# EMSX Desk Monitor V2.1 (진단 표시 강화)

이 변경은 기존 V2의 **버튼 비활성화 원인만 화면에 표시**하는 패치입니다.
주문 구독, ACK 적격성 심사, API 전송, 안전장치 및 관리자 승인 조건은 변경하지 않았습니다.

## 실행

```cmd
cd /d "C:\Users\SP90214\Desktop\EMSX_Desk_Monitor_V2_1_Diagnostics"
py -3 emsx_desk_monitor.py
```

1. PROD + TEAMVIEW `HDGB`에서 `START MONITOR`
2. `Auto ACK (E2E)` 탭의 `ARM BLOCKED:` 이유 읽기
3. 또는 `WHY ARM DISABLED?` 버튼 클릭
4. 원인과 `Activity / Diagnostics` 오류가 있으면 공유

`ARM` 버튼의 회색 표시 기준은 기존과 동일합니다. 기본적으로 PROD 쓰기 권한은 꺼져 있으며 사내 승인 없이 전송 플래그를 변경하지 마세요. `STARTED / SNAPSHOT DONE`은 Bloomberg의 `EVENT_STATUS=11` 시그널을 사용합니다.

Windows EXE 제작: `build_windows.bat` 실행. 기존 V2와 구별되도록 `EMSX_Desk_Monitor_HDGB_V2_1.exe`가 생성됩니다.
