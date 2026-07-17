#!/bin/bash
# Install dependencies for metadata daemon Phase 3 (commercial channel detection):
#   - tesseract-ocr for on-screen text extraction
#   - whisper.cpp (whisper-cli + tiny.en model) for speech-to-text
# Run as root on the device:  sudo ./install_channel_detection.sh
# Safe to re-run; each step is skipped if already done.
set -euo pipefail

WHISPER_SRC=/opt/whisper.cpp
WHISPER_MODEL_DIR=/usr/local/share/whisper
WHISPER_MODEL=$WHISPER_MODEL_DIR/ggml-tiny.en.bin
MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin

echo "== Installing tesseract and build tools =="
apt-get install -y tesseract-ocr cmake build-essential git curl
tesseract --version | head -1

if [ ! -x /usr/local/bin/whisper-cli ]; then
  echo "== Building whisper.cpp (this takes ~10-15 min on a Pi 4) =="
  if [ ! -d "$WHISPER_SRC" ]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_SRC"
  fi
  cd "$WHISPER_SRC"
  # Static build so whisper-cli is a single self-contained binary;
  # -j2 keeps peak memory within the Pi's 2GB during compilation.
  cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
  cmake --build build -j2 --target whisper-cli
  install -m 755 build/bin/whisper-cli /usr/local/bin/whisper-cli
else
  echo "== whisper-cli already installed, skipping build =="
fi

if [ ! -f "$WHISPER_MODEL" ]; then
  echo "== Downloading whisper tiny.en model (~78MB) =="
  mkdir -p "$WHISPER_MODEL_DIR"
  curl -L --fail -o "$WHISPER_MODEL.tmp" "$MODEL_URL"
  mv "$WHISPER_MODEL.tmp" "$WHISPER_MODEL"
else
  echo "== Whisper model already present, skipping download =="
fi

echo "== Restarting TVArgenta so the daemon picks up the tools =="
systemctl restart tvargenta.service

echo "Done. Watch detection progress with:"
echo "  tail -f /srv/tvargenta/content/logs/metadata_daemon.log"
