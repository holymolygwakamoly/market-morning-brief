@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [오류] .venv 가 없습니다. README 의 설치 절차를 먼저 실행하세요: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.lock -e .
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m brief.serve
pause
