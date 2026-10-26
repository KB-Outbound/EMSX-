@echo off
setlocal
cd /d "%~dp0"
echo ======================================================
echo EMSX Desk Monitor V2 ^+ HDGB TEAMVIEW ^+ E2E ACK
 echo IMPORTANT: Build only on an IT-approved Windows PC.
echo ======================================================
py -3 -m pip install --upgrade pyinstaller blpapi
if errorlevel 1 (
  echo ERROR: dependency install failed. Use IT-approved offline wheels if no Internet.
  pause
  exit /b 1
)
py -3 -m unittest discover -s tests -q
if errorlevel 1 (
  echo ERROR: offline tests failed. EXE will not be built.
  pause
  exit /b 1
)
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name EMSX_Desk_Monitor_HDGB_V2_3 --collect-all blpapi emsx_desk_monitor.py
if errorlevel 1 (
  echo ERROR: EXE build failed.
  pause
  exit /b 1
)
if not exist "dist\EMSX_Desk_Monitor_HDGB_V2_3.exe" (
  echo ERROR: No EXE output found.
  pause
  exit /b 1
)
echo.
echo SUCCESS: dist\EMSX_Desk_Monitor_HDGB_V2_3.exe
 echo Distributor: Ensure PROD writes remain administratively disabled by default.
pause
