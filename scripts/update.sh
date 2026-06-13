#!/usr/bin/env bash
# ===========================================================================
#  Pull the latest code and restart the bot service in one command.
#  Usage:   bash scripts/update.sh
# ===========================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> Pulling latest changes…"
git pull --ff-only

echo "==> Updating Python dependencies…"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade -r requirements.txt

echo "==> Restarting service…"
sudo systemctl restart ai-edit-bot
sudo systemctl --no-pager status ai-edit-bot | head -n 5

echo "==> Done. Follow logs with:  journalctl -u ai-edit-bot -f"
