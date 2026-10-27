@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not defined EMSX_BUILD_PYTHON set "EMSX_BUILD_PYTHON=python"
"%EMSX_BUILD_PYTHON%" "%~dp0windows_build.py" prepare
exit /b %errorlevel%
