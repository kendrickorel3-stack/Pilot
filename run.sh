#!/usr/bin/env sh
# Start Pilot on Mac or Linux. The first run sets everything up (a few minutes).
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null || {
  echo "Pilot needs Python 3.11 or newer. Get it from https://www.python.org/downloads/"; exit 1; }
if [ ! -f .venv/.ready ]; then
  echo "First run: installing Pilot and its browser. This takes a few minutes..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  .venv/bin/python -m playwright install chromium
  touch .venv/.ready
fi
exec .venv/bin/python -m invoice_agent.server --open --data ./data "$@"
