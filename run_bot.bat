@echo off
REM Starts the MSC Bot (bot core + API + web UI) on http://127.0.0.1:8000
REM Equivalent to: uvicorn app.main:app --host 127.0.0.1 --port 8000
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
start "" http://127.0.0.1:8000
python -m app %*
if errorlevel 1 pause
