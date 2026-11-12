@echo off
rem Start Pilot on Windows. The first run sets everything up (a few minutes).
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Pilot needs Python 3.11 or newer. Get it from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\.ready" (
  echo First run: installing Pilot and its browser. This takes a few minutes...
  %PY% -m venv .venv || goto fail
  .venv\Scripts\python -m pip install -q --upgrade pip || goto fail
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto fail
  .venv\Scripts\python -m playwright install chromium || goto fail
  type nul > ".venv\.ready"
)
.venv\Scripts\python -m invoice_agent.server --open --data .\data %*
goto :eof
:fail
echo Setup failed. Delete the .venv folder and run this again.
pause
