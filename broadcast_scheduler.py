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
- Rolling 5-minute buffer of scheduled content
- Commercial break insertion for series with short episodes
- Time-of-day based series programming
"""

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from settings import (
    SCHEDULE_FILE, TIME_SLOTS, VALID_TIME_OF_DAY,
    TEST_PATTERN_FILE, HANG_TIGHT_FILE, ADS_DIR,
    METADATA_FILE, SERIES_FILE, CANALES_FILE, VIDEO_DIR,
)

logger = logging.getLogger("tvargenta")

# Buffer configuration
BUFFER_DURATION_SEC = 300  # 5 minutes of buffer
LOOKAHEAD_SEC = 30  # Compute 30 seconds into the future
SCHEDULER_INTERVAL_SEC = 1  # Run scheduler every second

# Commercial break configuration
BLOCK_DURATION_SEC = 30 * 60  # 30 minutes
MAX_COMMERCIAL_BREAK_SEC = 4 * 60  # 4 minutes max per break
MIN_COMMERCIAL_BREAKS = 3  # Minimum number of breaks per block


class BroadcastScheduler:
    """
    Manages broadcast-style TV scheduling for series channels.

    The scheduler:
    1. Generates weekly schedules on Sunday midnight or when missing
    2. Maintains a rolling 5-minute buffer of scheduled content
    3. Handles commercial breaks for short episodes
    4. Shows test pattern when no eligible content
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._buffer: Dict[str, Dict] = {}  # channel_name:epoch_ts -> content info
        self._schedule: Dict = {}  # Persisted weekly schedule
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._ads_cache: List[Dict] = []  # Cached list of available ads
        self._ads_cache_time: float = 0

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

        Args:
            channel_name: Name of the channel
            timestamp: Unix timestamp (defaults to now)

        Returns:
            Dict with content info or None if not a broadcast channel
        """
        if timestamp is None:
            timestamp = int(time.time())

        key = f"{channel_name}:{timestamp}"

        with self._lock:
            if key in self._buffer:
                return self._buffer[key].copy()

        # If not in buffer, compute it directly (fallback)
        return self._compute_schedule_entry(channel_name, timestamp)

    def _scheduler_loop(self):
        """Main scheduler loop running every second."""
        # Initial schedule load/generation
        self._load_or_generate_schedule()

        # Populate initial buffer
        now = int(time.time())
        for t in range(now, now + BUFFER_DURATION_SEC):
            self._populate_buffer_at(t)

        self._ready.set()
        logger.info("[SCHEDULER] Initial buffer populated, scheduler ready")

        while self._running:
            try:
                loop_start = time.time()

                # Check for Sunday schedule regeneration
                self._check_schedule_regeneration()

                # Compute schedule for now + lookahead
                target_time = int(time.time()) + LOOKAHEAD_SEC
                self._populate_buffer_at(target_time)

                # Prune old entries
                self._prune_buffer()

                # Sleep for remainder of interval
                elapsed = time.time() - loop_start
                sleep_time = max(0, SCHEDULER_INTERVAL_SEC - elapsed)
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"[SCHEDULER] Error in scheduler loop: {e}", exc_info=True)
                time.sleep(1)

    def _load_or_generate_schedule(self):
        """Load existing schedule or generate a new one."""
        schedule_path = Path(SCHEDULE_FILE)

        if schedule_path.exists():
            try:
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    self._schedule = json.load(f)

                # Check if we need to regenerate (Sunday has passed)
                week_start = self._schedule.get("week_start", "")
                if week_start:
                    ws_dt = datetime.fromisoformat(week_start)
                    last_sunday = self._get_last_sunday_midnight()
                    if last_sunday > ws_dt:
                        logger.info("[SCHEDULER] New week detected, regenerating schedule")
                        self._generate_weekly_schedule()
                    else:
                        logger.info(f"[SCHEDULER] Loaded existing schedule from {week_start}")
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
        logger.info(f"[SCHEDULER] Generated schedule for {len(schedule['channels'])} broadcast channels")

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
                    max_duration = max(e.get("duracion", 0) or 0 for e in episodes)
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

            # Balance time among eligible series
            time_per_series = slot_duration_min // len(eligible)

            series_blocks = []
            current_minute = 0

            for series_info in eligible:
                series_blocks.append({
                    "series": series_info["name"],
                    "start_minute": current_minute,
                    "duration_minutes": time_per_series,
                    "use_commercial_blocks": series_info["use_commercial_blocks"]
                })
                current_minute += time_per_series

            daily_slots.append({
                "slot_name": slot_name,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "series_blocks": series_blocks
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

    def _populate_buffer_at(self, timestamp: int):
        """Populate the buffer with schedule entries for all broadcast channels."""
        channels = self._load_channels()

        for channel in channels:
            channel_name = channel.get("nombre", "")
            series_filter = channel.get("series_filter", [])

            if not series_filter:
                continue  # Not a broadcast channel

            entry = self._compute_schedule_entry(channel_name, timestamp)
            if entry:
                key = f"{channel_name}:{timestamp}"
                with self._lock:
                    self._buffer[key] = entry

    def _compute_schedule_entry(self, channel_name: str, timestamp: int) -> Optional[Dict]:
        """
        Compute what should be playing on a channel at a given timestamp.

        Returns:
        {
            "content_type": "episode" | "ad" | "test_pattern" | "hang_tight",
            "series": "Series_Name",  # Only for episodes
            "season": 1,
            "episode": 5,
            "timestamp": 1234.5,  # Seconds into the content
            "video_path": "series/Series_Name/S01E05.mp4",
            "duration": 2700  # Total duration of this content
        }
        """
        channel_schedule = self._schedule.get("channels", {}).get(channel_name)
        if not channel_schedule:
            return self._test_pattern_entry()

        daily_slots = channel_schedule.get("daily_slots", [])
        if not daily_slots:
            return self._test_pattern_entry()

        # Convert timestamp to time of day
        dt = datetime.fromtimestamp(timestamp)
        hour = dt.hour
        minute = dt.minute
        second = dt.second

        # Handle night slot that crosses midnight (21-28 means 21:00-04:00)
        effective_hour = hour
        if hour < 4:
            effective_hour = hour + 24  # Convert 0-3 to 24-27

        # Find the active slot
        active_slot = None
        for slot in daily_slots:
            start_h = slot["start_hour"]
            end_h = slot["end_hour"]
            if start_h <= effective_hour < end_h:
                active_slot = slot
                break

        if not active_slot:
            return self._test_pattern_entry()

        # Calculate minutes into the slot
        slot_start_hour = active_slot["start_hour"]
        if effective_hour >= 24:
            # We're in the early morning part of the night slot
            minutes_into_slot = (effective_hour - slot_start_hour) * 60 + minute
        else:
            minutes_into_slot = (hour - slot_start_hour) * 60 + minute

        # Find the active series block
        series_blocks = active_slot.get("series_blocks", [])
        active_block = None
        for block in series_blocks:
            block_start = block["start_minute"]
            block_end = block_start + block["duration_minutes"]
            if block_start <= minutes_into_slot < block_end:
                active_block = block
                break

        if not active_block:
            return self._test_pattern_entry()

        # Calculate position within series block
        series_name = active_block["series"]
        minutes_into_block = minutes_into_slot - active_block["start_minute"]
        seconds_into_block = minutes_into_block * 60 + second

        # Get series episodes and metadata
        metadata = self._load_metadata()
        episodes = self._get_series_episodes(series_name, metadata)
        if not episodes:
            return self._test_pattern_entry()

        # Determine if we use commercial blocks
        use_commercial_blocks = active_block.get("use_commercial_blocks", False)

        if use_commercial_blocks:
            return self._compute_commercial_block_entry(
                channel_name, series_name, episodes, seconds_into_block, timestamp
            )
        else:
            return self._compute_continuous_playback_entry(
                channel_name, series_name, episodes, seconds_into_block
            )

    def _compute_commercial_block_entry(
        self,
        channel_name: str,
        series_name: str,
        episodes: List[Dict],
        seconds_into_block: int,
        timestamp: int
    ) -> Dict:
        """
        Compute entry for series with 30-minute commercial blocks.

        Each 30-minute block contains:
        - Episode content
        - Commercial breaks (evenly spaced)
        """
        # Figure out which 30-minute block we're in within the series time
        block_index = seconds_into_block // BLOCK_DURATION_SEC
        seconds_into_30min_block = seconds_into_block % BLOCK_DURATION_SEC

        # Get episode for this block (cycling through episodes)
        cursor = self._get_episode_cursor(channel_name, series_name)
        episode_index = self._cursor_to_index(cursor, episodes)

        # Advance by block_index episodes (each block = 1 episode)
        target_index = (episode_index + block_index) % len(episodes)
        episode = episodes[target_index]

        episode_duration = episode.get("duracion", 0) or 1800  # Default 30 min
        if episode_duration >= BLOCK_DURATION_SEC:
            # Episode is >= 30 min, no commercials
            seek_time = min(seconds_into_30min_block, episode_duration - 1)
            return self._episode_entry(episode, seek_time)

        # Calculate commercial breaks
        commercial_time = BLOCK_DURATION_SEC - episode_duration
        num_breaks = max(MIN_COMMERCIAL_BREAKS, math.ceil(commercial_time / MAX_COMMERCIAL_BREAK_SEC))
        break_duration = commercial_time / num_breaks

        # Episode is split into num_breaks + 1 segments
        num_segments = num_breaks + 1
        segment_duration = episode_duration / num_segments

        # Build timeline: [break, segment, break, segment, break, segment, ...]
        # Actually: [segment, break, segment, break, segment, break] - start with content
        timeline = []
        pos = 0
        for i in range(num_segments):
            # Episode segment
            timeline.append({
                "type": "episode",
                "start": pos,
                "duration": segment_duration,
                "episode_start": i * segment_duration  # Position in episode
            })
            pos += segment_duration

            # Commercial break (except after last segment)
            if i < num_breaks:
                timeline.append({
                    "type": "commercial",
                    "start": pos,
                    "duration": break_duration
                })
                pos += break_duration

        # Find where we are in the timeline
        for segment in timeline:
            seg_start = segment["start"]
            seg_end = seg_start + segment["duration"]

            if seg_start <= seconds_into_30min_block < seg_end:
                offset_in_segment = seconds_into_30min_block - seg_start

                if segment["type"] == "episode":
                    episode_time = segment["episode_start"] + offset_in_segment
                    return self._episode_entry(episode, episode_time)
                else:
                    # Commercial break
                    return self._get_commercial_entry(offset_in_segment, segment["duration"], timestamp)

        # Fallback - shouldn't reach here
        return self._episode_entry(episode, 0)

    def _compute_continuous_playback_entry(
        self,
        channel_name: str,
        series_name: str,
        episodes: List[Dict],
        seconds_into_block: int
    ) -> Dict:
        """
        Compute entry for series with continuous playback (no commercial breaks).
        Episodes play back-to-back.
        """
        cursor = self._get_episode_cursor(channel_name, series_name)
        episode_index = self._cursor_to_index(cursor, episodes)

        # Calculate total time through all episodes
        total_time = sum(e.get("duracion", 0) or 1800 for e in episodes)

        # Where are we in the cycle?
        position = seconds_into_block % total_time

        # Find which episode and timestamp
        accumulated = 0
        for i, ep in enumerate(episodes):
            ep_duration = ep.get("duracion", 0) or 1800
            if accumulated + ep_duration > position:
                seek_time = position - accumulated
                return self._episode_entry(ep, seek_time)
            accumulated += ep_duration

        # Fallback
        return self._episode_entry(episodes[0], 0)

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

    def _episode_entry(self, episode: Dict, seek_time: float) -> Dict:
        """Create a buffer entry for an episode."""
        return {
            "content_type": "episode",
            "series": episode.get("series", ""),
            "season": episode.get("season", 1),
            "episode": episode.get("episode", 1),
            "timestamp": max(0, seek_time),
            "video_path": f"{episode.get('series_path', '')}.mp4",
            "duration": episode.get("duracion", 0) or 1800,
            "video_id": episode.get("video_id", "")
        }

    def _test_pattern_entry(self) -> Dict:
        """Create a buffer entry for test pattern."""
        return {
            "content_type": "test_pattern",
            "video_path": str(TEST_PATTERN_FILE),
            "timestamp": 0,
            "duration": 0  # Static image
        }

    def _hang_tight_entry(self) -> Dict:
        """Create a buffer entry for hang tight screen."""
        return {
            "content_type": "hang_tight",
            "video_path": str(HANG_TIGHT_FILE),
            "timestamp": 0,
            "duration": 0  # Static image
        }

    def _get_commercial_entry(self, offset_in_break: float, break_duration: float, timestamp: int) -> Dict:
        """
        Get the commercial to play at a given position in a commercial break.

        Args:
            offset_in_break: Seconds into the commercial break
            break_duration: Total duration of the break
            timestamp: Current timestamp (for cache key)
        """
        ads = self._get_available_ads()

        if not ads:
            # No ads available, show hang tight
            return self._hang_tight_entry()

        # Calculate which ad and position within it
        accumulated = 0
        total_ads_duration = sum(a.get("duracion", 30) for a in ads)

        if total_ads_duration == 0:
            return self._hang_tight_entry()

        # Loop through ads as needed to fill the break
        position_in_ads = offset_in_break % total_ads_duration

        for ad in ads:
            ad_duration = ad.get("duracion", 30) or 30
            if accumulated + ad_duration > position_in_ads:
                seek_time = position_in_ads - accumulated
                return {
                    "content_type": "ad",
                    "video_path": ad.get("path", ""),
                    "timestamp": seek_time,
                    "duration": ad_duration,
                    "video_id": ad.get("video_id", "")
                }
            accumulated += ad_duration

        # If we've exhausted all ads but break isn't over, show hang tight
        remaining = break_duration - offset_in_break
        if remaining > 0 and offset_in_break >= total_ads_duration:
            return self._hang_tight_entry()

        return self._hang_tight_entry()

    def _get_available_ads(self) -> List[Dict]:
        """Get list of available ads, with caching."""
        now = time.time()

        # Refresh cache every 60 seconds
        if now - self._ads_cache_time > 60:
            self._ads_cache = self._load_ads()
            self._ads_cache_time = now

        return self._ads_cache

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

    def _prune_buffer(self):
        """Remove buffer entries older than BUFFER_DURATION_SEC."""
        cutoff = int(time.time()) - BUFFER_DURATION_SEC

        with self._lock:
            keys_to_remove = [
                key for key in self._buffer.keys()
                if int(key.split(":")[-1]) < cutoff
            ]
            for key in keys_to_remove:
                del self._buffer[key]

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
        """Load video metadata from metadata.json."""
        try:
            with open(METADATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def force_regenerate_schedule(self):
        """Force regeneration of the weekly schedule."""
        logger.info("[SCHEDULER] Forcing schedule regeneration")
        self._generate_weekly_schedule()

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
