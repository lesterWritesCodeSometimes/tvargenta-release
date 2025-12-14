# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Broadcast TV Scheduler Module

This module implements broadcast-style TV scheduling for channels configured with TV series.
It generates weekly and daily schedules that emulate traditional broadcast television:

- Weekly schedule (Sundays 2:30am): Assigns which series play at which times of day
- Daily schedule (3am): Generates second-by-second playback mapping
- Test pattern plays from 3am-4am daily
- 30-minute blocks with commercial breaks (start, middle, end of each block)
- Episode cursor tracking for chronological progression
"""

import json
import logging
import os
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from settings import (
    CONTENT_DIR, VIDEO_DIR, METADATA_FILE, CANALES_FILE, SERIES_FILE,
    SERIES_VIDEO_DIR
)

logger = logging.getLogger("tvargenta.scheduler")

# ============================================================================
# CONSTANTS
# ============================================================================

# Schedule file paths
WEEKLY_SCHEDULE_FILE = CONTENT_DIR / "weekly_schedule.json"
DAILY_SCHEDULE_FILE = CONTENT_DIR / "daily_schedule.json"
EPISODE_CURSORS_FILE = CONTENT_DIR / "episode_cursors.json"
SCHEDULE_META_FILE = CONTENT_DIR / "schedule_meta.json"

# System video directory (for test pattern, sponsors placeholder)
SYSTEM_VIDEO_DIR = VIDEO_DIR / "system"

# Test pattern and placeholder videos
TEST_PATTERN_VIDEO = SYSTEM_VIDEO_DIR / "test_pattern.mp4"
SPONSORS_PLACEHOLDER_VIDEO = SYSTEM_VIDEO_DIR / "sponsors_placeholder.mp4"

# Time-of-day definitions (hour ranges)
TIME_OF_DAY_RANGES = {
    "early_morning": (4, 7),    # 4am - 7am
    "late_morning": (7, 12),    # 7am - 12pm
    "afternoon": (12, 17),      # 12pm - 5pm
    "evening": (17, 21),        # 5pm - 9pm
    "night": (21, 27),          # 9pm - 3am (27 = 3am next day)
}

# Time-of-day slot counts (30-minute blocks)
TIME_OF_DAY_SLOTS = {
    "early_morning": 6,   # 4am-7am = 3 hours = 6 slots
    "late_morning": 10,   # 7am-12pm = 5 hours = 10 slots
    "afternoon": 10,      # 12pm-5pm = 5 hours = 10 slots
    "evening": 8,         # 5pm-9pm = 4 hours = 8 slots
    "night": 12,          # 9pm-3am = 6 hours = 12 slots
}

# Back-to-back episode probability weighting
BACK_TO_BACK_WEIGHTS = {
    2: 80,  # 80% chance of 2 episodes back-to-back
    3: 10,  # 10% chance of 3 episodes
    4: 5,   # 5% chance of 4 episodes
    5: 3,   # 3% chance of 5 episodes
    6: 2,   # 2% chance of 6 episodes
}

# Episode duration thresholds (in seconds)
VERY_SHORT_EPISODE_MAX = 10 * 60      # < 10 min: 3 episodes per block
SHORT_EPISODE_MAX = 15 * 60           # 10-15 min: 2 episodes per block
MEDIUM_EPISODE_MAX = 28 * 60          # 15-28 min: 1 episode per block
LONG_EPISODE_MAX = 58 * 60            # 28-58 min: spans 2 blocks

# Block duration
BLOCK_DURATION_SEC = 30 * 60  # 30 minutes in seconds

# Commercial breaks per block
COMMERCIAL_BREAKS_PER_BLOCK = 3

# Schedule timing
WEEKLY_SCHEDULE_HOUR = 2
WEEKLY_SCHEDULE_MINUTE = 30
DAILY_SCHEDULE_HOUR = 3
DAILY_SCHEDULE_MINUTE = 0
TEST_PATTERN_START_HOUR = 3
TEST_PATTERN_END_HOUR = 4

# Background loop interval
SCHEDULER_CHECK_INTERVAL = 5  # seconds

# ============================================================================
# IN-MEMORY SCHEDULE CACHE
# ============================================================================

# Cache for daily schedule to eliminate disk I/O on hot path (channel switching)
_daily_schedule_cache: Optional[dict] = None
_daily_schedule_cache_lock = threading.Lock()

# ============================================================================
# DATA LOADING UTILITIES
# ============================================================================

def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON data atomically to prevent corruption."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_metadata() -> dict:
    """Load video metadata from metadata.json."""
    try:
        if METADATA_FILE.exists():
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading metadata.json: {e}")
    return {}


def load_series() -> dict:
    """Load series data from series.json."""
    try:
        if SERIES_FILE.exists():
            with open(SERIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading series.json: {e}")
    return {}


def save_series(data: dict) -> None:
    """Save series data to series.json."""
    _write_json_atomic(SERIES_FILE, data)


def load_canales() -> dict:
    """Load channel configurations from canales.json."""
    try:
        if CANALES_FILE.exists():
            with open(CANALES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading canales.json: {e}")
    return {}


def load_weekly_schedule() -> dict:
    """Load weekly schedule from file."""
    try:
        if WEEKLY_SCHEDULE_FILE.exists():
            with open(WEEKLY_SCHEDULE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading weekly_schedule.json: {e}")
    return {}


def save_weekly_schedule(data: dict) -> None:
    """Save weekly schedule to file."""
    _write_json_atomic(WEEKLY_SCHEDULE_FILE, data)


def load_daily_schedule() -> dict:
    """Load daily schedule from cache, falling back to file on cold start."""
    global _daily_schedule_cache

    with _daily_schedule_cache_lock:
        # Return cached version if available
        if _daily_schedule_cache is not None:
            return _daily_schedule_cache

        # Cold start: load from disk and populate cache
        try:
            if DAILY_SCHEDULE_FILE.exists():
                with open(DAILY_SCHEDULE_FILE, "r", encoding="utf-8") as f:
                    _daily_schedule_cache = json.load(f)
                    return _daily_schedule_cache
        except Exception as e:
            logger.error(f"[SCHEDULER] Error loading daily_schedule.json: {e}")

        return {}


def save_daily_schedule(data: dict) -> None:
    """Save daily schedule to file and update cache."""
    global _daily_schedule_cache

    _write_json_atomic(DAILY_SCHEDULE_FILE, data)

    # Update the in-memory cache (replaces previous day's cache)
    with _daily_schedule_cache_lock:
        _daily_schedule_cache = data

    logger.info("[SCHEDULER] Daily schedule cache updated")


def warm_daily_schedule_cache() -> bool:
    """
    Pre-warm the daily schedule cache on startup.
    Call this during initialization to avoid disk I/O on first channel switch.
    Returns True if cache was warmed, False if no schedule exists.
    """
    global _daily_schedule_cache

    with _daily_schedule_cache_lock:
        # Already cached
        if _daily_schedule_cache is not None:
            return True

        # Load from disk
        try:
            if DAILY_SCHEDULE_FILE.exists():
                with open(DAILY_SCHEDULE_FILE, "r", encoding="utf-8") as f:
                    _daily_schedule_cache = json.load(f)
                logger.info("[SCHEDULER] Daily schedule cache warmed from disk")
                return True
        except Exception as e:
            logger.error(f"[SCHEDULER] Error warming cache: {e}")

    return False


def load_episode_cursors() -> dict:
    """Load episode cursor positions from file."""
    try:
        if EPISODE_CURSORS_FILE.exists():
            with open(EPISODE_CURSORS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading episode_cursors.json: {e}")
    return {}


def save_episode_cursors(data: dict) -> None:
    """Save episode cursor positions to file."""
    _write_json_atomic(EPISODE_CURSORS_FILE, data)


def load_schedule_meta() -> dict:
    """Load schedule generation metadata."""
    try:
        if SCHEDULE_META_FILE.exists():
            with open(SCHEDULE_META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SCHEDULER] Error loading schedule_meta.json: {e}")
    return {}


def save_schedule_meta(data: dict) -> None:
    """Save schedule generation metadata."""
    _write_json_atomic(SCHEDULE_META_FILE, data)


# ============================================================================
# SERIES UTILITIES
# ============================================================================

def get_series_time_of_day(series_name: str) -> str:
    """Get the time-of-day preference for a series."""
    series_data = load_series()
    series_info = series_data.get(series_name, {})
    return series_info.get("time_of_day", "any")


def set_series_time_of_day(series_name: str, time_of_day: str) -> bool:
    """Set the time-of-day preference for a series."""
    valid_options = list(TIME_OF_DAY_RANGES.keys()) + ["any"]
    if time_of_day not in valid_options:
        logger.error(f"[SCHEDULER] Invalid time_of_day: {time_of_day}")
        return False

    series_data = load_series()
    if series_name not in series_data:
        logger.error(f"[SCHEDULER] Series not found: {series_name}")
        return False

    series_data[series_name]["time_of_day"] = time_of_day
    save_series(series_data)
    logger.info(f"[SCHEDULER] Set time_of_day for {series_name} to {time_of_day}")
    return True


def get_series_episodes(series_name: str, metadata: dict = None) -> List[dict]:
    """
    Get all episodes for a series, sorted chronologically.
    Returns list of dicts with video_id, season, episode, duration, series_path.
    """
    if metadata is None:
        metadata = load_metadata()

    episodes = []
    for video_id, data in metadata.items():
        if data.get("category") == "tv_episode" and data.get("series") == series_name:
            episodes.append({
                "video_id": video_id,
                "season": data.get("season") or 1,
                "episode": data.get("episode") or 1,
                "duration": data.get("duracion") or 0,
                "series_path": data.get("series_path"),
            })

    # Sort chronologically: by season, then by episode
    episodes.sort(key=lambda e: (e["season"], e["episode"]))
    return episodes


def get_commercials(metadata: dict = None) -> List[dict]:
    """
    Get all commercial videos from metadata.
    Returns list of dicts with video_id and duration.
    """
    if metadata is None:
        metadata = load_metadata()

    commercials = []
    for video_id, data in metadata.items():
        if data.get("category") == "commercial":
            commercials.append({
                "video_id": video_id,
                "duration": data.get("duracion") or 30,  # default 30s if unknown
            })

    return commercials


# ============================================================================
# SYSTEM VIDEO GENERATION
# ============================================================================

def ensure_system_videos_exist() -> None:
    """Ensure test pattern and sponsors placeholder videos exist."""
    SYSTEM_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    if not TEST_PATTERN_VIDEO.exists():
        logger.info("[SCHEDULER] Generating test pattern video...")
        generate_test_pattern_video()

    if not SPONSORS_PLACEHOLDER_VIDEO.exists():
        logger.info("[SCHEDULER] Generating sponsors placeholder video...")
        generate_sponsors_placeholder_video()


def generate_test_pattern_video() -> bool:
    """
    Generate SMPTE color bars test pattern video with 1kHz tone.
    Creates a 1-hour video for looping during 3am-4am.
    """
    try:
        SYSTEM_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        # Download SMPTE color bars image
        smpte_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/SMPTE_Color_Bars.svg/960px-SMPTE_Color_Bars.svg.png"
        smpte_image = SYSTEM_VIDEO_DIR / "smpte_bars.png"

        # Use wget or curl to download
        try:
            subprocess.run([
                "wget", "-q", "-O", str(smpte_image), smpte_url
            ], check=True, timeout=30)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Try curl as fallback
            subprocess.run([
                "curl", "-s", "-o", str(smpte_image), "-L", smpte_url
            ], check=True, timeout=30)

        if not smpte_image.exists():
            logger.error("[SCHEDULER] Failed to download SMPTE bars image")
            return False

        # Generate 1-hour test pattern video with 1kHz tone
        # Using ffmpeg to create video from image + generate tone
        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(smpte_image),
            "-f", "lavfi",
            "-i", "sine=frequency=1000:sample_rate=48000",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-t", "3600",  # 1 hour
            "-shortest",
            str(TEST_PATTERN_VIDEO)
        ], check=True, timeout=600)

        logger.info(f"[SCHEDULER] Test pattern video created: {TEST_PATTERN_VIDEO}")
        return True

    except Exception as e:
        logger.error(f"[SCHEDULER] Failed to generate test pattern video: {e}")
        return False


def generate_sponsors_placeholder_video() -> bool:
    """
    Generate a placeholder video with text "Your scheduled programming
    will resume after a word from our sponsors".
    """
    try:
        SYSTEM_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        # Create a 30-second video with text overlay on blue background
        text = "Your scheduled programming\\nwill resume after a word\\nfrom our sponsors"

        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x000088:s=960x540:d=30",
            "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", f"drawtext=text='{text}':fontsize=36:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:font=monospace",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-t", "30",
            "-pix_fmt", "yuv420p",
            str(SPONSORS_PLACEHOLDER_VIDEO)
        ], check=True, timeout=60)

        logger.info(f"[SCHEDULER] Sponsors placeholder video created: {SPONSORS_PLACEHOLDER_VIDEO}")
        return True

    except Exception as e:
        logger.error(f"[SCHEDULER] Failed to generate sponsors placeholder video: {e}")
        return False


# ============================================================================
# EPISODE CURSOR MANAGEMENT
# ============================================================================

def get_next_episode_for_channel(channel_id: str, series_name: str,
                                  cursors: dict = None, metadata: dict = None) -> Optional[dict]:
    """
    Get the next episode to play for a given channel and series.
    Advances cursor position and handles wrap-around.
    Returns episode dict or None if no episodes available.
    """
    # Track if we need to save cursors (when loaded internally)
    should_save_cursors = cursors is None

    if cursors is None:
        cursors = load_episode_cursors()
    if metadata is None:
        metadata = load_metadata()

    episodes = get_series_episodes(series_name, metadata)
    if not episodes:
        logger.warning(f"[SCHEDULER] No episodes found for series: {series_name}")
        return None

    # Get current cursor position for this channel+series
    channel_cursors = cursors.get(channel_id, {})
    series_cursor = channel_cursors.get(series_name, {
        "season": 1,
        "episode": 0,  # Will advance to 1 on first call
        "last_index": -1
    })

    current_index = series_cursor.get("last_index", -1)
    next_index = (current_index + 1) % len(episodes)

    next_episode = episodes[next_index]

    # Update cursor
    if channel_id not in cursors:
        cursors[channel_id] = {}
    cursors[channel_id][series_name] = {
        "season": next_episode["season"],
        "episode": next_episode["episode"],
        "last_index": next_index,
        "updated_at": datetime.now().isoformat()
    }

    # Save cursors if we loaded them internally
    if should_save_cursors:
        save_episode_cursors(cursors)

    return next_episode


def peek_next_episode_for_channel(channel_id: str, series_name: str,
                                   offset: int = 0,
                                   cursors: dict = None, metadata: dict = None) -> Optional[dict]:
    """
    Peek at the next episode without advancing cursor.
    offset=0 means the very next episode, offset=1 means the one after, etc.
    """
    if cursors is None:
        cursors = load_episode_cursors()
    if metadata is None:
        metadata = load_metadata()

    episodes = get_series_episodes(series_name, metadata)
    if not episodes:
        return None

    channel_cursors = cursors.get(channel_id, {})
    series_cursor = channel_cursors.get(series_name, {"last_index": -1})

    current_index = series_cursor.get("last_index", -1)
    peek_index = (current_index + 1 + offset) % len(episodes)

    return episodes[peek_index]


# ============================================================================
# WEEKLY SCHEDULE GENERATOR
# ============================================================================

def select_back_to_back_count() -> int:
    """Select number of episodes to play back-to-back based on probability weights."""
    total = sum(BACK_TO_BACK_WEIGHTS.values())
    roll = random.randint(1, total)

    cumulative = 0
    for count, weight in sorted(BACK_TO_BACK_WEIGHTS.items()):
        cumulative += weight
        if roll <= cumulative:
            return count

    return 2  # Default fallback


def get_eligible_series_for_time(time_of_day: str, channel_series: List[str],
                                  series_data: dict) -> List[str]:
    """
    Get series eligible to play during a specific time of day.
    Includes series with matching time_of_day or "any".
    """
    eligible = []
    for series_name in channel_series:
        series_info = series_data.get(series_name, {})
        series_time = series_info.get("time_of_day", "any")

        if series_time == "any" or series_time == time_of_day:
            eligible.append(series_name)

    return eligible


def generate_weekly_schedule(channel_id: str = None) -> dict:
    """
    Generate a new weekly schedule.
    Assigns series to time-of-day slots for each channel with series_filter.

    Args:
        channel_id: Optional. If provided, only regenerates for this specific channel,
                   preserving the existing schedule for other channels.
    """
    if channel_id:
        logger.info(f"[SCHEDULER] Generating weekly schedule for channel: {channel_id}")
    else:
        logger.info("[SCHEDULER] Generating weekly schedule for all channels...")

    canales = load_canales()
    series_data = load_series()

    now = datetime.now()
    # Find the most recent Sunday
    days_since_sunday = now.weekday() + 1  # Monday=0, Sunday=6, so +1
    if days_since_sunday == 7:
        days_since_sunday = 0
    week_start = (now - timedelta(days=days_since_sunday)).date()

    # If regenerating for a single channel, load existing schedule first
    if channel_id:
        schedule = load_weekly_schedule()
        if not schedule:
            schedule = {
                "generated_at": now.isoformat(),
                "week_start": str(week_start),
                "channels": {}
            }
        # Update timestamp
        schedule["generated_at"] = now.isoformat()
        schedule["week_start"] = str(week_start)
        channels_to_process = {channel_id: canales.get(channel_id, {})}
    else:
        schedule = {
            "generated_at": now.isoformat(),
            "week_start": str(week_start),
            "channels": {}
        }
        channels_to_process = canales

    for cid, config in channels_to_process.items():
        series_filter = config.get("series_filter", [])
        if not series_filter:
            continue  # Skip non-series channels

        channel_schedule = {"time_slots": {}}

        for time_of_day, slot_count in TIME_OF_DAY_SLOTS.items():
            eligible = get_eligible_series_for_time(time_of_day, series_filter, series_data)

            if not eligible:
                # No eligible series for this time - will show test pattern
                channel_schedule["time_slots"][time_of_day] = ["__test_pattern__"] * slot_count
                logger.warning(f"[SCHEDULER] No eligible series for {cid} during {time_of_day}")
                continue

            # Fill slots with series using back-to-back probability
            slots = []
            while len(slots) < slot_count:
                series = random.choice(eligible)
                back_to_back = select_back_to_back_count()

                # Add series for back_to_back slots (or remaining slots, whichever is less)
                for _ in range(min(back_to_back, slot_count - len(slots))):
                    slots.append(series)

            channel_schedule["time_slots"][time_of_day] = slots[:slot_count]

        schedule["channels"][cid] = channel_schedule

    save_weekly_schedule(schedule)

    if channel_id:
        logger.info(f"[SCHEDULER] Weekly schedule regenerated for channel: {channel_id}")
    else:
        logger.info(f"[SCHEDULER] Weekly schedule generated for {len(schedule['channels'])} channels")

    return schedule


# ============================================================================
# DAILY SCHEDULE GENERATOR
# ============================================================================

def get_time_of_day_for_hour(hour: int) -> str:
    """Determine which time-of-day period an hour falls into."""
    # Handle wrap-around for night (9pm-3am spans midnight)
    if hour >= 21 or hour < 3:
        return "night"
    elif 4 <= hour < 7:
        return "early_morning"
    elif 7 <= hour < 12:
        return "late_morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"  # 3am falls into test pattern, but just in case


def get_slot_index_for_time(hour: int, minute: int) -> Tuple[str, int]:
    """
    Get the time-of-day period and slot index for a given time.
    Returns (time_of_day, slot_index_within_period).
    """
    time_of_day = get_time_of_day_for_hour(hour)

    # Calculate slot index within the time-of-day period
    period_start_hour, _ = TIME_OF_DAY_RANGES.get(time_of_day, (4, 7))

    # Handle night period wrap-around
    if time_of_day == "night":
        if hour >= 21:
            hours_into_period = hour - 21
        else:
            hours_into_period = (hour + 24 - 21)  # e.g., 1am = 4 hours into night
    else:
        hours_into_period = hour - period_start_hour

    # Each hour has 2 slots (30 min each)
    slot_index = hours_into_period * 2 + (1 if minute >= 30 else 0)

    return time_of_day, slot_index


def build_commercial_sequence(duration_needed: float, commercials: List[dict]) -> List[dict]:
    """
    Build a sequence of commercials to fill the specified duration.
    Loops commercials if not enough unique ones available.
    Returns list of dicts with video_id, duration, and start_offset.
    """
    if not commercials:
        # No commercials available - use sponsors placeholder
        sequence = []
        remaining = duration_needed
        while remaining > 0:
            seq_entry = {
                "type": "sponsors_placeholder",
                "video_id": "__sponsors_placeholder__",
                "duration": min(30, remaining),  # 30 second placeholder video
            }
            sequence.append(seq_entry)
            remaining -= 30
        return sequence

    sequence = []
    remaining = duration_needed
    commercial_pool = commercials.copy()
    random.shuffle(commercial_pool)
    pool_index = 0

    while remaining > 0:
        if pool_index >= len(commercial_pool):
            # Loop back to start
            random.shuffle(commercial_pool)
            pool_index = 0

        comm = commercial_pool[pool_index]
        comm_duration = comm.get("duration", 30)

        sequence.append({
            "type": "commercial",
            "video_id": comm["video_id"],
            "duration": min(comm_duration, remaining),
        })

        remaining -= comm_duration
        pool_index += 1

    return sequence


def calculate_block_structure(episode_duration: float, block_duration: float = BLOCK_DURATION_SEC) -> dict:
    """
    Calculate how an episode fits into 30-minute blocks.
    Returns a dict describing the block structure.
    """
    if episode_duration <= 0:
        return {"type": "skip", "blocks": 0}

    if episode_duration < VERY_SHORT_EPISODE_MAX:
        # Very short: 3 episodes per block
        return {"type": "very_short", "episodes_per_block": 3, "blocks": 1}
    elif episode_duration < SHORT_EPISODE_MAX:
        # Short: 2 episodes per block
        return {"type": "short", "episodes_per_block": 2, "blocks": 1}
    elif episode_duration < MEDIUM_EPISODE_MAX:
        # Medium: 1 episode per block with commercial padding
        return {"type": "medium", "episodes_per_block": 1, "blocks": 1}
    elif episode_duration < LONG_EPISODE_MAX:
        # Long: spans 2 blocks
        return {"type": "long", "episodes_per_block": 1, "blocks": 2}
    else:
        # Very long: spans 3+ blocks
        blocks_needed = int((episode_duration + block_duration - 1) // block_duration)
        return {"type": "very_long", "episodes_per_block": 1, "blocks": blocks_needed}


def generate_block_schedule(block_start_second: int,
                           episodes: List[dict],
                           commercials: List[dict],
                           block_duration: float = BLOCK_DURATION_SEC) -> List[dict]:
    """
    Generate second-by-second schedule entries for a 30-minute block.

    Block structure:
    - Commercial break 1 (start)
    - Episode content
    - Commercial break 2 (middle)
    - Episode content
    - Commercial break 3 (end)

    Returns list of schedule entries (ranges, not individual seconds).
    """
    entries = []

    # Calculate total episode content time
    total_episode_time = sum(ep.get("duration", 0) for ep in episodes)

    # Calculate commercial time (remaining time in block)
    total_commercial_time = max(0, block_duration - total_episode_time)
    commercial_break_duration = total_commercial_time / COMMERCIAL_BREAKS_PER_BLOCK

    current_second = block_start_second
    episode_index = 0

    # For each episode, we split it around the commercial breaks
    if len(episodes) == 1:
        # Single episode: split into two parts around middle commercial
        ep = episodes[0]
        ep_duration = ep.get("duration", 0)
        half_ep = ep_duration / 2

        # Commercial break 1 (start)
        comm_seq = build_commercial_sequence(commercial_break_duration, commercials)
        for comm in comm_seq:
            entries.append({
                "start": current_second,
                "end": current_second + comm["duration"],
                "type": comm["type"],
                "video_id": comm["video_id"],
                "base_timestamp": 0,
            })
            current_second += comm["duration"]

        # Episode part 1
        entries.append({
            "start": current_second,
            "end": current_second + half_ep,
            "type": "episode",
            "video_id": ep["video_id"],
            "series_path": ep.get("series_path"),
            "base_timestamp": 0,
        })
        current_second += half_ep

        # Commercial break 2 (middle)
        comm_seq = build_commercial_sequence(commercial_break_duration, commercials)
        for comm in comm_seq:
            entries.append({
                "start": current_second,
                "end": current_second + comm["duration"],
                "type": comm["type"],
                "video_id": comm["video_id"],
                "base_timestamp": 0,
            })
            current_second += comm["duration"]

        # Episode part 2
        entries.append({
            "start": current_second,
            "end": current_second + half_ep,
            "type": "episode",
            "video_id": ep["video_id"],
            "series_path": ep.get("series_path"),
            "base_timestamp": half_ep,
        })
        current_second += half_ep

        # Commercial break 3 (end)
        remaining_time = block_start_second + block_duration - current_second
        if remaining_time > 0:
            comm_seq = build_commercial_sequence(remaining_time, commercials)
            for comm in comm_seq:
                entries.append({
                    "start": current_second,
                    "end": current_second + comm["duration"],
                    "type": comm["type"],
                    "video_id": comm["video_id"],
                    "base_timestamp": 0,
                })
                current_second += comm["duration"]

    else:
        # Multiple episodes: distribute with commercials between
        per_episode_commercial = commercial_break_duration

        for i, ep in enumerate(episodes):
            # Commercial before each episode
            if per_episode_commercial > 0:
                comm_seq = build_commercial_sequence(per_episode_commercial, commercials)
                for comm in comm_seq:
                    entries.append({
                        "start": current_second,
                        "end": current_second + comm["duration"],
                        "type": comm["type"],
                        "video_id": comm["video_id"],
                        "base_timestamp": 0,
                    })
                    current_second += comm["duration"]

            # Episode
            ep_duration = ep.get("duration", 0)
            entries.append({
                "start": current_second,
                "end": current_second + ep_duration,
                "type": "episode",
                "video_id": ep["video_id"],
                "series_path": ep.get("series_path"),
                "base_timestamp": 0,
            })
            current_second += ep_duration

    return entries


def generate_daily_schedule(channel_id: str = None) -> dict:
    """
    Generate a new daily schedule.
    Creates second-by-second (actually range-based) mapping for each channel.
    Schedule runs from 4am today to 3am tomorrow, with test pattern 3am-4am.

    Args:
        channel_id: Optional. If provided, only regenerates for this specific channel,
                   preserving the existing schedule for other channels.
    """
    if channel_id:
        logger.info(f"[SCHEDULER] Generating daily schedule for channel: {channel_id}")
    else:
        logger.info("[SCHEDULER] Generating daily schedule for all channels...")

    ensure_system_videos_exist()

    canales = load_canales()
    weekly_schedule = load_weekly_schedule()
    metadata = load_metadata()
    cursors = load_episode_cursors()
    commercials = get_commercials(metadata)

    now = datetime.now()
    schedule_date = now.date()

    # Schedule validity
    # If before 3am, we're still on yesterday's schedule conceptually,
    # but we generate for today starting at 4am
    valid_from = datetime.combine(schedule_date, datetime.min.time().replace(hour=4))
    valid_until = datetime.combine(schedule_date + timedelta(days=1),
                                   datetime.min.time().replace(hour=3))

    # If regenerating for a single channel, load existing schedule first
    if channel_id:
        schedule = load_daily_schedule()
        if not schedule:
            schedule = {
                "generated_at": now.isoformat(),
                "schedule_date": str(schedule_date),
                "valid_from": valid_from.isoformat(),
                "valid_until": valid_until.isoformat(),
                "channels": {}
            }
        # Update timestamps
        schedule["generated_at"] = now.isoformat()
        schedule["schedule_date"] = str(schedule_date)
        schedule["valid_from"] = valid_from.isoformat()
        schedule["valid_until"] = valid_until.isoformat()
        channels_to_process = {channel_id: canales.get(channel_id, {})}
    else:
        schedule = {
            "generated_at": now.isoformat(),
            "schedule_date": str(schedule_date),
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "channels": {}
        }
        channels_to_process = canales

    for cid, config in channels_to_process.items():
        series_filter = config.get("series_filter", [])
        if not series_filter:
            continue

        channel_weekly = weekly_schedule.get("channels", {}).get(cid, {})
        if not channel_weekly:
            logger.warning(f"[SCHEDULER] No weekly schedule for channel {cid}")
            continue

        time_slots = channel_weekly.get("time_slots", {})
        channel_entries = []

        # Test pattern: 3am-4am (seconds 0-3599 of the day)
        # But actually our day starts at 3am, so:
        # Second 0 = 3:00:00am
        # Second 3600 = 4:00:00am (start of programming)
        channel_entries.append({
            "start": 0,
            "end": 3600,
            "type": "test_pattern",
            "video_id": "__test_pattern__",
        })

        # Process each 30-minute block from 4am to 3am
        # That's 46 blocks (4am to 2:30am = 22.5 hours = 45 blocks, plus we end at 3am)
        # Actually: 4am to 3am next day = 23 hours = 46 blocks

        current_second = 3600  # Start at 4am (3600 seconds into day starting at 3am)

        for block_num in range(46):  # 46 half-hour blocks from 4am to 3am
            block_start_second = 3600 + (block_num * BLOCK_DURATION_SEC)

            # Calculate what time this block represents
            total_seconds_from_midnight = (3 * 3600) + block_start_second  # 3am base
            block_hour = (total_seconds_from_midnight // 3600) % 24
            block_minute = (total_seconds_from_midnight % 3600) // 60

            time_of_day, slot_index = get_slot_index_for_time(block_hour, block_minute)

            # Get series assigned to this slot from weekly schedule
            slots_for_period = time_slots.get(time_of_day, [])
            if slot_index < len(slots_for_period):
                series_name = slots_for_period[slot_index]
            else:
                series_name = "__test_pattern__"

            if series_name == "__test_pattern__":
                # Show test pattern for this block
                channel_entries.append({
                    "start": block_start_second,
                    "end": block_start_second + BLOCK_DURATION_SEC,
                    "type": "test_pattern",
                    "video_id": "__test_pattern__",
                })
                continue

            # Get episodes for this block
            # First, peek at next episode to determine block structure
            next_ep = peek_next_episode_for_channel(cid, series_name, 0, cursors, metadata)

            if not next_ep:
                # No episodes - show test pattern
                channel_entries.append({
                    "start": block_start_second,
                    "end": block_start_second + BLOCK_DURATION_SEC,
                    "type": "test_pattern",
                    "video_id": "__test_pattern__",
                })
                continue

            ep_duration = next_ep.get("duration", 0)
            block_structure = calculate_block_structure(ep_duration)

            # Collect episodes for this block
            block_episodes = []
            episodes_needed = block_structure.get("episodes_per_block", 1)

            for i in range(episodes_needed):
                ep = get_next_episode_for_channel(cid, series_name, cursors, metadata)
                if ep:
                    block_episodes.append(ep)

            if not block_episodes:
                channel_entries.append({
                    "start": block_start_second,
                    "end": block_start_second + BLOCK_DURATION_SEC,
                    "type": "test_pattern",
                    "video_id": "__test_pattern__",
                })
                continue

            # Handle multi-block episodes
            if block_structure["blocks"] > 1:
                # For long episodes spanning multiple blocks, we generate entries
                # for all blocks the episode spans
                total_duration = block_episodes[0].get("duration", 0)
                blocks_to_span = block_structure["blocks"]

                # Generate entries for all blocks
                ep = block_episodes[0]
                time_per_block = total_duration / blocks_to_span

                for span_block in range(blocks_to_span):
                    span_start = block_start_second + (span_block * BLOCK_DURATION_SEC)
                    span_end = span_start + BLOCK_DURATION_SEC

                    # Commercial time per block
                    commercial_time = BLOCK_DURATION_SEC - time_per_block
                    commercial_per_break = commercial_time / COMMERCIAL_BREAKS_PER_BLOCK

                    block_entries = generate_block_schedule(
                        span_start,
                        [{"video_id": ep["video_id"],
                          "series_path": ep.get("series_path"),
                          "duration": time_per_block,
                          "_base_offset": span_block * time_per_block}],
                        commercials,
                        BLOCK_DURATION_SEC
                    )

                    # Adjust base_timestamp for spanning blocks
                    for entry in block_entries:
                        if entry["type"] == "episode":
                            entry["base_timestamp"] = span_block * time_per_block

                    channel_entries.extend(block_entries)

                # Skip the extra blocks we've already scheduled
                # (handled by block_num advancing normally)
                # Actually we need to skip blocks - let's track this
                # For simplicity, we'll let the loop continue and overwrite
                # TODO: Better handling of multi-block episodes
            else:
                # Single block - generate normally
                block_entries = generate_block_schedule(
                    block_start_second,
                    block_episodes,
                    commercials,
                    BLOCK_DURATION_SEC
                )
                channel_entries.extend(block_entries)

        schedule["channels"][cid] = channel_entries

    # Save cursors (they were modified during generation)
    save_episode_cursors(cursors)
    save_daily_schedule(schedule)

    if channel_id:
        logger.info(f"[SCHEDULER] Daily schedule regenerated for channel: {channel_id}")
    else:
        logger.info(f"[SCHEDULER] Daily schedule generated for {len(schedule['channels'])} channels")
    return schedule


# ============================================================================
# SCHEDULE LOOKUP
# ============================================================================

def get_scheduled_content(channel_id: str, timestamp: datetime = None) -> Optional[dict]:
    """
    Get the scheduled content for a channel at a specific timestamp.
    Returns dict with video_id, seek_to timestamp, type, etc.
    Falls back to test pattern if no schedule exists.
    """
    # Fallback response for when no schedule exists
    fallback = {
        "type": "test_pattern",
        "video_id": "__test_pattern__",
        "video_url": "/videos/system/test_pattern.mp4",
        "seek_to": 0,
    }

    if timestamp is None:
        timestamp = datetime.now()

    schedule = load_daily_schedule()
    if not schedule:
        return fallback

    channel_entries = schedule.get("channels", {}).get(channel_id, [])
    if not channel_entries:
        return fallback

    # Calculate second-of-day relative to 3am start
    # Our schedule day runs 3am to 3am
    hour = timestamp.hour
    minute = timestamp.minute
    second = timestamp.second

    if hour < 3:
        # Before 3am - this is the end of yesterday's schedule
        # But we treat it as late in today's schedule
        seconds_since_3am = ((24 - 3) * 3600) + (hour * 3600) + (minute * 60) + second
    else:
        seconds_since_3am = ((hour - 3) * 3600) + (minute * 60) + second

    # Binary search for the entry containing this second
    for entry in channel_entries:
        if entry["start"] <= seconds_since_3am < entry["end"]:
            # Found the entry
            offset_into_entry = seconds_since_3am - entry["start"]
            base_timestamp = entry.get("base_timestamp", 0)

            result = {
                "type": entry["type"],
                "video_id": entry["video_id"],
                "seek_to": base_timestamp + offset_into_entry,
            }

            if entry["type"] == "test_pattern":
                result["video_url"] = "/videos/system/test_pattern.mp4"
            elif entry["type"] == "sponsors_placeholder":
                result["video_url"] = "/videos/system/sponsors_placeholder.mp4"
            elif entry["type"] == "commercial":
                result["video_url"] = f"/videos/commercials/{entry['video_id']}.mp4"
            elif entry["type"] == "episode":
                series_path = entry.get("series_path")
                if series_path:
                    result["video_url"] = f"/videos/{series_path}.mp4"
                else:
                    result["video_url"] = f"/videos/{entry['video_id']}.mp4"

            return result

    # No entry found - return test pattern as fallback
    return {
        "type": "test_pattern",
        "video_id": "__test_pattern__",
        "video_url": "/videos/system/test_pattern.mp4",
        "seek_to": 0,
    }


def is_broadcast_channel(channel_id: str) -> bool:
    """Check if a channel is configured for broadcast scheduling."""
    canales = load_canales()
    config = canales.get(channel_id, {})
    return bool(config.get("series_filter"))


# ============================================================================
# SCHEDULE CHECKS AND BACKGROUND LOOP
# ============================================================================

def needs_weekly_regeneration(meta: dict, now: datetime) -> bool:
    """Check if weekly schedule needs to be regenerated."""
    # Check if we have a weekly schedule at all
    if not WEEKLY_SCHEDULE_FILE.exists():
        logger.info("[SCHEDULER] No weekly schedule exists - need to generate")
        return True

    last_generated = meta.get("weekly_generated")
    if not last_generated:
        return True

    try:
        last_dt = datetime.fromisoformat(last_generated)
    except (ValueError, TypeError):
        return True

    # Check if it's Sunday and past 2:30am
    if now.weekday() != 6:  # Not Sunday
        return False

    if now.hour < WEEKLY_SCHEDULE_HOUR:
        return False
    if now.hour == WEEKLY_SCHEDULE_HOUR and now.minute < WEEKLY_SCHEDULE_MINUTE:
        return False

    # It's Sunday past 2:30am - check if we've already generated today
    if last_dt.date() == now.date() and last_dt.hour >= WEEKLY_SCHEDULE_HOUR:
        return False

    return True


def needs_daily_regeneration(meta: dict, now: datetime) -> bool:
    """Check if daily schedule needs to be regenerated."""
    # Check if we have a daily schedule at all
    if not DAILY_SCHEDULE_FILE.exists():
        logger.info("[SCHEDULER] No daily schedule exists - need to generate")
        return True

    last_generated = meta.get("daily_generated")
    if not last_generated:
        return True

    try:
        last_dt = datetime.fromisoformat(last_generated)
    except (ValueError, TypeError):
        return True

    # Check if it's past 3am
    if now.hour < DAILY_SCHEDULE_HOUR:
        # Before 3am - check if yesterday's schedule is still valid
        yesterday = now.date() - timedelta(days=1)
        if last_dt.date() == yesterday and last_dt.hour >= DAILY_SCHEDULE_HOUR:
            return False
        return True

    if now.hour == DAILY_SCHEDULE_HOUR and now.minute < DAILY_SCHEDULE_MINUTE:
        # Not quite 3am yet
        if last_dt.date() == now.date() - timedelta(days=1):
            return False
        return True

    # Past 3am - check if we've already generated today
    if last_dt.date() == now.date() and last_dt.hour >= DAILY_SCHEDULE_HOUR:
        return False

    return True


def check_and_generate_schedules() -> None:
    """Check if schedules need regeneration and generate if needed."""
    meta = load_schedule_meta()
    now = datetime.now()

    schedules_updated = False

    # Check weekly schedule
    if needs_weekly_regeneration(meta, now):
        try:
            generate_weekly_schedule()
            meta["weekly_generated"] = now.isoformat()
            schedules_updated = True
            logger.info("[SCHEDULER] Weekly schedule regenerated")
        except Exception as e:
            logger.error(f"[SCHEDULER] Failed to generate weekly schedule: {e}")

    # Check daily schedule
    if needs_daily_regeneration(meta, now):
        try:
            generate_daily_schedule()
            meta["daily_generated"] = now.isoformat()
            schedules_updated = True
            logger.info("[SCHEDULER] Daily schedule regenerated")
        except Exception as e:
            logger.error(f"[SCHEDULER] Failed to generate daily schedule: {e}")

    if schedules_updated:
        save_schedule_meta(meta)


# Background scheduler thread
_scheduler_thread = None
_scheduler_running = False


def _scheduler_loop():
    """Background loop that checks for schedule updates every 5 seconds."""
    global _scheduler_running

    logger.info("[SCHEDULER] Background scheduler loop started")

    while _scheduler_running:
        try:
            check_and_generate_schedules()
        except Exception as e:
            logger.error(f"[SCHEDULER] Error in scheduler loop: {e}")

        time.sleep(SCHEDULER_CHECK_INTERVAL)

    logger.info("[SCHEDULER] Background scheduler loop stopped")


def start_scheduler():
    """Start the background scheduler thread."""
    global _scheduler_thread, _scheduler_running

    if _scheduler_running:
        logger.warning("[SCHEDULER] Scheduler already running")
        return

    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()

    logger.info("[SCHEDULER] Background scheduler started")


def stop_scheduler():
    """Stop the background scheduler thread."""
    global _scheduler_running

    _scheduler_running = False
    logger.info("[SCHEDULER] Background scheduler stop requested")


# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_scheduler():
    """Initialize the scheduler module."""
    logger.info("[SCHEDULER] Initializing scheduler module...")

    # Ensure content directory exists
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure system videos exist
    ensure_system_videos_exist()

    # Check if we need to generate schedules immediately
    check_and_generate_schedules()

    # Warm the cache for fast channel switching
    # (if schedule was just generated, cache is already warm via save_daily_schedule)
    warm_daily_schedule_cache()

    # Start background loop
    start_scheduler()

    logger.info("[SCHEDULER] Scheduler module initialized")
