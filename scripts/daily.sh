#!/usr/bin/env bash
# Run the daily routine once in the foreground (update + Claude research + report in reports/).
# Normally not needed: `desk.py serve` runs it on schedule (app/settings.schedule).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
"$PY" desk.py job daily
