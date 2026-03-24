#!/usr/bin/env bash
# Install Instrument GPT as a systemd --user unit (starts with your login session).
# For start-on-boot without logging in graphically: sudo loginctl enable-linger "$USER"
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TEMPLATE="$REPO/systemd/instrumentgpt.service.in"
TARGET="$UNIT_DIR/instrumentgpt.service"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"
chmod +x "$REPO/scripts/run-streamlit-service.sh"
sed "s|{{REPO}}|$REPO|g" "$TEMPLATE" > "$TARGET"
systemctl --user daemon-reload
systemctl --user enable instrumentgpt.service

echo "Wrote $TARGET"
echo ""
echo "  Start now:     systemctl --user start instrumentgpt.service"
echo "  Status:        systemctl --user status instrumentgpt.service"
echo "  Logs:          journalctl --user -u instrumentgpt.service -f"
echo ""
echo "  Start on boot (before login), run once:"
echo "    sudo loginctl enable-linger $USER"
