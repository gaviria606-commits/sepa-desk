#!/usr/bin/env bash
# SEPA Desk: install on a Linux or macOS home machine as an always-on user service.
# Idempotent: re-run after pulling new code.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || .venv/bin/python desk.py token

B="$(ls -t "$ROOT"/sepa-desk-*.zip "$ROOT"/../sepa-desk-*.zip 2>/dev/null | head -1 || true)"
if [ -n "$B" ]; then echo "Restoring data from $B"; .venv/bin/python desk.py restore "$B"; fi

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
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/reports/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/reports/server.log</string>
</dict></plist>
PLIST
  launchctl unload "$P" 2>/dev/null || true
  launchctl load "$P"
  echo "Keep it awake: System Settings > Energy > Prevent automatic sleeping when the display is off."
else
  U="$HOME/.config/systemd/user/sepa-desk.service"
  mkdir -p "$(dirname "$U")"
  cat > "$U" <<UNIT
[Unit]
Description=SEPA + CAN SLIM Desk
After=network-online.target

[Service]
WorkingDirectory=$ROOT
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$ROOT/.venv/bin/python desk.py serve
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now sepa-desk
  systemctl --user restart sepa-desk
  loginctl enable-linger "$USER" 2>/dev/null || echo "Run 'sudo loginctl enable-linger $USER' so it runs while you're logged out."
fi
echo "SEPA Desk is running at http://127.0.0.1:8765"
echo "Remote access: install Tailscale, then:  tailscale serve --bg 8765"
echo "Research jobs need Claude Code on PATH: run 'claude' once and sign in."
