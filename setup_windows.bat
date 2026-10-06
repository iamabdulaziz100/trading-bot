@echo off
REM One-time setup: creates .venv and installs dependencies (Windows, 64-bit Python 3.11/3.12).
setlocal
cd /d "%~dp0"

set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY set "PY=python"

%PY% -c "import sys; sys.exit(0 if sys.maxsize > 2**32 and sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo [ERROR] 64-bit Python 3.11+ is required ^(the MetaTrader5 package must match the 64-bit terminal^).
  echo         Install it from https://www.python.org/downloads/windows/ and re-run this script.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment with %PY% ...
  %PY% -m venv .venv || goto :err
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Setup complete.
echo  1. Open MT5, log in to your DEMO account, enable Tools ^> Options ^> Expert Advisors ^> Allow algorithmic trading.
echo  2. Optional: put the account login/password/server in .env
echo  3. Start the bot with run_bot.bat  ^(UI: http://127.0.0.1:8000^)
pause
exit /b 0

:err
echo [ERROR] Setup failed - see the messages above.
pause
exit /b 1
