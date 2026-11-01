@echo off
rem Double-click to install or update Sports CLI, then open it.
title Install Sports CLI
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.ps1 | iex"
if errorlevel 1 (
  echo.
  echo Something went wrong; the message above says what.
  pause
  exit /b 1
)
echo.
echo Starting Sports CLI. Next time, open "Sports CLI" from the Start menu.
call "%LOCALAPPDATA%\sports-cli\bin\sports.cmd"
