@echo off
rem Double-click to remove Sports CLI. It lists what it removes and asks first.
title Uninstall Sports CLI
if exist "%LOCALAPPDATA%\sports-cli\app\uninstall.ps1" (
  copy /y "%LOCALAPPDATA%\sports-cli\app\uninstall.ps1" "%TEMP%\sports-cli-uninstall.ps1" >nul
) else (
  powershell -NoProfile -Command "irm https://raw.githubusercontent.com/osiddiki/sports-cli/main/uninstall.ps1 -OutFile $env:TEMP\sports-cli-uninstall.ps1"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\sports-cli-uninstall.ps1"
del "%TEMP%\sports-cli-uninstall.ps1" >nul 2>&1
pause
