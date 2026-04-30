#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="live-dictation"
SERVICE_DIR="$HOME/.config/systemd/user"

echo "[install] Project directory: $PROJECT_DIR"

# ── 1. apt dependencies ──────────────────────────────────────────────────────
echo "[install] Installing apt packages…"
sudo apt-get update -qq
sudo apt-get install -y \
    xdotool \
    xclip \
    portaudio19-dev \
    python3-venv \
    python3-dev \
    libnotify-bin
# python3-dev provides Python.h, required to build pynput's evdev dependency.
# NOTE: do NOT install python3-xlib from apt — pynput pulls python-xlib from
# PyPI which is newer; installing both risks version conflicts.

# ── 2. Python virtual environment ────────────────────────────────────────────
echo "[install] Creating .venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade --quiet pip

# ── 3. Python package installation ───────────────────────────────────────────
echo "[install] Installing Python dependencies…"
"$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"

# ── 4. Pre-download Whisper model ─────────────────────────────────────────────
# Downloads ~460 MB to ~/.cache/huggingface/hub/models--Systran--faster-whisper-small/
echo "[install] Downloading Whisper small model (~460 MB, one-time)…"
"$VENV_DIR/bin/python" - <<'EOF'
from faster_whisper import WhisperModel
print("Downloading…")
WhisperModel("small", device="cpu", compute_type="int8")
print("Model downloaded and verified.")
EOF

# ── 5. systemd user service installation ─────────────────────────────────────
echo "[install] Installing systemd user service…"
mkdir -p "$SERVICE_DIR"
cp "$PROJECT_DIR/$SERVICE_NAME.service" "$SERVICE_DIR/$SERVICE_NAME.service"
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service"
systemctl --user start "$SERVICE_NAME.service"

echo ""
echo "[install] Done. live-dictation is running."
echo ""
echo "  Status : systemctl --user status $SERVICE_NAME"
echo "  Logs   : journalctl --user -u $SERVICE_NAME -f"
echo "  Stop   : systemctl --user stop $SERVICE_NAME"
echo "  Start  : systemctl --user start $SERVICE_NAME"
echo ""
echo "Hold Right Ctrl to record, release to transcribe."

# ── XDG autostart fallback (uncomment if systemd DISPLAY forwarding fails) ───
#
# AUTOSTART_DIR="$HOME/.config/autostart"
# mkdir -p "$AUTOSTART_DIR"
# cat > "$AUTOSTART_DIR/live-dictation.desktop" <<DESKTOP
# [Desktop Entry]
# Type=Application
# Name=live-dictation
# Exec=$VENV_DIR/bin/python $PROJECT_DIR/dictate.py
# Hidden=false
# NoDisplay=false
# X-GNOME-Autostart-enabled=true
# Comment=Push-to-talk voice dictation daemon
# DESKTOP
# echo "[install] XDG autostart entry created at $AUTOSTART_DIR/live-dictation.desktop"
