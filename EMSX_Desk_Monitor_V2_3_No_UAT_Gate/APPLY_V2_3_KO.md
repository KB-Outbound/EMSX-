# V2.3 적용 안내

## Python으로 실행 중인 PC
1. Auto ACK DISARM → STOP → 프로그램 완전 종료.
2. 기존 폴더의 `ack_engine.py`, `emsx_desk_monitor.py`를 V2.3 파일로 교체하거나 V2.3 전체 폴더를 별도 압축 해제.
3. 기존 `admin_config.json` 파일은 삭제하지 마세요. `uat_reference` 값은 수정하지 않아도 됩니다.
4. `py -3 emsx_desk_monitor.py`로 실행.
5. PROD / TEAMVIEW HDGB 접속 → ORDER·ROUTE SNAPSHOT DONE 및 API SEQ GAPS 0 확인.
6. Auto ACK 탭의 ARM 조작은 실제 송신하므로, 권한·사내 승인·단일 송신 PC 구성을 점검한 후 사용.

## EXE 배포 중인 PC
1. 소스 교체만으로 기존 EXE가 바뀌지 않습니다.
2. 승인된 Windows 빌드 PC에서 `build_windows.bat` 실행 → `dist\EMSX_Desk_Monitor_HDGB_V2_3.exe` 생성.
3. 신규 EXE는 먼저 DEMO/모니터링 전용으로 점검하고 팀 배포.

## 변경되지 않은 안전장치
- PROD 승인 플래그 3개는 모두 필요하며 자동으로 켜지지 않습니다.
- Bloomberg SellSideAck entitlement는 이 프로그램이 생성할 수 없습니다.
- 현재 모니터링 가능한 모든 FIX 주문이 SellSideAck 대상인 것은 아닙니다.
- 최초 스냅샷/ARM 이전 주문은 자동 ACK하지 않습니다.
- 송신용 PC는 1대로 제한해야 합니다(중앙 중복 방지 기능 없음).
