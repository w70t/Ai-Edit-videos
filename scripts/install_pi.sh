#!/usr/bin/env bash
# ===========================================================================
#  One-shot installer for Raspberry Pi 5 (Raspberry Pi OS / Debian Bookworm).
#  Run from the project root:   bash scripts/install_pi.sh
# ===========================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> Project dir: $PROJECT_DIR"

echo "==> Installing system packages (ffmpeg, python venv)…"
sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip

echo "==> Creating virtualenv (.venv)…"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies…"
pip install --upgrade pip wheel
pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "==> Creating .env from template (EDIT IT before starting!)"
  cp .env.example .env
fi

echo
echo "==> Checking FFmpeg hardware decoder support on this Pi:"
ffmpeg -hide_banner -decoders 2>/dev/null | grep -i v4l2m2m || \
  echo "    (h264_v4l2m2m not listed — bot will use software decode)"

cat <<'EONOTE'

============================================================
 Next steps
============================================================
 1. Edit .env and set BOT_TOKEN and ADMIN_ID:
        nano .env

 2. Test it runs:
        source .venv/bin/activate
        python -m bot.main

 3. Install as a 24/7 service (edit User=/paths first if needed):
        sudo cp systemd/ai-edit-bot.service /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable --now ai-edit-bot
        journalctl -u ai-edit-bot -f
============================================================
EONOTE
