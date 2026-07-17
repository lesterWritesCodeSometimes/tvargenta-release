#!/usr/bin/env python3
"""
Metadata Population Daemon for TVArgenta

Background service that discovers videos and populates missing metadata using a three-phase approach:

Phase 0 (Directory Scan):
- Scans content/videos/series/ for TV episode files
- Scans content/videos/commercials/ for commercial files
- Adds newly discovered videos to metadata.json

Phase 1 (Fast Metadata):
- duracion: Video duration in seconds
- thumbnails: Preview images for the UI

Phase 2 (Loudness Analysis):
- loudness_lufs: Audio loudness in LUFS for volume normalization

Phase 3 (Channel Detection, commercials only):
- detected_channels: Channels whose name/aliases appear in the commercial's
  speech (whisper.cpp) or on-screen text (tesseract). A human-set "channels"
  field always wins over this at read time. Skipped when neither tool is
  installed.

Runs with low resource priority (nice/ionice) but processes videos continuously
without throttling between them.

Usage:
    python3 metadata_daemon.py

The daemon will:
1. Phase 0: Scan directories for new videos (series + commercials)
2. Phase 1: Process ALL videos for duration and thumbnails (fast)
3. Phase 2: Process ALL videos for loudness analysis (slow)
4. Phase 3: Process commercials for channel detection (slow)
5. Sleep only when all metadata is complete
6. Use nice/ionice for low CPU/IO priority
7. Log all activity to content/logs/metadata_daemon.log
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import channel_detection

# Configuration
CHECK_INTERVAL = 300          # Seconds between scans when all metadata is complete (5 minutes)
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
METADATA_LOCK_FILE = CONTENT_DIR / ".metadata.lock"
SERIES_FILE = CONTENT_DIR / "series.json"
CANALES_FILE = CONTENT_DIR / "canales.json"
CHANNEL_CACHE_FILE = CONTENT_DIR / "channel_detection_cache.json"
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


@contextmanager
def metadata_lock(timeout=30):
    """
    Context manager for exclusive access to metadata.json.
    Prevents race conditions with app.py.
    """
    METADATA_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(METADATA_LOCK_FILE, 'w')
    try:
        start = time.time()
        while True:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - start > timeout:
                    raise TimeoutError(f"Could not acquire metadata lock within {timeout}s")
                time.sleep(0.1)
        yield
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def load_metadata():
    """Load metadata from JSON file."""
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_metadata_fields(video_id, fields_to_update):
    """
    Safely update specific fields for a video in metadata.json.
    Uses locking and reloads fresh data to avoid overwriting other changes.

    Args:
        video_id: The video ID to update
        fields_to_update: Dict of field_name -> value to update
    """
    with metadata_lock():
        # Reload fresh metadata to avoid overwriting changes made by app.py
        current_metadata = load_metadata()

        if video_id in current_metadata:
            for field, value in fields_to_update.items():
                current_metadata[video_id][field] = value

            # Atomic write
            tmp = METADATA_FILE.with_suffix('.json.tmp')
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(current_metadata, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, METADATA_FILE)


def load_series():
    """Load series data from series.json."""
    if SERIES_FILE.exists():
        with open(SERIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_series(data):
    """Save series data to series.json with atomic write."""
    tmp = SERIES_FILE.with_suffix('.json.tmp')
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SERIES_FILE)


def parse_episode_info(filename):
    """
    Parse season/episode from filename. Returns (season, episode) or (None, None).
    Supports: S01E05, s1e5, 1x05, Season 1 Episode 5, Season1Episode5
    """
    patterns = [
        r'[Ss](\d+)[Ee](\d+)',                      # S01E05, s1e5
        r'(\d+)[xX](\d+)',                           # 1x05
        r'[Ss]eason\s*(\d+)\s*[Ee]pisode\s*(\d+)',  # Season 1 Episode 5, Season1Episode5
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


def scan_series_directories():
    """
    Phase 0a: Scan series directories for new videos.
    Discovers series folders and video files, adds them to metadata.json and series.json.
    Returns number of new videos discovered.
    """
    if not SERIES_VIDEO_DIR.exists():
        SERIES_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        return 0

    series_data = load_series()
    changes_made = False
    new_videos = 0

    with metadata_lock():
        metadata = load_metadata()

        # Scan for series directories
        for series_dir in SERIES_VIDEO_DIR.iterdir():
            if not series_dir.is_dir():
                continue

            series_name = series_dir.name

            # Add to series.json if not present
            if series_name not in series_data:
                series_data[series_name] = {
                    "created": datetime.now().strftime("%Y-%m-%d")
                }
                logger.info(f"Discovered new series: {series_name}")
                changes_made = True

            # Scan for video files in this series
            for video_file in series_dir.glob("*.mp4"):
                video_id = video_file.stem  # filename without extension
                series_path = f"series/{series_name}/{video_id}"

                # Check if we already have metadata for this video
                existing = metadata.get(video_id, {})

                # Parse season/episode from filename
                season, episode = parse_episode_info(video_id)

                # Create or update metadata if missing or path changed
                if video_id not in metadata or existing.get("series_path") != series_path:
                    metadata[video_id] = {
                        "title": existing.get("title") or video_id,
                        "category": "tv_episode",
                        "series": series_name,
                        "series_path": series_path,
                        "season": existing.get("season") or season,
                        "episode": existing.get("episode") or episode,
                        "tags": existing.get("tags", []),
                        "personaje": existing.get("personaje", ""),
                        "fecha": existing.get("fecha", ""),
                        "modo": existing.get("modo", []),
                        "duracion": existing.get("duracion"),
                        "loudness_lufs": existing.get("loudness_lufs")
                    }
                    logger.info(f"Added series video: {series_path}")
                    changes_made = True
                    new_videos += 1

        # Save changes
        if changes_made:
            # Save metadata with atomic write
            tmp = METADATA_FILE.with_suffix('.json.tmp')
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, METADATA_FILE)

            save_series(series_data)

    return new_videos


def scan_commercials_directory():
    """
    Phase 0b: Scan commercials directory for new videos.
    Discovers commercial video files and adds them to metadata.json.
    Returns number of new videos discovered.
    """
    if not COMMERCIALS_DIR.exists():
        COMMERCIALS_DIR.mkdir(parents=True, exist_ok=True)
        return 0

    changes_made = False
    new_videos = 0

    with metadata_lock():
        metadata = load_metadata()

        # Scan for video files in commercials directory
        for video_file in COMMERCIALS_DIR.glob("*.mp4"):
            video_id = video_file.stem  # filename without extension
            commercials_path = f"commercials/{video_id}"

            # Check if we already have metadata for this video
            existing = metadata.get(video_id, {})

            # Create metadata if missing
            if video_id not in metadata:
                metadata[video_id] = {
                    "title": video_id,
                    "category": "commercial",
                    "commercials_path": commercials_path,
                    "tags": existing.get("tags", []),
                    "personaje": existing.get("personaje", ""),
                    "fecha": existing.get("fecha", ""),
                    "modo": existing.get("modo", []),
                    "duracion": existing.get("duracion"),
                    "loudness_lufs": existing.get("loudness_lufs")
                }
                logger.info(f"Added commercial: {commercials_path}")
                changes_made = True
                new_videos += 1

        # Save changes
        if changes_made:
            tmp = METADATA_FILE.with_suffix('.json.tmp')
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, METADATA_FILE)

    return new_videos


def scan_all_directories():
    """
    Phase 0: Scan all video directories for new content.
    Returns total number of new videos discovered.
    """
    logger.info("=" * 50)
    logger.info("PHASE 0: Directory Scan")
    logger.info("=" * 50)

    series_count = scan_series_directories()
    commercials_count = scan_commercials_directory()

    total = series_count + commercials_count
    if total > 0:
        logger.info(f"[Phase 0] Discovered {series_count} series videos, {commercials_count} commercials")
    else:
        logger.info("[Phase 0] No new videos discovered")

    return total


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


def find_videos_needing_fast_metadata(metadata):
    """
    Phase 1: Find videos missing duration or thumbnails.
    These are fast operations that should complete quickly.
    Returns list of (video_id, info, missing_fields) tuples.
    """
    needs_work = []

    for video_id, info in metadata.items():
        missing = []

        # Check for missing duration
        if info.get("duracion") is None:
            missing.append("duracion")

        # Check for missing thumbnail
        thumb_path = THUMB_DIR / f"{video_id}.jpg"
        if not thumb_path.exists():
            missing.append("thumbnail")

        if missing:
            needs_work.append((video_id, info, missing))

    return needs_work


def find_videos_needing_loudness(metadata):
    """
    Phase 2: Find videos missing loudness analysis.
    This is a slower operation that requires full audio processing.
    Returns list of (video_id, info, missing_fields) tuples.
    """
    needs_work = []

    for video_id, info in metadata.items():
        # Check for missing loudness
        if info.get("loudness_lufs") is None:
            needs_work.append((video_id, info, ["loudness_lufs"]))

    return needs_work


def load_canales():
    """Load channel configurations from canales.json."""
    if CANALES_FILE.exists():
        with open(CANALES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def find_commercials_needing_channel_detection(metadata):
    """
    Phase 3: Find channel-detection work.
    Work exists when a commercial has no cached extraction (needs the tools),
    has a cached extraction but no detected_channels field (match is free),
    or the alias phrases changed since the cache was last matched (rematch).
    Returns a list of work descriptors; empty means the daemon can idle.
    """
    phrases = channel_detection.get_channel_phrases(load_canales())
    if not phrases:
        return []

    cache = channel_detection.load_cache(CHANNEL_CACHE_FILE)
    entries = cache.get("entries", {})

    needs_work = []
    for video_id, info in metadata.items():
        if info.get("category") != "commercial":
            continue
        if video_id not in entries:
            if channel_detection.detection_available():
                needs_work.append((video_id, info, ["extract"]))
        elif "detected_channels" not in info:
            needs_work.append((video_id, info, ["match"]))

    if entries and cache.get("phrases_fingerprint") != channel_detection.phrases_fingerprint(phrases):
        needs_work.append(("__phrases_changed__", {}, ["rematch"]))

    return needs_work


def save_channel_match(video_id, channels, evidence, canales):
    """Persist one commercial's match result and log the verdict."""
    save_metadata_fields(video_id, {
        "detected_channels": channels,
        "detected_channels_evidence": evidence,
    })
    if channels:
        names = {cid: canales.get(cid, {}).get("nombre", cid) for cid in channels}
        logger.info(f"[Phase 3] {video_id} -> {names} (evidence: {evidence})")
    else:
        logger.info(f"[Phase 3] {video_id} -> no channel mentions (all channels)")


def run_channel_detection_phase():
    """
    Phase 3: Channel detection for commercials.
    Extraction (STT + OCR) runs once per commercial and is cached; matching
    runs from the cache and is re-run for every commercial whenever the alias
    phrases change. Returns number of commercials processed.
    """
    global running

    metadata = load_metadata()
    canales = load_canales()
    phrases = channel_detection.get_channel_phrases(canales)
    if not phrases:
        return 0

    cache = channel_detection.load_cache(CHANNEL_CACHE_FILE)
    entries = cache.get("entries", {})
    fingerprint = channel_detection.phrases_fingerprint(phrases)
    processed = 0

    commercials = {vid: info for vid, info in metadata.items()
                   if info.get("category") == "commercial"}

    # Drop cache entries for commercials that no longer exist
    stale = [vid for vid in entries if vid not in commercials]
    for vid in stale:
        del entries[vid]
    if stale:
        logger.info(f"[Phase 3] Pruned {len(stale)} stale cache entries")

    # Rematch everything cached when the alias phrases changed (covers both
    # UI edits and hand edits of canales.json), or when a commercial has a
    # cached extraction but no verdict yet. Text-vs-text: effectively free.
    rematch_all = bool(entries) and cache.get("phrases_fingerprint") != fingerprint
    if rematch_all:
        logger.info(f"[Phase 3] Alias phrases changed - rematching {len(entries)} cached commercials")
    for vid, entry in entries.items():
        if not running:
            break
        if not rematch_all and "detected_channels" in commercials[vid]:
            continue
        channels, evidence = channel_detection.match_entry(entry, phrases)
        info = commercials[vid]
        if (info.get("detected_channels") != channels
                or info.get("detected_channels_evidence") != evidence):
            save_channel_match(vid, channels, evidence, canales)
        processed += 1

    # Only mark the phrases as matched if the rematch wasn't interrupted;
    # otherwise the next cycle picks up where this one left off.
    if running:
        cache["phrases_fingerprint"] = fingerprint
    with metadata_lock():
        channel_detection.save_cache(CHANNEL_CACHE_FILE, cache)

    # Extract text for commercials not yet cached (the expensive part)
    to_extract = [vid for vid in commercials if vid not in entries]
    if to_extract and channel_detection.detection_available():
        total = len(to_extract)
        logger.info(f"[Phase 3] Found {total} commercials to analyze")
        logger.info(f"[Phase 3] STT: {'yes' if channel_detection.stt_available() else 'no'}, "
                    f"OCR: {'yes' if channel_detection.ocr_available() else 'no'}")

        for i, vid in enumerate(to_extract):
            if not running:
                break
            info = commercials[vid]
            filepath = get_video_path(vid, info)
            if not filepath.exists():
                logger.warning(f"[Phase 3] File not found: {filepath}")
                continue

            logger.info(f"[Phase 3] Extracting {i + 1}/{total}: {vid}")
            try:
                entry = channel_detection.extract_text(
                    filepath, duration=info.get("duracion"), run_cmd=run_throttled)
            except Exception as e:
                logger.error(f"[Phase 3] Extraction failed for {vid}: {e}")
                continue

            entries[vid] = entry
            with metadata_lock():
                channel_detection.save_cache(CHANNEL_CACHE_FILE, cache)

            channels, evidence = channel_detection.match_entry(entry, phrases)
            save_channel_match(vid, channels, evidence, canales)
            processed += 1

    logger.info(f"[Phase 3] Complete - processed {processed} commercials")
    return processed


def process_one_video(video_id, info, missing_fields):
    """
    Process a single video to populate missing metadata.
    Returns dict of fields that were updated, or empty dict if none.
    """
    filepath = get_video_path(video_id, info)

    if not filepath.exists():
        logger.warning(f"File not found: {filepath}")
        return {}

    updates = {}
    category = info.get("category", "unknown")

    logger.info(f"Processing: {video_id} ({category})")
    logger.info(f"  Missing: {', '.join(missing_fields)}")

    # Get duration if missing
    if "duracion" in missing_fields:
        logger.info(f"  Analyzing duration...")
        duration = get_duration(filepath)
        if duration is not None:
            updates["duracion"] = duration
            logger.info(f"  Duration: {duration:.1f}s")
        else:
            logger.warning(f"  Duration: FAILED")

    # Get loudness if missing
    if "loudness_lufs" in missing_fields:
        logger.info(f"  Analyzing loudness...")
        # Pass duration to enable efficient sampling (avoid re-fetching)
        known_duration = info.get("duracion") or updates.get("duracion")
        lufs = analyze_loudness(filepath, duration=known_duration)
        if lufs is not None:
            updates["loudness_lufs"] = lufs
            logger.info(f"  Loudness: {lufs:.1f} LUFS")
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

    return updates


def run_phase(phase_name, find_func):
    """
    Run a single phase of metadata processing.
    Processes all videos without throttling between them.

    Args:
        phase_name: Name of the phase for logging
        find_func: Function to find videos needing work for this phase

    Returns:
        Number of videos processed
    """
    global running

    metadata = load_metadata()
    if not metadata:
        return 0

    needs_work = find_func(metadata)
    if not needs_work:
        return 0

    total = len(needs_work)
    logger.info(f"[{phase_name}] Found {total} videos to process")

    processed = 0
    for video_id, info, missing_fields in needs_work:
        if not running:
            break

        logger.info(f"[{phase_name}] Processing {processed + 1}/{total}: {video_id}")
        updates = process_one_video(video_id, info, missing_fields)

        if updates:
            save_metadata_fields(video_id, updates)
            logger.info(f"[{phase_name}] Saved: {list(updates.keys())}")

        processed += 1

    logger.info(f"[{phase_name}] Complete - processed {processed} videos")
    return processed


def run_daemon():
    """Main daemon loop with three-phase processing."""
    global running

    logger.info("TVArgenta Metadata Daemon starting...")
    logger.info(f"Configuration:")
    logger.info(f"  Check interval (idle): {CHECK_INTERVAL}s")
    logger.info(f"  Nice level: {NICE_LEVEL}")
    logger.info(f"  I/O class: {IONICE_CLASS} (best-effort), priority: {IONICE_PRIORITY}")
    logger.info(f"  Log file: {LOG_FILE}")
    logger.info(f"Four-phase operation:")
    logger.info(f"  Phase 0: Directory Scan (discover new videos)")
    logger.info(f"  Phase 1: Duration + Thumbnails (fast)")
    logger.info(f"  Phase 2: Loudness Analysis (slow)")
    logger.info(f"  Phase 3: Channel Detection (commercials, slow)")
    if not channel_detection.detection_available():
        logger.warning("Phase 3 disabled: neither whisper-cli nor tesseract found "
                       "(install them to enable commercial channel detection)")

    while running:
        try:
            # Phase 0: Scan directories for new videos
            scan_all_directories()

            if not running:
                break

            # Load current metadata (may have been updated by Phase 0)
            metadata = load_metadata()

            if not metadata:
                logger.info("No videos in metadata, sleeping...")
                time.sleep(CHECK_INTERVAL)
                continue

            # Check if any work is needed
            fast_work = find_videos_needing_fast_metadata(metadata)
            loudness_work = find_videos_needing_loudness(metadata)
            detection_work = find_commercials_needing_channel_detection(metadata)

            if not fast_work and not loudness_work and not detection_work:
                logger.info(f"All videos have complete metadata, sleeping {CHECK_INTERVAL}s...")
                time.sleep(CHECK_INTERVAL)
                continue

            # Phase 1: Fast metadata (duration + thumbnails)
            if fast_work and running:
                logger.info("=" * 50)
                logger.info("PHASE 1: Fast Metadata (duration + thumbnails)")
                logger.info("=" * 50)
                run_phase("Phase 1", find_videos_needing_fast_metadata)

            # Phase 2: Loudness analysis
            if running:
                # Reload metadata to get fresh state after phase 1
                loudness_work = find_videos_needing_loudness(load_metadata())
                if loudness_work:
                    logger.info("=" * 50)
                    logger.info("PHASE 2: Loudness Analysis")
                    logger.info("=" * 50)
                    run_phase("Phase 2", find_videos_needing_loudness)

            # Phase 3: Channel detection for commercials
            if running:
                detection_work = find_commercials_needing_channel_detection(load_metadata())
                if detection_work:
                    logger.info("=" * 50)
                    logger.info("PHASE 3: Channel Detection")
                    logger.info("=" * 50)
                    run_channel_detection_phase()

            # After all phases complete, loop back to check for new videos
            logger.info("All phases complete, checking for new videos...")

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
