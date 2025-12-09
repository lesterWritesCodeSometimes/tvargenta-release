# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Broadcast TV Scheduler

This module implements broadcast-style TV scheduling for channels with series_filter.
It provides:
- Weekly schedule generation (regenerates every Sunday at midnight)
- Daily pre-computed schedule with video segments (regenerates at 3am or on demand)
- Commercial break insertion for series with short episodes
- Time-of-day based series programming
"""

import bisect
import json
import logging
import math
import os
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from settings import (
    SCHEDULE_FILE, DAILY_SCHEDULE_FILE, TIME_SLOTS, VALID_TIME_OF_DAY,
    TEST_PATTERN_FILE, HANG_TIGHT_FILE, ADS_DIR,
    METADATA_FILE, SERIES_FILE, CANALES_FILE, VIDEO_DIR,
)

logger = logging.getLogger("tvargenta")

# Daily schedule configuration
DAILY_REGEN_HOUR = 3  # Regenerate daily schedule at 3 AM
SCHEDULER_CHECK_INTERVAL_SEC = 60  # Check for regeneration every minute

# Commercial break configuration
BLOCK_DURATION_SEC = 30 * 60  # 30 minutes
MAX_COMMERCIAL_BREAK_SEC = 4 * 60  # 4 minutes max per break
MIN_COMMERCIAL_BREAKS = 3  # Minimum number of breaks per block

# Schedule version - increment when schedule logic changes to trigger regeneration
SCHEDULE_VERSION = 3

# Contiguous block randomization for series scheduling
# Probability distribution for max consecutive 30-min blocks per series per time slot
CONTIGUOUS_BLOCK_WEIGHTS = [
    (2, 80),   # 2 blocks (60 min): 80% chance
    (3, 10),   # 3 blocks (90 min): 10% chance
    (4, 6),    # 4 blocks (120 min): 6% chance
    (5, 4),    # 5 blocks (150 min): 4% chance
]


def _roll_max_contiguous_blocks() -> int:
    """
    Roll a random max contiguous blocks value based on weighted probability.

    Returns:
        Number of max consecutive 30-min blocks (2, 3, 4, or 5)
    """
    choices = [blocks for blocks, _ in CONTIGUOUS_BLOCK_WEIGHTS]
    weights = [weight for _, weight in CONTIGUOUS_BLOCK_WEIGHTS]
    return random.choices(choices, weights=weights, k=1)[0]


class BroadcastScheduler:
    """
    Manages broadcast-style TV scheduling for series channels.

    The scheduler:
    1. Generates weekly schedules on Sunday midnight (defines time slots and series assignments)
    2. Pre-computes daily schedule with video segments (regenerates at 3 AM or on new day)
    3. Uses binary search for O(log n) content lookup at any timestamp
    4. Handles commercial breaks for short episodes
    5. Shows test pattern when no eligible content

    Daily schedule is written to disk for debugging inspection.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._schedule: Dict = {}  # Persisted weekly schedule
        self._daily_schedule: Dict = {}  # Pre-computed daily segments per channel
        self._daily_schedule_date: str = ""  # Date string for current daily schedule
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._ads_cache: List[Dict] = []  # Cached list of available ads
        self._ads_cache_time: float = 0
        self._metadata_cache: Dict = {}  # In-memory metadata cache
        self._metadata_loaded = False

    def start(self):
        """Start the background scheduler thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("[SCHEDULER] Broadcast scheduler started")

    def stop(self):
        """Stop the background scheduler thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[SCHEDULER] Broadcast scheduler stopped")

    def wait_ready(self, timeout: float = 30) -> bool:
        """Wait for the scheduler to be ready with initial buffer."""
        return self._ready.wait(timeout=timeout)

    def is_ready(self) -> bool:
        """Check if scheduler has populated the buffer."""
        return self._ready.is_set()

    def get_schedule_at(self, channel_name: str, timestamp: Optional[int] = None) -> Optional[Dict]:
        """
        Get the scheduled content for a channel at a specific timestamp.

        Uses binary search on pre-computed daily schedule segments.

        Args:
            channel_name: Name of the channel
            timestamp: Unix timestamp (defaults to now)

        Returns:
            Dict with content info or None if not a broadcast channel
        """
        if timestamp is None:
            timestamp = int(time.time())

        with self._lock:
            channel_segments = self._daily_schedule.get(channel_name, {}).get("segments", [])

        if not channel_segments:
            return self._test_pattern_entry()

        # Binary search to find the segment containing this timestamp
        # We search for the rightmost segment where start <= timestamp
        starts = [seg["start"] for seg in channel_segments]
        idx = bisect.bisect_right(starts, timestamp) - 1

        if idx < 0:
            # Before first segment
            return self._test_pattern_entry()

        segment = channel_segments[idx]

        # Check if timestamp is within this segment
        if timestamp >= segment["end"]:
            # After this segment ends (gap or end of day)
            return self._test_pattern_entry()

        # Calculate seek position within the content
        elapsed = timestamp - segment["start"]
        seek_time = segment.get("seek_start", 0) + elapsed

        # Return content info with calculated seek time
        return {
            "content_type": segment["content_type"],
            "series": segment.get("series", ""),
            "season": segment.get("season", 0),
            "episode": segment.get("episode", 0),
            "timestamp": seek_time,
            "video_path": segment.get("video_path", ""),
            "duration": segment.get("duration", 0),
            "video_id": segment.get("video_id", "")
        }

    def _scheduler_loop(self):
        """Main scheduler loop - checks for schedule regeneration periodically."""
        # Load weekly schedule
        self._load_or_generate_schedule()

        # Generate or load daily schedule
        self._load_or_generate_daily_schedule()

        self._ready.set()
        logger.info("[SCHEDULER] Daily schedule ready")

        while self._running:
            try:
                # Check for weekly schedule regeneration (Sunday midnight)
                self._check_schedule_regeneration()

                # Check for daily schedule regeneration (3 AM or new day)
                self._check_daily_schedule_regeneration()

                # Sleep until next check
                time.sleep(SCHEDULER_CHECK_INTERVAL_SEC)

            except Exception as e:
                logger.error(f"[SCHEDULER] Error in scheduler loop: {e}", exc_info=True)
                time.sleep(SCHEDULER_CHECK_INTERVAL_SEC)

    def _load_or_generate_schedule(self):
        """Load existing schedule or generate a new one."""
        schedule_path = Path(SCHEDULE_FILE)

        if schedule_path.exists():
            try:
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    self._schedule = json.load(f)

                # Check if we need to regenerate (version mismatch or Sunday has passed)
                schedule_version = self._schedule.get("version", 1)
                if schedule_version < SCHEDULE_VERSION:
                    logger.info(f"[SCHEDULER] Schedule version {schedule_version} < {SCHEDULE_VERSION}, regenerating")
                    self._generate_weekly_schedule()
                    return

                week_start = self._schedule.get("week_start", "")
                if week_start:
                    ws_dt = datetime.fromisoformat(week_start)
                    last_sunday = self._get_last_sunday_midnight()
                    if last_sunday > ws_dt:
                        logger.info("[SCHEDULER] New week detected, regenerating schedule")
                        self._generate_weekly_schedule()
                    else:
                        logger.info(f"[SCHEDULER] Loaded existing schedule v{schedule_version} from {week_start}")
                else:
                    self._generate_weekly_schedule()
            except Exception as e:
                logger.error(f"[SCHEDULER] Error loading schedule: {e}")
                self._generate_weekly_schedule()
        else:
            self._generate_weekly_schedule()

    def _check_schedule_regeneration(self):
        """Check if we need to regenerate the schedule (Sunday midnight)."""
        now = datetime.now()

        # Check if it's Sunday and past midnight
        if now.weekday() == 6:  # Sunday
            week_start = self._schedule.get("week_start", "")
            if week_start:
                ws_dt = datetime.fromisoformat(week_start)
                # If the schedule is from before this Sunday
                this_sunday = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if ws_dt < this_sunday:
                    logger.info("[SCHEDULER] Sunday midnight reached, regenerating weekly schedule")
                    self._generate_weekly_schedule()
                    # Also regenerate daily schedule after weekly regeneration
                    self._generate_daily_schedule()

    def _load_or_generate_daily_schedule(self):
        """Load existing daily schedule or generate a new one."""
        today = datetime.now().strftime("%Y-%m-%d")
        schedule_path = Path(DAILY_SCHEDULE_FILE)

        if schedule_path.exists():
            try:
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    daily_data = json.load(f)

                schedule_date = daily_data.get("date", "")
                if schedule_date == today:
                    self._daily_schedule = daily_data.get("channels", {})
                    self._daily_schedule_date = today
                    logger.info(f"[SCHEDULER] Loaded existing daily schedule for {today}")
                    return
                else:
                    logger.info(f"[SCHEDULER] Daily schedule is for {schedule_date}, need {today}")
            except Exception as e:
                logger.error(f"[SCHEDULER] Error loading daily schedule: {e}")

        # Generate new daily schedule
        self._generate_daily_schedule()

    def _check_daily_schedule_regeneration(self):
        """Check if we need to regenerate the daily schedule."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # Regenerate if date changed or at 3 AM
        if self._daily_schedule_date != today:
            logger.info(f"[SCHEDULER] New day detected ({today}), regenerating daily schedule")
            self._generate_daily_schedule()
        elif now.hour == DAILY_REGEN_HOUR and now.minute < 2:
            # Regenerate at 3 AM (within first 2 minutes to catch it with 60s interval)
            # Check if we already regenerated today at 3 AM
            if self._daily_schedule_date == today:
                # Check if schedule was generated before 3 AM
                generated_at = self._daily_schedule.get("_generated_at", "")
                if generated_at:
                    gen_time = datetime.fromisoformat(generated_at)
                    if gen_time.hour < DAILY_REGEN_HOUR:
                        logger.info("[SCHEDULER] 3 AM regeneration triggered")
                        self._generate_daily_schedule()

    def _generate_daily_schedule(self):
        """Generate pre-computed segments for all broadcast channels for today."""
        logger.info("[SCHEDULER] Generating daily schedule...")

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_str = today.strftime("%Y-%m-%d")
        day_start_ts = int(today.timestamp())
        day_end_ts = day_start_ts + 86400  # 24 hours

        channels = self._load_channels()
        daily_schedule = {}

        for channel in channels:
            channel_name = channel.get("nombre", "")
            series_filter = channel.get("series_filter", [])

            if not series_filter:
                continue  # Not a broadcast channel

            segments = self._generate_channel_daily_segments(
                channel_name, day_start_ts, day_end_ts
            )
            daily_schedule[channel_name] = {
                "segments": segments,
                "_generated_at": datetime.now().isoformat()
            }

        # Update in-memory state
        with self._lock:
            self._daily_schedule = daily_schedule
            self._daily_schedule_date = today_str

        # Save to file for debugging
        self._save_daily_schedule(today_str, daily_schedule)

        total_segments = sum(len(ch.get("segments", [])) for ch in daily_schedule.values())
        logger.info(f"[SCHEDULER] Generated daily schedule: {len(daily_schedule)} channels, {total_segments} segments")

    def _generate_channel_daily_segments(
        self,
        channel_name: str,
        day_start_ts: int,
        day_end_ts: int
    ) -> List[Dict]:
        """
        Generate all segments for a channel for the day.

        Returns a list of segments, each containing:
        {
            "start": epoch_timestamp,
            "end": epoch_timestamp,
            "content_type": "episode" | "ad" | "test_pattern",
            "video_path": "path/to/video.mp4",
            "seek_start": seconds_into_video,
            "series": "Series Name",
            "season": 1,
            "episode": 5,
            "duration": total_video_duration,
            "video_id": "video_id"
        }
        """
        channel_schedule = self._schedule.get("channels", {}).get(channel_name)
        if not channel_schedule:
            return []

        daily_slots = channel_schedule.get("daily_slots", [])
        if not daily_slots:
            return []

        metadata = self._load_metadata()
        segments = []
        current_ts = day_start_ts

        # Process each second of the day by iterating through time slots
        while current_ts < day_end_ts:
            dt = datetime.fromtimestamp(current_ts)
            hour = dt.hour

            # Handle night slot crossing midnight
            effective_hour = hour
            if hour < 4:
                effective_hour = hour + 24

            # Find active slot for this time
            active_slot = None
            for slot in daily_slots:
                start_h = slot["start_hour"]
                end_h = slot["end_hour"]
                if start_h <= effective_hour < end_h:
                    active_slot = slot
                    break

            if not active_slot:
                # No content for this time, skip to next hour
                next_hour_dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                current_ts = int(next_hour_dt.timestamp())
                continue

            # Calculate slot boundaries for today
            slot_start_hour = active_slot["start_hour"]
            slot_end_hour = active_slot["end_hour"]

            # Handle night slot crossing midnight
            if slot_start_hour >= 24:
                slot_start_dt = dt.replace(hour=slot_start_hour - 24, minute=0, second=0, microsecond=0)
            elif slot_start_hour > 21 and hour < 4:
                # We're in early morning part of night slot
                slot_start_dt = (dt - timedelta(days=1)).replace(hour=slot_start_hour, minute=0, second=0, microsecond=0)
            else:
                slot_start_dt = dt.replace(hour=slot_start_hour, minute=0, second=0, microsecond=0)

            if slot_end_hour > 24:
                slot_end_dt = (dt + timedelta(days=1)).replace(hour=slot_end_hour - 24, minute=0, second=0, microsecond=0)
            elif slot_end_hour <= slot_start_hour and hour >= slot_start_hour:
                slot_end_dt = (dt + timedelta(days=1)).replace(hour=slot_end_hour, minute=0, second=0, microsecond=0)
            else:
                slot_end_dt = dt.replace(hour=min(slot_end_hour, 24) % 24, minute=0, second=0, microsecond=0)

            slot_start_ts = int(slot_start_dt.timestamp())
            slot_end_ts = int(slot_end_dt.timestamp())

            # Clamp to day boundaries
            slot_start_ts = max(slot_start_ts, day_start_ts)
            slot_end_ts = min(slot_end_ts, day_end_ts)

            # Generate segments for this slot
            slot_segments = self._generate_slot_segments(
                channel_name, active_slot, slot_start_ts, slot_end_ts, metadata
            )
            segments.extend(slot_segments)

            # Move to end of this slot
            current_ts = slot_end_ts

        return segments

    def _generate_slot_segments(
        self,
        channel_name: str,
        slot: Dict,
        slot_start_ts: int,
        slot_end_ts: int,
        metadata: Dict
    ) -> List[Dict]:
        """Generate segments for a single time slot."""
        segments = []
        series_blocks = slot.get("series_blocks", [])

        if not series_blocks:
            return segments

        slot_duration = slot_end_ts - slot_start_ts

        for block in series_blocks:
            series_name = block["series"]
            block_start_minute = block["start_minute"]
            block_duration_minutes = block["duration_minutes"]
            use_commercial_blocks = block.get("use_commercial_blocks", False)

            # Calculate block time boundaries
            block_start_ts = slot_start_ts + (block_start_minute * 60)
            block_end_ts = block_start_ts + (block_duration_minutes * 60)

            # Clamp to slot boundaries
            block_start_ts = max(block_start_ts, slot_start_ts)
            block_end_ts = min(block_end_ts, slot_end_ts)

            if block_start_ts >= block_end_ts:
                continue

            # Generate segments for this series block
            block_segments = self._generate_series_block_segments(
                channel_name, series_name, block_start_ts, block_end_ts,
                use_commercial_blocks, metadata
            )
            segments.extend(block_segments)

        return segments

    def _generate_series_block_segments(
        self,
        channel_name: str,
        series_name: str,
        block_start_ts: int,
        block_end_ts: int,
        use_commercial_blocks: bool,
        metadata: Dict
    ) -> List[Dict]:
        """Generate segments for a series within a time block."""
        segments = []
        episodes = self._get_series_episodes(series_name, metadata)

        if not episodes:
            return segments

        cursor = self._get_episode_cursor(channel_name, series_name)
        episode_index = self._cursor_to_index(cursor, episodes)

        current_ts = block_start_ts

        while current_ts < block_end_ts:
            episode = episodes[episode_index % len(episodes)]
            video_id = episode.get("video_id", "")
            episode_duration = self._get_episode_duration(video_id, episode)

            if use_commercial_blocks and episode_duration < BLOCK_DURATION_SEC:
                # Short episode with commercial breaks
                segs = self._generate_episode_with_ads_segments(
                    episode, current_ts, block_end_ts, episode_duration
                )
                segments.extend(segs)
                # Each episode block is 30 minutes
                current_ts += BLOCK_DURATION_SEC
            else:
                # Long episode, no commercials
                remaining_time = block_end_ts - current_ts
                segment_duration = min(episode_duration, remaining_time)

                segments.append({
                    "start": current_ts,
                    "end": current_ts + int(segment_duration),
                    "content_type": "episode",
                    "video_path": f"{episode.get('series_path', '')}.mp4",
                    "seek_start": 0,
                    "series": series_name,
                    "season": episode.get("season", 1),
                    "episode": episode.get("episode", 1),
                    "duration": episode_duration,
                    "video_id": video_id
                })
                current_ts += int(segment_duration)

            episode_index += 1

        return segments

    def _generate_episode_with_ads_segments(
        self,
        episode: Dict,
        block_start_ts: int,
        block_end_ts: int,
        episode_duration: float
    ) -> List[Dict]:
        """Generate segments for an episode with commercial breaks."""
        segments = []
        video_id = episode.get("video_id", "")
        series_name = episode.get("series", "")

        # Calculate commercial break structure
        commercial_time = BLOCK_DURATION_SEC - episode_duration
        num_breaks = max(MIN_COMMERCIAL_BREAKS, int(commercial_time / MAX_COMMERCIAL_BREAK_SEC) + 1)
        break_duration = commercial_time / num_breaks
        num_segments = num_breaks + 1
        segment_duration = episode_duration / num_segments

        current_ts = block_start_ts
        episode_position = 0

        for i in range(num_segments):
            # Episode segment
            seg_duration = min(segment_duration, episode_duration - episode_position)
            if seg_duration > 0 and current_ts < block_end_ts:
                segments.append({
                    "start": current_ts,
                    "end": current_ts + int(seg_duration),
                    "content_type": "episode",
                    "video_path": f"{episode.get('series_path', '')}.mp4",
                    "seek_start": episode_position,
                    "series": series_name,
                    "season": episode.get("season", 1),
                    "episode": episode.get("episode", 1),
                    "duration": episode_duration,
                    "video_id": video_id
                })
                current_ts += int(seg_duration)
                episode_position += seg_duration

            # Commercial break (except after last segment)
            if i < num_breaks and current_ts < block_end_ts:
                ad_segments = self._generate_ad_break_segments(
                    current_ts, break_duration, block_end_ts
                )
                segments.extend(ad_segments)
                current_ts += int(break_duration)

        return segments

    def _generate_ad_break_segments(
        self,
        break_start_ts: int,
        break_duration: float,
        max_end_ts: int
    ) -> List[Dict]:
        """Generate segments for a commercial break."""
        segments = []
        ads = self._load_ads()

        if not ads:
            # No ads available - use hang tight
            segments.append({
                "start": break_start_ts,
                "end": min(break_start_ts + int(break_duration), max_end_ts),
                "content_type": "hang_tight",
                "video_path": str(HANG_TIGHT_FILE),
                "seek_start": 0,
                "duration": 0
            })
            return segments

        current_ts = break_start_ts
        break_end_ts = min(break_start_ts + int(break_duration), max_end_ts)
        ad_index = 0

        while current_ts < break_end_ts and ad_index < len(ads):
            ad = ads[ad_index % len(ads)]
            ad_duration = ad.get("duracion", 30) or 30
            remaining = break_end_ts - current_ts

            if remaining <= 0:
                break

            seg_duration = min(ad_duration, remaining)
            segments.append({
                "start": current_ts,
                "end": current_ts + int(seg_duration),
                "content_type": "ad",
                "video_path": ad.get("path", ""),
                "seek_start": 0,
                "duration": ad_duration,
                "video_id": ad.get("video_id", "")
            })
            current_ts += int(seg_duration)
            ad_index += 1

        # Fill remaining time with hang_tight if we ran out of unique ads
        if current_ts < break_end_ts:
            segments.append({
                "start": current_ts,
                "end": break_end_ts,
                "content_type": "hang_tight",
                "video_path": str(HANG_TIGHT_FILE),
                "seek_start": 0,
                "duration": 0
            })

        return segments

    def _save_daily_schedule(self, date_str: str, schedule: Dict):
        """Save daily schedule to disk for debugging."""
        try:
            schedule_path = Path(DAILY_SCHEDULE_FILE)
            schedule_path.parent.mkdir(parents=True, exist_ok=True)

            output = {
                "date": date_str,
                "generated_at": datetime.now().isoformat(),
                "channels": schedule
            }

            tmp_path = schedule_path.with_suffix(".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, schedule_path)

            logger.info(f"[SCHEDULER] Saved daily schedule to {schedule_path}")
        except Exception as e:
            logger.error(f"[SCHEDULER] Error saving daily schedule: {e}")

    def _get_last_sunday_midnight(self) -> datetime:
        """Get the datetime of the most recent Sunday at midnight."""
        now = datetime.now()
        days_since_sunday = (now.weekday() + 1) % 7
        last_sunday = now - timedelta(days=days_since_sunday)
        return last_sunday.replace(hour=0, minute=0, second=0, microsecond=0)

    def _generate_weekly_schedule(self):
        """Generate a new weekly schedule for all broadcast channels."""
        logger.info("[SCHEDULER] Generating new weekly schedule...")

        channels = self._load_channels()
        series_data = self._load_series()
        metadata = self._load_metadata()

        schedule = {
            "version": SCHEDULE_VERSION,
            "week_start": self._get_last_sunday_midnight().isoformat(),
            "channels": {}
        }

        for channel in channels:
            channel_name = channel.get("nombre", "")
            series_filter = channel.get("series_filter", [])

            if not series_filter:
                continue  # Not a broadcast channel

            channel_schedule = self._generate_channel_schedule(
                channel_name, series_filter, series_data, metadata
            )
            schedule["channels"][channel_name] = channel_schedule

        self._schedule = schedule
        self._save_schedule()
        logger.info(f"[SCHEDULER] Generated v{SCHEDULE_VERSION} schedule for {len(schedule['channels'])} broadcast channels")

    def _generate_channel_schedule(
        self,
        channel_name: str,
        series_filter: List[str],
        series_data: Dict,
        metadata: Dict
    ) -> Dict:
        """
        Generate daily schedule for a single channel.

        Returns structure:
        {
            "daily_slots": [
                {
                    "slot_name": "early_morning",
                    "start_hour": 4,
                    "end_hour": 7,
                    "series_blocks": [
                        {
                            "series": "Series_Name",
                            "start_minute": 0,  # Minutes from slot start
                            "duration_minutes": 90,
                            "use_commercial_blocks": True
                        }
                    ]
                }
            ],
            "episode_cursor": {
                "Series_Name": {"season": 1, "episode": 1}
            }
        }
        """
        # Get series info for this channel
        channel_series = []
        for s_name in series_filter:
            if s_name in series_data:
                s_info = series_data[s_name]
                episodes = self._get_series_episodes(s_name, metadata)
                if episodes:
                    # Get max duration, running ffprobe on-demand for missing durations
                    max_duration = max(
                        self._get_episode_duration(e.get("video_id", ""), e)
                        for e in episodes
                    )
                    channel_series.append({
                        "name": s_name,
                        "time_of_day": s_info.get("time_of_day", "any"),
                        "episodes": episodes,
                        "max_duration": max_duration,
                        "use_commercial_blocks": max_duration < BLOCK_DURATION_SEC
                    })

        if not channel_series:
            return {"daily_slots": [], "episode_cursor": {}}

        # Build daily slots
        daily_slots = []

        for slot_name, (start_hour, end_hour) in TIME_SLOTS.items():
            # Find eligible series for this slot
            eligible = [
                s for s in channel_series
                if s["time_of_day"] == "any" or s["time_of_day"] == slot_name
            ]

            if not eligible:
                continue

            # Calculate slot duration in minutes
            if end_hour > start_hour:
                slot_duration_min = (end_hour - start_hour) * 60
            else:
                # Crosses midnight (e.g., night: 21-28 means 21:00-04:00)
                slot_duration_min = (end_hour - start_hour) * 60

            # Roll random max contiguous blocks for each series in this slot
            # This determines how many consecutive 30-min blocks each series plays before switching
            series_max_blocks = {}
            for series_info in eligible:
                series_max_blocks[series_info["name"]] = _roll_max_contiguous_blocks()

            # Build series blocks by cycling through series with their block limits
            series_blocks = []
            current_minute = 0
            series_index = 0
            block_duration = 30  # Each block is 30 minutes

            # Track how many blocks each series has played in current turn
            current_turn_blocks = {s["name"]: 0 for s in eligible}

            while current_minute < slot_duration_min:
                series_info = eligible[series_index]
                series_name = series_info["name"]
                max_blocks = series_max_blocks[series_name]

                # Calculate how many blocks this series can play in this turn
                blocks_remaining_in_turn = max_blocks - current_turn_blocks[series_name]
                time_remaining_in_slot = slot_duration_min - current_minute
                blocks_that_fit = time_remaining_in_slot // block_duration

                # Play the minimum of: remaining turn blocks, or what fits in slot
                blocks_to_play = min(blocks_remaining_in_turn, blocks_that_fit)

                if blocks_to_play > 0:
                    duration_minutes = blocks_to_play * block_duration
                    series_blocks.append({
                        "series": series_name,
                        "start_minute": current_minute,
                        "duration_minutes": duration_minutes,
                        "use_commercial_blocks": series_info["use_commercial_blocks"]
                    })
                    current_minute += duration_minutes
                    current_turn_blocks[series_name] += blocks_to_play

                # Check if this series hit its max for this turn
                if current_turn_blocks[series_name] >= max_blocks:
                    # Reset turn counter and move to next series
                    current_turn_blocks[series_name] = 0
                    series_index = (series_index + 1) % len(eligible)
                elif blocks_to_play == 0:
                    # No more full blocks fit, we're done with this slot
                    break

            daily_slots.append({
                "slot_name": slot_name,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "series_blocks": series_blocks,
                "series_max_blocks": series_max_blocks  # Store rolls for debugging/reference
            })

        # Initialize episode cursors (start at first episode)
        episode_cursor = {}
        for s in channel_series:
            if s["episodes"]:
                first_ep = s["episodes"][0]
                episode_cursor[s["name"]] = {
                    "season": first_ep.get("season", 1),
                    "episode": first_ep.get("episode", 1)
                }

        return {
            "daily_slots": daily_slots,
            "episode_cursor": episode_cursor
        }

    def _get_series_episodes(self, series_name: str, metadata: Dict) -> List[Dict]:
        """Get all episodes for a series, sorted by season and episode."""
        episodes = []
        for video_id, data in metadata.items():
            if data.get("series") == series_name and data.get("category") == "tv_episode":
                episodes.append({
                    "video_id": video_id,
                    "series": series_name,  # Include series name for _episode_entry
                    "season": data.get("season", 1) or 1,
                    "episode": data.get("episode", 1) or 1,
                    "duracion": data.get("duracion", 0),
                    "series_path": data.get("series_path", "")
                })

        # Sort by season, then episode
        episodes.sort(key=lambda e: (e["season"], e["episode"]))
        return episodes

    def _get_episode_cursor(self, channel_name: str, series_name: str) -> Dict:
        """Get the episode cursor for a series on a channel."""
        channel_schedule = self._schedule.get("channels", {}).get(channel_name, {})
        cursors = channel_schedule.get("episode_cursor", {})
        return cursors.get(series_name, {"season": 1, "episode": 1})

    def _cursor_to_index(self, cursor: Dict, episodes: List[Dict]) -> int:
        """Convert a cursor (season/episode) to an index in the episodes list."""
        target_season = cursor.get("season", 1)
        target_episode = cursor.get("episode", 1)

        for i, ep in enumerate(episodes):
            if ep.get("season") == target_season and ep.get("episode") == target_episode:
                return i

        return 0  # Default to first episode

    def _test_pattern_entry(self) -> Dict:
        """Create a schedule entry for test pattern."""
        return {
            "content_type": "test_pattern",
            "video_path": str(TEST_PATTERN_FILE),
            "timestamp": 0,
            "duration": 0  # Static image
        }

    def _load_ads(self) -> List[Dict]:
        """Load available ads from the ads directory."""
        ads = []
        ads_dir = Path(ADS_DIR)

        if not ads_dir.exists():
            return ads

        metadata = self._load_metadata()

        # Look for videos in ads directory
        for video_file in ads_dir.glob("*.mp4"):
            video_id = video_file.stem
            meta = metadata.get(video_id, {})

            ads.append({
                "video_id": video_id,
                "path": f"ads/{video_id}.mp4",
                "duracion": meta.get("duracion", 30) or 30
            })

        return ads

    def _save_schedule(self):
        """Save the schedule to disk."""
        schedule_path = Path(SCHEDULE_FILE)
        schedule_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            tmp_path = schedule_path.with_suffix(".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._schedule, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, schedule_path)
            logger.info("[SCHEDULER] Saved weekly schedule")
        except Exception as e:
            logger.error(f"[SCHEDULER] Error saving schedule: {e}")

    def _load_channels(self) -> List[Dict]:
        """Load channels from canales.json."""
        try:
            with open(CANALES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # canales.json is a dict keyed by canal_id, convert to list
                channels = []
                for canal_id, config in data.items():
                    config_copy = config.copy()
                    config_copy["id"] = canal_id
                    channels.append(config_copy)
                return channels
        except Exception:
            return []

    def _load_series(self) -> Dict:
        """Load series data from series.json."""
        try:
            with open(SERIES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_metadata(self) -> Dict:
        """Get video metadata from in-memory cache, loading from disk if needed."""
        if not self._metadata_loaded:
            self._reload_metadata()
        return self._metadata_cache

    def _reload_metadata(self):
        """Reload metadata from disk into cache."""
        try:
            with open(METADATA_FILE, 'r', encoding='utf-8') as f:
                self._metadata_cache = json.load(f)
            self._metadata_loaded = True
            logger.info(f"[SCHEDULER] Loaded metadata cache ({len(self._metadata_cache)} entries)")
        except Exception as e:
            logger.error(f"[SCHEDULER] Error loading metadata: {e}")
            self._metadata_cache = {}
            self._metadata_loaded = True

    def _save_metadata(self):
        """Persist metadata cache to disk."""
        try:
            tmp_path = Path(METADATA_FILE).with_suffix(".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._metadata_cache, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, METADATA_FILE)
            logger.debug("[SCHEDULER] Saved metadata to disk")
        except Exception as e:
            logger.error(f"[SCHEDULER] Error saving metadata: {e}")

    def _get_video_duration_ffprobe(self, filepath: str) -> float:
        """Get video duration using ffprobe."""
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filepath
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
            return float(result.stdout.strip())
        except Exception as e:
            logger.warning(f"[SCHEDULER] Could not get duration for {filepath}: {e}")
            return 0

    def _get_episode_duration(self, video_id: str, episode_data: Dict) -> float:
        """
        Get episode duration, running ffprobe on-demand if missing.

        Args:
            video_id: The video identifier
            episode_data: Episode metadata dict (will be updated if duration found)

        Returns:
            Duration in seconds, or 1800 (30 min) as fallback
        """
        duration = episode_data.get("duracion", 0)
        if duration and duration > 0:
            return duration

        # Duration missing - try to get it via ffprobe
        series_path = episode_data.get("series_path", "")
        if series_path:
            filepath = VIDEO_DIR / f"{series_path}.mp4"
        else:
            filepath = VIDEO_DIR / f"{video_id}.mp4"

        if filepath.exists():
            duration = self._get_video_duration_ffprobe(str(filepath))
            if duration > 0:
                # Update cache and persist
                episode_data["duracion"] = duration
                if video_id in self._metadata_cache:
                    self._metadata_cache[video_id]["duracion"] = duration
                    self._save_metadata()
                logger.info(f"[SCHEDULER] Computed duration for {video_id}: {duration:.1f}s")
                return duration

        # Fallback to 30 minutes
        logger.warning(f"[SCHEDULER] No duration for {video_id}, using 30 min default")
        return 1800

    def force_regenerate_schedule(self):
        """Force regeneration of the weekly schedule."""
        logger.info("[SCHEDULER] Forcing schedule regeneration")
        self._generate_weekly_schedule()

    def reload_metadata(self):
        """Reload metadata cache from disk. Call after external metadata updates."""
        self._reload_metadata()

    def get_current_schedule_info(self, channel_name: str) -> Dict:
        """
        Get human-readable info about current and upcoming programming.

        Returns:
        {
            "now_playing": {...},
            "up_next": {...},
            "schedule_week_start": "2025-12-07T00:00:00"
        }
        """
        now = int(time.time())
        current = self.get_schedule_at(channel_name, now)

        # Find what's next (skip ahead until content changes)
        next_content = None
        for offset in range(1, 3600):  # Look up to 1 hour ahead
            future = self.get_schedule_at(channel_name, now + offset)
            if future and future != current:
                if future.get("content_type") == "episode":
                    next_content = future
                    break

        return {
            "now_playing": current,
            "up_next": next_content,
            "schedule_week_start": self._schedule.get("week_start", "")
        }


# Global scheduler instance
_scheduler: Optional[BroadcastScheduler] = None


def get_scheduler() -> BroadcastScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BroadcastScheduler()
    return _scheduler


def start_scheduler():
    """Start the global scheduler."""
    scheduler = get_scheduler()
    scheduler.start()


def stop_scheduler():
    """Stop the global scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
