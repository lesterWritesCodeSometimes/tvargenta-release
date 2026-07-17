# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Channel detection for commercials.

Extracts spoken words (whisper.cpp) and on-screen text (tesseract) from a
commercial and matches them against per-channel alias phrases to determine
which channels the commercial belongs to. Results go in the commercial's
"detected_channels" metadata field; a human-set "channels" field (key present,
even if empty) always takes precedence at read time.

Alias phrases live in canales.json per channel:

    "1": {"nombre": "Nickelodeon", "aliases": ["nick", "nick jr", "snick"], ...}

The channel's nombre is always matched in addition to its aliases. Matching is
whole-phrase with word boundaries on normalized text, so an alias "nick"
matches "on nick tonight" but never "nickel"; bare words like "network" only
match if someone explicitly lists them as an alias.

External tools (all optional — detection degrades to whichever are present):
- ffmpeg: audio extraction and frame sampling
- whisper.cpp CLI (whisper-cli) + a ggml model: speech-to-text
- tesseract: OCR on sampled frames
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Speech-to-text: whisper.cpp binary and model, overridable via environment
WHISPER_BIN = os.environ.get("TVARGENTA_WHISPER_BIN") or shutil.which("whisper-cli")
WHISPER_MODEL = os.environ.get(
    "TVARGENTA_WHISPER_MODEL", "/usr/local/share/whisper/ggml-tiny.en.bin"
)
WHISPER_THREADS = 2

TESSERACT_BIN = os.environ.get("TVARGENTA_TESSERACT_BIN") or shutil.which("tesseract")

OCR_FPS = 1          # frames per second sampled for OCR
OCR_MAX_FRAMES = 120  # cap OCR work on unusually long commercials


def _run(cmd, timeout):
    """Default command runner. The daemon passes its own throttled runner."""
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode == 0
    except subprocess.TimeoutExpired:
        return "", "Timeout", False
    except Exception as e:
        return "", str(e), False


def stt_available():
    return bool(WHISPER_BIN) and Path(WHISPER_MODEL).exists()


def ocr_available():
    return bool(TESSERACT_BIN)


def detection_available():
    return stt_available() or ocr_available()


def normalize_text(text):
    """Lowercase, strip everything but letters/digits, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def get_channel_phrases(canales):
    """
    Build {channel_id: [normalized phrases]} from canales.json data.
    Each channel matches on its nombre plus any "aliases" entries.
    Only broadcast channels (those with a series_filter) participate.
    """
    phrases = {}
    for channel_id, config in canales.items():
        if not config.get("series_filter"):
            continue
        candidates = [config.get("nombre", "")] + list(config.get("aliases", []))
        normalized = [normalize_text(c) for c in candidates]
        phrases[channel_id] = sorted({p for p in normalized if p})
    return phrases


def match_channels(text, channel_phrases):
    """
    Match normalized alias phrases against text (whole phrases, word boundaries).
    Returns {channel_id: [matched phrases]} for channels with at least one hit.
    """
    haystack = f" {normalize_text(text)} "
    hits = {}
    for channel_id, phrases in channel_phrases.items():
        matched = [p for p in phrases if f" {p} " in haystack]
        if matched:
            hits[channel_id] = matched
    return hits


def transcribe_audio(video_path, duration=None, run_cmd=_run):
    """
    Extract the audio track and run whisper.cpp on it.
    Returns the transcript text ("" on failure or no speech).
    """
    if not stt_available():
        return ""

    timeout = max(300, int(duration or 0) * 10)

    with tempfile.TemporaryDirectory(prefix="tva_stt_") as tmpdir:
        wav_path = Path(tmpdir) / "audio.wav"

        # whisper.cpp wants 16kHz mono WAV
        _, stderr, ok = run_cmd([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)
        ], timeout=120)
        if not ok or not wav_path.exists():
            return ""

        stdout, stderr, ok = run_cmd([
            WHISPER_BIN,
            "-m", WHISPER_MODEL,
            "-f", str(wav_path),
            "-t", str(WHISPER_THREADS),
            "-np",  # no progress/system prints
            "-nt",  # no timestamps, plain text lines
        ], timeout=timeout)
        if not ok:
            return ""

        return stdout


def ocr_video_frames(video_path, run_cmd=_run):
    """
    Sample frames from the video and OCR each one.
    Returns the concatenated OCR text ("" on failure or no text).
    """
    if not ocr_available():
        return ""

    texts = []
    with tempfile.TemporaryDirectory(prefix="tva_ocr_") as tmpdir:
        _, stderr, ok = run_cmd([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"fps={OCR_FPS},scale=960:-2",
            "-frames:v", str(OCR_MAX_FRAMES),
            str(Path(tmpdir) / "frame_%04d.jpg")
        ], timeout=300)
        if not ok:
            return ""

        for frame in sorted(Path(tmpdir).glob("frame_*.jpg")):
            stdout, _, ok = run_cmd(
                [TESSERACT_BIN, str(frame), "stdout"], timeout=60
            )
            if ok and stdout.strip():
                texts.append(stdout)

    return "\n".join(texts)


def detect_channels(video_path, canales, duration=None, run_cmd=_run):
    """
    Full detection pass for one commercial.
    Returns (channel_ids, evidence) where channel_ids is a sorted list and
    evidence maps channel_id -> {"audio": [phrases], "screen": [phrases]}.
    """
    channel_phrases = get_channel_phrases(canales)
    if not channel_phrases:
        return [], {}

    evidence = {}

    transcript = transcribe_audio(video_path, duration=duration, run_cmd=run_cmd)
    for channel_id, matched in match_channels(transcript, channel_phrases).items():
        evidence.setdefault(channel_id, {})["audio"] = matched

    screen_text = ocr_video_frames(video_path, run_cmd=run_cmd)
    for channel_id, matched in match_channels(screen_text, channel_phrases).items():
        evidence.setdefault(channel_id, {})["screen"] = matched

    return sorted(evidence.keys()), evidence
