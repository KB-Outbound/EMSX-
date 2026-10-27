@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ======================================================
echo KB증권 해외주식영업부 - EMSX 모니터 - 정동화
 echo 승인된 Windows PC에서 빌드하십시오.
echo ======================================================
py -3 -m pip install --upgrade pyinstaller blpapi
if errorlevel 1 (
  echo 오류: 의존성 설치 실패. 인터넷이 없으면 승인된 오프라인 패키지를 사용하십시오.
  pause
  exit /b 1
)
py -3 -m unittest discover -s tests -q
if errorlevel 1 (
  echo 오류: 오프라인 테스트 실패로 실행 파일을 빌드하지 않습니다.
  pause
  exit /b 1
)
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name EMSX_Monitor --version-file windows_version_info.txt --add-data "assets/desk_alert.wav;assets" --collect-all blpapi emsx_desk_monitor.py
if errorlevel 1 (
  echo 오류: 실행 파일 빌드 실패.
  pause
  exit /b 1
)
if not exist "dist\EMSX_Monitor.exe" (
  echo 오류: 실행 파일을 찾을 수 없습니다.
  pause
  exit /b 1
)
echo.
echo 빌드 완료: dist\EMSX_Monitor.exe
 echo 배포 시 PROD 전송의 기본 차단 설정을 유지하십시오.
pause
