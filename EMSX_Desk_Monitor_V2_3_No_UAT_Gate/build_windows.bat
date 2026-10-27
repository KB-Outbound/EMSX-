@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem 이 스크립트는 패키지를 다운로드하거나 설치하지 않습니다.
if not defined EMSX_BUILD_PYTHON set "EMSX_BUILD_PYTHON=python"
"%EMSX_BUILD_PYTHON%" -c "import sys,struct,tkinter,blpapi,PyInstaller; assert sys.platform=='win32' and struct.calcsize('P')==8; r=tkinter.Tk(); r.withdraw(); r.destroy(); print('빌드 의존성과 Tk 초기화 확인 완료')"
if errorlevel 1 goto failure
"%EMSX_BUILD_PYTHON%" -m unittest discover -s tests -q
if errorlevel 1 goto failure
"%EMSX_BUILD_PYTHON%" -m PyInstaller --noconfirm --clean EMSX_Monitor.spec
if errorlevel 1 goto failure
if not exist "dist\KB증권 해외주식영업부 EMSX 모니터.exe" goto failure
if exist "dist\패키지_검증.json" del "dist\패키지_검증.json"
start /wait "" "dist\KB증권 해외주식영업부 EMSX 모니터.exe" --self-test "%CD%\dist\패키지_검증.json"
if errorlevel 1 goto failure
"%EMSX_BUILD_PYTHON%" -c "import json; from pathlib import Path; assert json.loads(Path('dist/패키지_검증.json').read_text(encoding='utf-8'))['성공'] and json.loads(Path('dist/패키지_검증.json').read_text(encoding='utf-8'))['독립실행']"
if errorlevel 1 goto failure
"%EMSX_BUILD_PYTHON%" -m pip --disable-pip-version-check freeze > "dist\빌드_패키지_목록.txt"
if errorlevel 1 goto failure
echo 빌드 완료: dist\KB증권 해외주식영업부 EMSX 모니터.exe
exit /b 0
:failure
echo 빌드 또는 독립 실행 검증 실패. 오류를 해결한 뒤 다시 실행하십시오.
exit /b 1
