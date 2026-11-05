#!/usr/bin/env bash
# SEPA Desk: install on a Linux or macOS home machine as an always-on user service.
# Idempotent: re-run after pulling new code.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Python 3.9+ required ($("$PY" -V 2>&1))." >&2; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
[ -f .env ] || .venv/bin/python desk.py token

# First install only: a bundle already restored here is skipped, so re-running never rolls data back.
.venv/bin/python desk.py restore --once

# The service runs with a minimal PATH; carry over wherever `claude` lives (nvm, npm prefix, ~/.local/bin...).
SVC_PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if CLAUDE_BIN="$(command -v claude 2>/dev/null)"; then
  SVC_PATH="$(dirname "$CLAUDE_BIN"):$SVC_PATH"
else
  echo "WARNING: 'claude' is not on PATH; the scheduled research jobs will fail until Claude Code is installed (then re-run this script)." >&2
fi

mkdir -p reports
if [ "$(uname)" = "Darwin" ]; then
  P="$HOME/Library/LaunchAgents/com.sepa.desk.plist"
  cat > "$P" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sepa.desk</string>
  <key>ProgramArguments</key><array><string>$ROOT/.venv/bin/python</string><string>desk.py</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$SVC_PATH</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/reports/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/reports/server.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/com.sepa.desk" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$P"
  echo "Keep it awake: System Settings > Energy > Prevent automatic sleeping when the display is off."
else
  U="$HOME/.config/systemd/user/sepa-desk.service"
  mkdir -p "$(dirname "$U")"
  cat > "$U" <<UNIT
[Unit]
Description=SEPA + CAN SLIM Desk

[Service]
WorkingDirectory=$ROOT
Environment=PATH=$SVC_PATH
ExecStart=$ROOT/.venv/bin/python desk.py serve
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable sepa-desk
  systemctl --user restart sepa-desk
  U_NAME="${USER:-$(id -un)}"
  loginctl enable-linger "$U_NAME" 2>/dev/null || echo "Run 'sudo loginctl enable-linger $U_NAME' so it runs while you're logged out."
fi
for _ in $(seq 1 20); do
  if .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2)' 2>/dev/null; then
    OK=1; break
  fi
  sleep 1
done
if [ -z "${OK:-}" ]; then echo "The service did not answer on :8765 within 20 s. Check reports/server.log or 'journalctl --user -u sepa-desk'." >&2; exit 1; fi
echo "SEPA Desk is running at http://127.0.0.1:8765"
echo "Remote access: install Tailscale, then:  tailscale serve --bg 8765"
echo "Research jobs need Claude Code on PATH: run 'claude' once and sign in."
