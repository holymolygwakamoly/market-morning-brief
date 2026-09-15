@echo off
rem Market Morning Brief - local dashboard launcher (ASCII only: cmd parses batch files in the OEM codepage)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run the install steps in README first:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.lock -e .
  pause
  exit /b 1
)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m brief.serve
if errorlevel 1 pause
