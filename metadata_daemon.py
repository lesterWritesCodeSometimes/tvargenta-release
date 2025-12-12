#!/usr/bin/env python3
"""
Metadata Population Daemon for TVArgenta

Background service that populates missing metadata for videos:
- duracion: Video duration in seconds
- loudness_lufs: Audio loudness in LUFS for volume normalization
- thumbnails: Preview images for the UI

Runs with low resource priority to avoid impacting system performance.

Usage:
    python3 metadata_daemon.py

The daemon will:
1. Periodically scan for videos missing metadata
2. Process one video at a time with throttling
3. Use nice/ionice for low CPU/IO priority
4. Sleep between videos to avoid resource contention
5. Log all activity to content/logs/metadata_daemon.log
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Configuration
CHECK_INTERVAL = 300          # Seconds between scans when idle (5 minutes)
SLEEP_BETWEEN_VIDEOS = 60     # Seconds to sleep between processing videos
BATCH_SIZE = 10               # Process up to N videos before re-checking queue
NICE_LEVEL = 19               # Lowest CPU priority (19 = nicest)
IONICE_CLASS = 2              # Best-effort I/O class
IONICE_PRIORITY = 7           # Lowest priority within best-effort (0-7)
FFMPEG_THREADS = 1            # Single-threaded FFmpeg

# Paths
ROOT_DIR = Path(__file__).parent
CONTENT_DIR = ROOT_DIR / "content"
VIDEO_DIR = CONTENT_DIR / "videos"
SERIES_VIDEO_DIR = VIDEO_DIR / "series"
COMMERCIALS_DIR = VIDEO_DIR / "commercials"
METADATA_FILE = CONTENT_DIR / "metadata.json"
THUMB_DIR = CONTENT_DIR / "thumbnails"
LOG_DIR = CONTENT_DIR / "logs"
LOG_FILE = LOG_DIR / "metadata_daemon.log"

# State
running = True
logger = None


def setup_logging():
    """Configure logging to file and stdout."""
    global logger

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("metadata_daemon")
    logger.setLevel(logging.INFO)

    # File handler - append mode, with rotation-friendly naming
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Format
    formatter = logging.Formatter(
        "%(asctime)s [META] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def load_metadata():
    """Load metadata from JSON file."""
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_metadata(metadata):
    """Save metadata to JSON file."""
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def get_video_path(video_id, info):
    """Determine the file path for a video based on its metadata."""
    if info.get("commercials_path"):
        return VIDEO_DIR / f"{info['commercials_path']}.mp4"
    elif info.get("series_path"):
        return VIDEO_DIR / f"{info['series_path']}.mp4"
    else:
        return VIDEO_DIR / f"{video_id}.mp4"


def run_throttled(cmd, timeout=600):
    """
    Run a command with nice/ionice for low resource usage.
    Returns (stdout, stderr, success).
    """
    throttled_cmd = [
        "nice", "-n", str(NICE_LEVEL),
        "ionice", "-c", str(IONICE_CLASS), "-n", str(IONICE_PRIORITY),
    ] + cmd

    try:
        result = subprocess.run(
            throttled_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode == 0
    except subprocess.TimeoutExpired:
        return "", "Timeout", False
    except Exception as e:
        return "", str(e), False


def get_duration(filepath):
    """Get video duration using ffprobe with throttling."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]

    stdout, stderr, success = run_throttled(cmd, timeout=60)

    if success and stdout.strip():
        try:
            return float(stdout.strip())
        except ValueError:
            pass

    logger.warning(f"Failed to get duration: {stderr}")
    return None


def analyze_loudness(filepath, duration=None):
    """
    Analyze audio loudness using FFmpeg's ebur128 filter.
    Samples 30 seconds every 5 minutes for efficiency on long files.
    Returns integrated loudness in LUFS, or None if analysis fails.
    """
    SAMPLE_DURATION = 30   # seconds per sample
    SAMPLE_INTERVAL = 300  # seconds between sample starts (5 minutes)

    # Get duration if not provided
    if duration is None:
        duration = get_duration(filepath)

    # Build audio filter
    if duration is None or duration <= SAMPLE_INTERVAL:
        # Short file or unknown duration - analyze entire file
        audio_filter = "ebur128=framelog=verbose"
        timeout = 600  # 10 min for short files
    else:
        # Sample 30 seconds every 5 minutes throughout the file
        samples = []
        t = 0
        while t + SAMPLE_DURATION <= duration:
            samples.append(f"between(t,{t},{t + SAMPLE_DURATION})")
            t += SAMPLE_INTERVAL

        # Add final segment if there's significant time remaining
        if duration - t > 10:  # More than 10 seconds left
            end_start = max(t, duration - SAMPLE_DURATION)
            samples.append(f"between(t,{end_start},{duration})")

        select_expr = "+".join(samples)
        # aselect picks the samples, asetpts fixes timestamps for ebur128
        audio_filter = f"aselect='{select_expr}',asetpts=N/SR/TB,ebur128=framelog=verbose"

        # Timeout based on actual audio to process (samples × duration + overhead)
        audio_seconds = len(samples) * SAMPLE_DURATION
        timeout = max(300, audio_seconds * 3)  # 3x realtime + minimum 5 min

        logger.debug(f"Sampling {len(samples)} segments ({audio_seconds}s total) from {duration:.0f}s file")

    cmd = [
        "ffmpeg",
        "-threads", str(FFMPEG_THREADS),
        "-i", str(filepath),
        "-af", audio_filter,
        "-f", "null", "-"
    ]

    stdout, stderr, success = run_throttled(cmd, timeout=timeout)

    # Check for timeout
    if stderr == "Timeout":
        logger.warning(f"Loudness analysis timed out for {filepath}")
        return None

    # Parse integrated loudness from stderr
    for line in stderr.split('\n'):
        line = line.strip()
        if line.startswith('I:') and 'LUFS' in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == 'LUFS' and i > 0:
                    try:
                        return float(parts[i-1])
                    except ValueError:
                        continue

    logger.warning(f"Failed to parse loudness from output")
    return None


def generate_thumbnail(video_path, thumb_path):
    """Generate a thumbnail image from a video with throttling."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", "00:00:02",
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", "scale=320:-1",
        str(thumb_path)
    ]

    stdout, stderr, success = run_throttled(cmd, timeout=60)

    if not success:
        logger.warning(f"Failed to generate thumbnail: {stderr}")

    return success


def find_videos_needing_work(metadata):
    """
    Find videos that are missing metadata or thumbnails.
    Returns list of (video_id, info, missing_fields) tuples.
    """
    needs_work = []

    for video_id, info in metadata.items():
        missing = []

        # Check for missing duration
        if info.get("duracion") is None:
            missing.append("duracion")

        # Check for missing loudness
        if info.get("loudness_lufs") is None:
            missing.append("loudness_lufs")

        # Check for missing thumbnail
        thumb_path = THUMB_DIR / f"{video_id}.jpg"
        if not thumb_path.exists():
            missing.append("thumbnail")

        if missing:
            needs_work.append((video_id, info, missing))

    return needs_work


def process_one_video(video_id, info, missing_fields, metadata):
    """
    Process a single video to populate missing metadata.
    Returns True if any metadata was updated.
    """
    filepath = get_video_path(video_id, info)

    if not filepath.exists():
        logger.warning(f"File not found: {filepath}")
        return False

    updated = False
    category = info.get("category", "unknown")

    logger.info(f"Processing: {video_id} ({category})")
    logger.info(f"  Missing: {', '.join(missing_fields)}")

    # Get duration if missing
    if "duracion" in missing_fields:
        logger.info(f"  Analyzing duration...")
        duration = get_duration(filepath)
        if duration is not None:
            metadata[video_id]["duracion"] = duration
            logger.info(f"  Duration: {duration:.1f}s")
            updated = True
        else:
            logger.warning(f"  Duration: FAILED")

    # Get loudness if missing
    if "loudness_lufs" in missing_fields:
        logger.info(f"  Analyzing loudness...")
        # Pass duration to enable efficient sampling (avoid re-fetching)
        known_duration = metadata[video_id].get("duracion")
        lufs = analyze_loudness(filepath, duration=known_duration)
        if lufs is not None:
            metadata[video_id]["loudness_lufs"] = lufs
            logger.info(f"  Loudness: {lufs:.1f} LUFS")
            updated = True
        else:
            logger.warning(f"  Loudness: FAILED")

    # Generate thumbnail if missing
    if "thumbnail" in missing_fields:
        logger.info(f"  Generating thumbnail...")
        thumb_path = THUMB_DIR / f"{video_id}.jpg"
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        if generate_thumbnail(filepath, thumb_path):
            logger.info(f"  Thumbnail: OK")
            # Thumbnail isn't stored in metadata, just the file
        else:
            logger.warning(f"  Thumbnail: FAILED")

    return updated


def run_daemon():
    """Main daemon loop."""
    global running

    logger.info("TVArgenta Metadata Daemon starting...")
    logger.info(f"Configuration:")
    logger.info(f"  Check interval (idle): {CHECK_INTERVAL}s")
    logger.info(f"  Sleep between videos: {SLEEP_BETWEEN_VIDEOS}s")
    logger.info(f"  Batch size: {BATCH_SIZE}")
    logger.info(f"  Nice level: {NICE_LEVEL}")
    logger.info(f"  I/O class: {IONICE_CLASS} (best-effort), priority: {IONICE_PRIORITY}")
    logger.info(f"  Log file: {LOG_FILE}")

    while running:
        try:
            # Load current metadata
            metadata = load_metadata()

            if not metadata:
                logger.info("No videos in metadata, sleeping...")
                time.sleep(CHECK_INTERVAL)
                continue

            # Find videos needing work
            needs_work = find_videos_needing_work(metadata)

            if not needs_work:
                logger.info(f"All videos have complete metadata, sleeping {CHECK_INTERVAL}s...")
                time.sleep(CHECK_INTERVAL)
                continue

            total_pending = len(needs_work)
            logger.info(f"Found {total_pending} videos needing metadata")

            # Process up to BATCH_SIZE videos
            batch = needs_work[:BATCH_SIZE]
            processed_count = 0

            for i, (video_id, info, missing_fields) in enumerate(batch):
                if not running:
                    break

                updated = process_one_video(video_id, info, missing_fields, metadata)

                if updated:
                    save_metadata(metadata)
                    logger.info(f"Saved metadata for {video_id}")

                processed_count += 1

                # Sleep between videos (but not after the last one in batch)
                if running and i < len(batch) - 1:
                    logger.info(f"Processed {processed_count}/{len(batch)} in batch, sleeping {SLEEP_BETWEEN_VIDEOS}s...")
                    time.sleep(SLEEP_BETWEEN_VIDEOS)

            # After batch: check if more work remains
            remaining = total_pending - processed_count
            if remaining > 0:
                logger.info(f"Batch complete ({processed_count} processed), {remaining} remaining - checking immediately...")
                # Don't sleep - loop back to check for more work
            else:
                logger.info(f"All caught up ({processed_count} processed), sleeping {CHECK_INTERVAL}s...")
                time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Interrupted by keyboard")
            break

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL)

    logger.info("Daemon stopped")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global running
    logger.info(f"Received signal {signum}, shutting down...")
    running = False


def main():
    """Entry point."""
    # Set up logging first
    setup_logging()

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the daemon
    run_daemon()


if __name__ == "__main__":
    main()
