@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem wheelhouse는 Windows x64와 선택한 Python 버전에 맞춰 사전 반입합니다.
if not defined EMSX_BUILD_PYTHON set "EMSX_BUILD_PYTHON=python"
if not exist wheelhouse (
  echo 오류: 승인된 wheelhouse 폴더가 필요합니다.
  exit /b 1
)
"%EMSX_BUILD_PYTHON%" -m pip --disable-pip-version-check install --no-index --only-binary=:all: --find-links "%CD%\wheelhouse" -r requirements.txt
exit /b %errorlevel%
