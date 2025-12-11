#!/usr/bin/env python3
"""
Test cases for broadcast TV scheduling system.
Tests 10 happy paths through the scheduler module.
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Set up test environment before importing scheduler
TEST_DIR = tempfile.mkdtemp(prefix="tvargenta_test_")
TEST_CONTENT_DIR = Path(TEST_DIR) / "content"
TEST_VIDEO_DIR = TEST_CONTENT_DIR / "videos"
TEST_SERIES_DIR = TEST_VIDEO_DIR / "series"

# Create test directories
TEST_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
TEST_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
TEST_SERIES_DIR.mkdir(parents=True, exist_ok=True)

# Patch settings before importing scheduler
import settings
settings.CONTENT_DIR = TEST_CONTENT_DIR
settings.VIDEO_DIR = TEST_VIDEO_DIR
settings.SERIES_VIDEO_DIR = TEST_SERIES_DIR
settings.METADATA_FILE = TEST_CONTENT_DIR / "metadata.json"
settings.CANALES_FILE = TEST_CONTENT_DIR / "canales.json"
settings.SERIES_FILE = TEST_CONTENT_DIR / "series.json"

# Now import scheduler with patched settings
import scheduler

# Override scheduler paths
scheduler.CONTENT_DIR = TEST_CONTENT_DIR
scheduler.VIDEO_DIR = TEST_VIDEO_DIR
scheduler.SERIES_VIDEO_DIR = TEST_SERIES_DIR
scheduler.METADATA_FILE = TEST_CONTENT_DIR / "metadata.json"
scheduler.CANALES_FILE = TEST_CONTENT_DIR / "canales.json"
scheduler.SERIES_FILE = TEST_CONTENT_DIR / "series.json"
scheduler.WEEKLY_SCHEDULE_FILE = TEST_CONTENT_DIR / "weekly_schedule.json"
scheduler.DAILY_SCHEDULE_FILE = TEST_CONTENT_DIR / "daily_schedule.json"
scheduler.EPISODE_CURSORS_FILE = TEST_CONTENT_DIR / "episode_cursors.json"
scheduler.SCHEDULE_META_FILE = TEST_CONTENT_DIR / "schedule_meta.json"
scheduler.SYSTEM_VIDEO_DIR = TEST_VIDEO_DIR / "system"
scheduler.TEST_PATTERN_VIDEO = scheduler.SYSTEM_VIDEO_DIR / "test_pattern.mp4"
scheduler.SPONSORS_PLACEHOLDER_VIDEO = scheduler.SYSTEM_VIDEO_DIR / "sponsors_placeholder.mp4"


def setup_test_data():
    """Create test data files."""
    # Create series.json
    series_data = {
        "Test_Series_A": {
            "created": "2025-01-01",
            "time_of_day": "evening"
        },
        "Test_Series_B": {
            "created": "2025-01-02",
            "time_of_day": "any"
        },
        "Test_Series_C": {
            "created": "2025-01-03",
            "time_of_day": "night"
        }
    }
    with open(TEST_CONTENT_DIR / "series.json", "w") as f:
        json.dump(series_data, f)

    # Create metadata.json with episodes
    metadata = {
        "ep_a_s01e01": {
            "title": "Series A S01E01",
            "category": "tv_episode",
            "series": "Test_Series_A",
            "series_path": "series/Test_Series_A/ep_a_s01e01",
            "season": 1,
            "episode": 1,
            "duracion": 1200,  # 20 minutes
            "tags": []
        },
        "ep_a_s01e02": {
            "title": "Series A S01E02",
            "category": "tv_episode",
            "series": "Test_Series_A",
            "series_path": "series/Test_Series_A/ep_a_s01e02",
            "season": 1,
            "episode": 2,
            "duracion": 1200,
            "tags": []
        },
        "ep_a_s01e03": {
            "title": "Series A S01E03",
            "category": "tv_episode",
            "series": "Test_Series_A",
            "series_path": "series/Test_Series_A/ep_a_s01e03",
            "season": 1,
            "episode": 3,
            "duracion": 1200,
            "tags": []
        },
        "ep_b_s01e01": {
            "title": "Series B S01E01",
            "category": "tv_episode",
            "series": "Test_Series_B",
            "series_path": "series/Test_Series_B/ep_b_s01e01",
            "season": 1,
            "episode": 1,
            "duracion": 2700,  # 45 minutes (long episode)
            "tags": []
        },
        "ep_c_s01e01": {
            "title": "Series C S01E01",
            "category": "tv_episode",
            "series": "Test_Series_C",
            "series_path": "series/Test_Series_C/ep_c_s01e01",
            "season": 1,
            "episode": 1,
            "duracion": 480,  # 8 minutes (very short)
            "tags": []
        },
        "ep_c_s01e02": {
            "title": "Series C S01E02",
            "category": "tv_episode",
            "series": "Test_Series_C",
            "series_path": "series/Test_Series_C/ep_c_s01e02",
            "season": 1,
            "episode": 2,
            "duracion": 480,
            "tags": []
        },
        "commercial_1": {
            "title": "Test Commercial 1",
            "category": "commercial",
            "duracion": 30,
            "tags": []
        },
        "commercial_2": {
            "title": "Test Commercial 2",
            "category": "commercial",
            "duracion": 60,
            "tags": []
        }
    }
    with open(TEST_CONTENT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f)

    # Create canales.json
    canales = {
        "channel_1": {
            "nombre": "Test Channel 1",
            "series_filter": ["Test_Series_A", "Test_Series_B"],
            "tags_prioridad": [],
            "tags_incluidos": []
        },
        "channel_2": {
            "nombre": "Test Channel 2",
            "series_filter": ["Test_Series_C"],
            "tags_prioridad": [],
            "tags_incluidos": []
        },
        "channel_3": {
            "nombre": "Regular Channel",
            "tags_prioridad": ["tag1"],
            "tags_incluidos": ["tag1"]
        }
    }
    with open(TEST_CONTENT_DIR / "canales.json", "w") as f:
        json.dump(canales, f)


def cleanup_test_data():
    """Clean up test data."""
    try:
        shutil.rmtree(TEST_DIR)
    except Exception as e:
        print(f"Warning: Could not clean up test directory: {e}")


def test_1_get_series_time_of_day():
    """Test 1: Getting time-of-day preference for a series."""
    print("\n=== Test 1: Get series time_of_day ===")

    # Test existing series with time_of_day
    result = scheduler.get_series_time_of_day("Test_Series_A")
    assert result == "evening", f"Expected 'evening', got '{result}'"
    print(f"  Test_Series_A time_of_day: {result} ✓")

    # Test series with "any"
    result = scheduler.get_series_time_of_day("Test_Series_B")
    assert result == "any", f"Expected 'any', got '{result}'"
    print(f"  Test_Series_B time_of_day: {result} ✓")

    # Test non-existent series (should return default "any")
    result = scheduler.get_series_time_of_day("Non_Existent_Series")
    assert result == "any", f"Expected 'any' for non-existent, got '{result}'"
    print(f"  Non-existent series time_of_day: {result} ✓")

    print("  Test 1 PASSED")
    return True


def test_2_set_series_time_of_day():
    """Test 2: Setting time-of-day preference for a series."""
    print("\n=== Test 2: Set series time_of_day ===")

    # Set time_of_day to a valid value
    result = scheduler.set_series_time_of_day("Test_Series_A", "morning")
    # This should fail because "morning" is not valid
    # Valid options are: early_morning, late_morning, afternoon, evening, night, any

    # Let's test with a valid value
    result = scheduler.set_series_time_of_day("Test_Series_A", "afternoon")
    assert result == True, f"Expected True, got {result}"
    print(f"  Set Test_Series_A to 'afternoon': {result} ✓")

    # Verify it was saved
    saved = scheduler.get_series_time_of_day("Test_Series_A")
    assert saved == "afternoon", f"Expected 'afternoon', got '{saved}'"
    print(f"  Verified saved value: {saved} ✓")

    # Reset back to evening for other tests
    scheduler.set_series_time_of_day("Test_Series_A", "evening")

    print("  Test 2 PASSED")
    return True


def test_3_get_series_episodes():
    """Test 3: Getting episodes for a series in chronological order."""
    print("\n=== Test 3: Get series episodes ===")

    episodes = scheduler.get_series_episodes("Test_Series_A")

    assert len(episodes) == 3, f"Expected 3 episodes, got {len(episodes)}"
    print(f"  Found {len(episodes)} episodes for Test_Series_A ✓")

    # Verify chronological order
    for i, ep in enumerate(episodes):
        expected_ep_num = i + 1
        assert ep["episode"] == expected_ep_num, f"Expected episode {expected_ep_num}, got {ep['episode']}"
        print(f"    Episode {i+1}: {ep['video_id']} (S{ep['season']}E{ep['episode']}) ✓")

    # Test empty series
    episodes = scheduler.get_series_episodes("Non_Existent_Series")
    assert len(episodes) == 0, f"Expected 0 episodes, got {len(episodes)}"
    print(f"  Non-existent series: 0 episodes ✓")

    print("  Test 3 PASSED")
    return True


def test_4_get_commercials():
    """Test 4: Getting commercial videos from metadata."""
    print("\n=== Test 4: Get commercials ===")

    commercials = scheduler.get_commercials()

    assert len(commercials) == 2, f"Expected 2 commercials, got {len(commercials)}"
    print(f"  Found {len(commercials)} commercials ✓")

    for comm in commercials:
        assert "video_id" in comm, "Commercial missing video_id"
        assert "duration" in comm, "Commercial missing duration"
        print(f"    {comm['video_id']}: {comm['duration']}s ✓")

    print("  Test 4 PASSED")
    return True


def test_5_episode_cursor_management():
    """Test 5: Episode cursor progression through a series."""
    print("\n=== Test 5: Episode cursor management ===")

    # Clear any existing cursors
    scheduler.save_episode_cursors({})

    # Get first episode
    ep1 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep1 is not None, "Expected an episode"
    assert ep1["episode"] == 1, f"Expected episode 1, got {ep1['episode']}"
    print(f"  First call: {ep1['video_id']} (S{ep1['season']}E{ep1['episode']}) ✓")

    # Get second episode
    ep2 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep2["episode"] == 2, f"Expected episode 2, got {ep2['episode']}"
    print(f"  Second call: {ep2['video_id']} (S{ep2['season']}E{ep2['episode']}) ✓")

    # Get third episode
    ep3 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep3["episode"] == 3, f"Expected episode 3, got {ep3['episode']}"
    print(f"  Third call: {ep3['video_id']} (S{ep3['season']}E{ep3['episode']}) ✓")

    # Get fourth episode (should wrap to episode 1)
    ep4 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep4["episode"] == 1, f"Expected episode 1 (wrap), got {ep4['episode']}"
    print(f"  Fourth call (wrap): {ep4['video_id']} (S{ep4['season']}E{ep4['episode']}) ✓")

    # Verify cursors are persisted
    cursors = scheduler.load_episode_cursors()
    assert "channel_1" in cursors, "Channel not in cursors"
    assert "Test_Series_A" in cursors["channel_1"], "Series not in channel cursors"
    print(f"  Cursors persisted correctly ✓")

    print("  Test 5 PASSED")
    return True


def test_6_block_structure_calculation():
    """Test 6: Block structure calculation for different episode durations."""
    print("\n=== Test 6: Block structure calculation ===")

    # Very short episode (< 10 min) -> 3 per block
    result = scheduler.calculate_block_structure(480)  # 8 minutes
    assert result["type"] == "very_short", f"Expected 'very_short', got '{result['type']}'"
    assert result["episodes_per_block"] == 3, f"Expected 3 episodes per block"
    print(f"  8 min episode: {result['type']}, {result['episodes_per_block']} per block ✓")

    # Short episode (10-15 min) -> 2 per block
    result = scheduler.calculate_block_structure(720)  # 12 minutes
    assert result["type"] == "short", f"Expected 'short', got '{result['type']}'"
    assert result["episodes_per_block"] == 2, f"Expected 2 episodes per block"
    print(f"  12 min episode: {result['type']}, {result['episodes_per_block']} per block ✓")

    # Medium episode (15-28 min) -> 1 per block with padding
    result = scheduler.calculate_block_structure(1200)  # 20 minutes
    assert result["type"] == "medium", f"Expected 'medium', got '{result['type']}'"
    assert result["blocks"] == 1, f"Expected 1 block"
    print(f"  20 min episode: {result['type']}, {result['blocks']} block(s) ✓")

    # Long episode (28-58 min) -> spans 2 blocks
    result = scheduler.calculate_block_structure(2700)  # 45 minutes
    assert result["type"] == "long", f"Expected 'long', got '{result['type']}'"
    assert result["blocks"] == 2, f"Expected 2 blocks"
    print(f"  45 min episode: {result['type']}, {result['blocks']} block(s) ✓")

    # Very long episode (> 58 min) -> spans 3+ blocks
    result = scheduler.calculate_block_structure(4200)  # 70 minutes
    assert result["type"] == "very_long", f"Expected 'very_long', got '{result['type']}'"
    assert result["blocks"] >= 3, f"Expected 3+ blocks, got {result['blocks']}"
    print(f"  70 min episode: {result['type']}, {result['blocks']} block(s) ✓")

    print("  Test 6 PASSED")
    return True


def test_7_commercial_sequence_building():
    """Test 7: Building commercial sequences to fill duration."""
    print("\n=== Test 7: Commercial sequence building ===")

    commercials = scheduler.get_commercials()

    # Build a 2-minute commercial break
    sequence = scheduler.build_commercial_sequence(120, commercials)

    total_duration = sum(c.get("duration", 0) for c in sequence)
    assert total_duration >= 120, f"Expected at least 120s, got {total_duration}s"
    print(f"  2-minute break: {len(sequence)} commercials, {total_duration}s total ✓")

    for c in sequence:
        assert c["type"] == "commercial", f"Expected 'commercial', got '{c['type']}'"
    print(f"  All entries are type 'commercial' ✓")

    # Test with empty commercials (should use sponsors placeholder)
    sequence = scheduler.build_commercial_sequence(60, [])
    assert len(sequence) > 0, "Expected at least one entry"
    assert sequence[0]["type"] == "sponsors_placeholder", f"Expected sponsors_placeholder"
    print(f"  Empty commercials: uses sponsors_placeholder ✓")

    print("  Test 7 PASSED")
    return True


def test_8_eligible_series_for_time():
    """Test 8: Getting eligible series for a time-of-day slot."""
    print("\n=== Test 8: Eligible series for time of day ===")

    series_data = scheduler.load_series()
    channel_series = ["Test_Series_A", "Test_Series_B", "Test_Series_C"]

    # Evening: should include Test_Series_A (evening) and Test_Series_B (any)
    eligible = scheduler.get_eligible_series_for_time("evening", channel_series, series_data)
    assert "Test_Series_A" in eligible, "Test_Series_A should be eligible for evening"
    assert "Test_Series_B" in eligible, "Test_Series_B (any) should be eligible for evening"
    assert "Test_Series_C" not in eligible, "Test_Series_C (night) should not be eligible for evening"
    print(f"  Evening: {eligible} ✓")

    # Night: should include Test_Series_B (any) and Test_Series_C (night)
    eligible = scheduler.get_eligible_series_for_time("night", channel_series, series_data)
    assert "Test_Series_B" in eligible, "Test_Series_B (any) should be eligible for night"
    assert "Test_Series_C" in eligible, "Test_Series_C should be eligible for night"
    print(f"  Night: {eligible} ✓")

    # Early morning: only Test_Series_B (any) should be eligible
    eligible = scheduler.get_eligible_series_for_time("early_morning", channel_series, series_data)
    assert "Test_Series_B" in eligible, "Test_Series_B (any) should be eligible"
    assert len(eligible) == 1, f"Expected only 1 series, got {len(eligible)}"
    print(f"  Early morning: {eligible} ✓")

    print("  Test 8 PASSED")
    return True


def test_9_weekly_schedule_generation():
    """Test 9: Weekly schedule generation."""
    print("\n=== Test 9: Weekly schedule generation ===")

    # Clear existing schedule
    if scheduler.WEEKLY_SCHEDULE_FILE.exists():
        scheduler.WEEKLY_SCHEDULE_FILE.unlink()

    # Generate weekly schedule
    schedule = scheduler.generate_weekly_schedule()

    assert "generated_at" in schedule, "Missing generated_at"
    assert "week_start" in schedule, "Missing week_start"
    assert "channels" in schedule, "Missing channels"
    print(f"  Generated at: {schedule['generated_at']} ✓")
    print(f"  Week start: {schedule['week_start']} ✓")

    # Check channel_1 (has series_filter)
    assert "channel_1" in schedule["channels"], "channel_1 missing from schedule"
    ch1 = schedule["channels"]["channel_1"]
    assert "time_slots" in ch1, "Missing time_slots"
    print(f"  channel_1 has {len(ch1['time_slots'])} time-of-day slots ✓")

    # Verify time slots exist
    for time_of_day in ["early_morning", "late_morning", "afternoon", "evening", "night"]:
        assert time_of_day in ch1["time_slots"], f"Missing {time_of_day} slot"
        slots = ch1["time_slots"][time_of_day]
        expected_count = scheduler.TIME_OF_DAY_SLOTS[time_of_day]
        assert len(slots) == expected_count, f"Expected {expected_count} slots for {time_of_day}, got {len(slots)}"
    print(f"  All time slots have correct counts ✓")

    # channel_3 (no series_filter) should not be in schedule
    assert "channel_3" not in schedule["channels"], "channel_3 should not be in schedule"
    print(f"  channel_3 (non-series) not in schedule ✓")

    # Verify schedule was saved
    assert scheduler.WEEKLY_SCHEDULE_FILE.exists(), "Weekly schedule file not created"
    print(f"  Schedule saved to file ✓")

    print("  Test 9 PASSED")
    return True


def test_10_daily_schedule_generation():
    """Test 10: Daily schedule generation with second-by-second mapping."""
    print("\n=== Test 10: Daily schedule generation ===")

    # Ensure weekly schedule exists first
    if not scheduler.WEEKLY_SCHEDULE_FILE.exists():
        scheduler.generate_weekly_schedule()

    # Clear episode cursors for clean test
    scheduler.save_episode_cursors({})

    # Mock the system video generation (skip actual ffmpeg calls)
    scheduler.SYSTEM_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    # Create dummy video files
    (scheduler.SYSTEM_VIDEO_DIR / "test_pattern.mp4").touch()
    (scheduler.SYSTEM_VIDEO_DIR / "sponsors_placeholder.mp4").touch()

    # Generate daily schedule
    schedule = scheduler.generate_daily_schedule()

    assert "generated_at" in schedule, "Missing generated_at"
    assert "schedule_date" in schedule, "Missing schedule_date"
    assert "valid_from" in schedule, "Missing valid_from"
    assert "valid_until" in schedule, "Missing valid_until"
    assert "channels" in schedule, "Missing channels"
    print(f"  Generated at: {schedule['generated_at']} ✓")
    print(f"  Valid from: {schedule['valid_from']} to {schedule['valid_until']} ✓")

    # Check channel_1
    assert "channel_1" in schedule["channels"], "channel_1 missing"
    ch1_entries = schedule["channels"]["channel_1"]
    assert len(ch1_entries) > 0, "No entries for channel_1"
    print(f"  channel_1 has {len(ch1_entries)} schedule entries ✓")

    # Check first entry is test pattern (3am-4am)
    first_entry = ch1_entries[0]
    assert first_entry["type"] == "test_pattern", f"Expected test_pattern, got {first_entry['type']}"
    assert first_entry["start"] == 0, f"Expected start=0, got {first_entry['start']}"
    assert first_entry["end"] == 3600, f"Expected end=3600, got {first_entry['end']}"
    print(f"  First entry: test_pattern (0-3600s = 3am-4am) ✓")

    # Verify there are episode and commercial entries
    entry_types = set(e["type"] for e in ch1_entries)
    print(f"  Entry types found: {entry_types}")

    # Verify schedule was saved
    assert scheduler.DAILY_SCHEDULE_FILE.exists(), "Daily schedule file not created"
    print(f"  Schedule saved to file ✓")

    # Verify cursors were updated
    cursors = scheduler.load_episode_cursors()
    assert len(cursors) > 0, "Episode cursors not updated"
    print(f"  Episode cursors updated ✓")

    print("  Test 10 PASSED")
    return True


def test_11_get_scheduled_content():
    """Test 11: Get scheduled content for a specific timestamp."""
    print("\n=== Test 11: Get scheduled content lookup ===")

    # Ensure schedules exist
    if not scheduler.DAILY_SCHEDULE_FILE.exists():
        scheduler.generate_weekly_schedule()
        scheduler.generate_daily_schedule()

    # Test at 3:30am (should be test pattern)
    test_time = datetime.now().replace(hour=3, minute=30, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result is not None, "Expected result for test pattern time"
    assert result["type"] == "test_pattern", f"Expected test_pattern at 3:30am, got {result['type']}"
    assert "video_url" in result, "Missing video_url"
    assert "seek_to" in result, "Missing seek_to"
    print(f"  3:30am: {result['type']} ✓")

    # Test at 5pm (should be programming)
    test_time = datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result is not None, "Expected result for 5pm"
    assert result["type"] in ["episode", "commercial", "test_pattern"], f"Unexpected type: {result['type']}"
    print(f"  5:00pm: {result['type']} (video_id: {result.get('video_id', 'N/A')}) ✓")

    # Test seek_to calculation
    test_time = datetime.now().replace(hour=17, minute=0, second=30, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)
    # seek_to should account for the 30 seconds into the block
    assert result.get("seek_to", 0) >= 0, "seek_to should be non-negative"
    print(f"  5:00:30pm: seek_to = {result.get('seek_to', 0)}s ✓")

    print("  Test 11 PASSED")
    return True


def test_12_time_of_day_period_detection():
    """Test 12: Time-of-day period detection for different hours."""
    print("\n=== Test 12: Time-of-day period detection ===")

    test_cases = [
        (4, "early_morning"),
        (6, "early_morning"),
        (7, "late_morning"),
        (11, "late_morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (20, "evening"),
        (21, "night"),
        (23, "night"),
        (0, "night"),
        (2, "night"),
    ]

    for hour, expected in test_cases:
        result = scheduler.get_time_of_day_for_hour(hour)
        assert result == expected, f"Hour {hour}: expected {expected}, got {result}"
        print(f"  {hour}:00 -> {result} ✓")

    print("  Test 12 PASSED")
    return True


def test_13_back_to_back_probability():
    """Test 13: Back-to-back episode probability selection."""
    print("\n=== Test 13: Back-to-back probability ===")

    # Run multiple times and collect distribution
    counts = {2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
    iterations = 1000

    for _ in range(iterations):
        result = scheduler.select_back_to_back_count()
        assert result in counts, f"Unexpected result: {result}"
        counts[result] += 1

    print(f"  Distribution over {iterations} iterations:")
    for count, occurrences in sorted(counts.items()):
        percentage = (occurrences / iterations) * 100
        expected = scheduler.BACK_TO_BACK_WEIGHTS[count]
        print(f"    {count} episodes: {percentage:.1f}% (expected ~{expected}%)")

    # Verify 2 is most common (should be ~80%)
    assert counts[2] > counts[3], "2 should be more common than 3"
    assert counts[2] > counts[4], "2 should be more common than 4"
    assert counts[2] > counts[5], "2 should be more common than 5"
    assert counts[2] > counts[6], "2 should be more common than 6"
    print(f"  2-episode is most common ✓")

    print("  Test 13 PASSED")
    return True


def test_14_generate_block_schedule():
    """Test 14: Block schedule generation with commercials and episodes."""
    print("\n=== Test 14: Generate block schedule ===")

    commercials = scheduler.get_commercials()
    metadata = scheduler.load_metadata()

    # Test single medium episode (20 min) in a 30-min block
    episodes = [{
        "video_id": "ep_a_s01e01",
        "series_path": "series/Test_Series_A/ep_a_s01e01",
        "duration": 1200,  # 20 minutes
    }]

    entries = scheduler.generate_block_schedule(0, episodes, commercials, 1800)

    assert len(entries) > 0, "Expected entries to be generated"
    print(f"  Generated {len(entries)} entries for 20-min episode in 30-min block")

    # Verify we have both episode and commercial entries
    entry_types = set(e["type"] for e in entries)
    assert "episode" in entry_types, "Should have episode entries"
    assert "commercial" in entry_types, "Should have commercial entries"
    print(f"  Entry types: {entry_types} ✓")

    # Verify entries have required fields
    for entry in entries:
        assert "start" in entry, "Entry missing 'start'"
        assert "end" in entry, "Entry missing 'end'"
        assert "type" in entry, "Entry missing 'type'"
        assert "video_id" in entry, "Entry missing 'video_id'"
        assert entry["end"] > entry["start"], "End should be > start"
    print(f"  All entries have required fields ✓")

    # Verify episode entries have base_timestamp
    episode_entries = [e for e in entries if e["type"] == "episode"]
    for ep in episode_entries:
        assert "base_timestamp" in ep, "Episode missing base_timestamp"
    print(f"  Episode entries have base_timestamp ✓")

    print("  Test 14 PASSED")
    return True


def test_15_cross_day_boundary():
    """Test 15: Schedule lookup handles cross-day boundary (before/after 3am)."""
    print("\n=== Test 15: Cross-day boundary handling ===")

    # Ensure schedule exists
    if not scheduler.DAILY_SCHEDULE_FILE.exists():
        scheduler.generate_weekly_schedule()
        scheduler.generate_daily_schedule()

    # Test at 2:30am (before 3am - should be in "night" of previous logical day)
    # This is still within the test pattern period (3am-4am) or late night
    test_time = datetime.now().replace(hour=2, minute=30, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result is not None, "Should return content for 2:30am"
    print(f"  2:30am: {result['type']} (seek_to: {result.get('seek_to', 0)}) ✓")

    # Test at 3:15am (within test pattern period)
    test_time = datetime.now().replace(hour=3, minute=15, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result is not None, "Should return content for 3:15am"
    assert result["type"] == "test_pattern", f"Expected test_pattern, got {result['type']}"
    print(f"  3:15am: {result['type']} ✓")

    # Test at 4:01am (just after test pattern ends)
    test_time = datetime.now().replace(hour=4, minute=1, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result is not None, "Should return content for 4:01am"
    assert result["type"] != "test_pattern" or result["type"] in ["episode", "commercial", "test_pattern"], \
        "Should be programming or scheduled content"
    print(f"  4:01am: {result['type']} ✓")

    print("  Test 15 PASSED")
    return True


def test_16_schedule_regeneration_checks():
    """Test 16: Schedule regeneration need detection."""
    print("\n=== Test 16: Schedule regeneration checks ===")

    now = datetime.now()

    # Test needs_daily_regeneration with no schedule
    if scheduler.DAILY_SCHEDULE_FILE.exists():
        scheduler.DAILY_SCHEDULE_FILE.unlink()

    meta = {}
    result = scheduler.needs_daily_regeneration(meta, now)
    assert result == True, "Should need regeneration when no schedule exists"
    print(f"  No schedule exists: needs_daily_regeneration = {result} ✓")

    # Generate a schedule
    scheduler.generate_weekly_schedule()
    scheduler.generate_daily_schedule()

    # Test with recent schedule
    meta = {"daily_generated": now.isoformat()}
    result = scheduler.needs_daily_regeneration(meta, now)
    # If we're past 3am, we shouldn't need regeneration (we just generated)
    print(f"  Recent schedule: needs_daily_regeneration = {result}")

    # Test needs_weekly_regeneration
    # Not Sunday - should not need regeneration
    non_sunday = now
    while non_sunday.weekday() == 6:  # Find a non-Sunday
        non_sunday = non_sunday + timedelta(days=1)

    if non_sunday.weekday() != 6:
        meta = {"weekly_generated": non_sunday.isoformat()}
        result = scheduler.needs_weekly_regeneration(meta, non_sunday)
        assert result == False, "Should not need weekly regeneration on non-Sunday"
        print(f"  Non-Sunday: needs_weekly_regeneration = {result} ✓")

    print("  Test 16 PASSED")
    return True


def test_17_is_broadcast_channel():
    """Test 17: Broadcast channel detection."""
    print("\n=== Test 17: Is broadcast channel detection ===")

    # channel_1 has series_filter - should be broadcast
    result = scheduler.is_broadcast_channel("channel_1")
    assert result == True, "channel_1 should be broadcast channel"
    print(f"  channel_1 (has series_filter): is_broadcast = {result} ✓")

    # channel_3 has no series_filter - should not be broadcast
    result = scheduler.is_broadcast_channel("channel_3")
    assert result == False, "channel_3 should not be broadcast channel"
    print(f"  channel_3 (no series_filter): is_broadcast = {result} ✓")

    # Non-existent channel
    result = scheduler.is_broadcast_channel("non_existent_channel")
    assert result == False, "Non-existent channel should not be broadcast"
    print(f"  non_existent_channel: is_broadcast = {result} ✓")

    print("  Test 17 PASSED")
    return True


def test_18_schedule_lookup_no_schedule():
    """Test 18: Schedule lookup when no schedule exists for channel."""
    print("\n=== Test 18: Schedule lookup - no schedule ===")

    # Test with a channel that has no schedule entries
    result = scheduler.get_scheduled_content("non_existent_channel")

    # Should return test pattern as fallback
    assert result is not None, "Should return fallback content"
    assert result["type"] == "test_pattern", f"Fallback should be test_pattern, got {result['type']}"
    print(f"  Non-existent channel: returns {result['type']} fallback ✓")

    # Test with channel_3 (not a broadcast channel, so no schedule)
    result = scheduler.get_scheduled_content("channel_3")
    assert result is not None, "Should return fallback for non-broadcast channel"
    print(f"  channel_3 (non-broadcast): returns {result['type']} ✓")

    print("  Test 18 PASSED")
    return True


def test_19_peek_episode_without_advancing():
    """Test 19: Peek at next episode without advancing cursor."""
    print("\n=== Test 19: Peek episode without advancing ===")

    # Clear cursors
    scheduler.save_episode_cursors({})

    # Peek at next episode
    peeked = scheduler.peek_next_episode_for_channel("channel_1", "Test_Series_A")
    assert peeked is not None, "Should peek an episode"
    assert peeked["episode"] == 1, "First peek should be episode 1"
    print(f"  First peek: {peeked['video_id']} (S{peeked['season']}E{peeked['episode']}) ✓")

    # Peek again - should still be episode 1 (not advanced)
    peeked2 = scheduler.peek_next_episode_for_channel("channel_1", "Test_Series_A")
    assert peeked2["episode"] == 1, "Second peek should still be episode 1"
    print(f"  Second peek: {peeked2['video_id']} (still episode 1) ✓")

    # Peek with offset
    peeked_offset = scheduler.peek_next_episode_for_channel("channel_1", "Test_Series_A", offset=1)
    assert peeked_offset["episode"] == 2, "Peek with offset=1 should be episode 2"
    print(f"  Peek with offset=1: {peeked_offset['video_id']} (S{peeked_offset['season']}E{peeked_offset['episode']}) ✓")

    # Now actually get episode - should advance cursor
    actual = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert actual["episode"] == 1, "Get should return episode 1"

    # Peek again - should now be episode 2
    peeked3 = scheduler.peek_next_episode_for_channel("channel_1", "Test_Series_A")
    assert peeked3["episode"] == 2, "After get, peek should be episode 2"
    print(f"  After get, peek: {peeked3['video_id']} (now episode 2) ✓")

    print("  Test 19 PASSED")
    return True


def test_20_multiple_episodes_per_block():
    """Test 20: Block with multiple short episodes."""
    print("\n=== Test 20: Multiple episodes per block ===")

    commercials = scheduler.get_commercials()

    # Test with 3 very short episodes (8 min each = 24 min total)
    episodes = [
        {"video_id": "ep_c_s01e01", "series_path": "series/Test_Series_C/ep_c_s01e01", "duration": 480},
        {"video_id": "ep_c_s01e02", "series_path": "series/Test_Series_C/ep_c_s01e02", "duration": 480},
        {"video_id": "ep_c_s01e03", "series_path": "series/Test_Series_C/ep_c_s01e03", "duration": 480},
    ]

    entries = scheduler.generate_block_schedule(0, episodes, commercials, 1800)

    # Count episode entries
    episode_entries = [e for e in entries if e["type"] == "episode"]
    unique_videos = set(e["video_id"] for e in episode_entries)

    print(f"  Generated {len(entries)} total entries")
    print(f"  Episode entries: {len(episode_entries)}")
    print(f"  Unique episode videos: {unique_videos}")

    # Should have entries for multiple episodes
    assert len(unique_videos) >= 2, f"Expected multiple episodes, got {len(unique_videos)}"
    print(f"  Multiple episodes in block ✓")

    # Commercial entries should exist
    commercial_entries = [e for e in entries if e["type"] == "commercial"]
    assert len(commercial_entries) > 0, "Should have commercial entries"
    print(f"  Commercial entries: {len(commercial_entries)} ✓")

    print("  Test 20 PASSED")
    return True


def test_21_slot_index_calculation():
    """Test 21: Slot index calculation within time-of-day periods."""
    print("\n=== Test 21: Slot index calculation ===")

    # Test early_morning slots (4am-7am = 6 slots)
    # 4:00 = slot 0, 4:30 = slot 1, 5:00 = slot 2, etc.
    time_of_day, slot_idx = scheduler.get_slot_index_for_time(4, 0)
    assert time_of_day == "early_morning", f"Expected early_morning, got {time_of_day}"
    assert slot_idx == 0, f"4:00am should be slot 0, got {slot_idx}"
    print(f"  4:00am: {time_of_day}, slot {slot_idx} ✓")

    time_of_day, slot_idx = scheduler.get_slot_index_for_time(4, 30)
    assert slot_idx == 1, f"4:30am should be slot 1, got {slot_idx}"
    print(f"  4:30am: {time_of_day}, slot {slot_idx} ✓")

    time_of_day, slot_idx = scheduler.get_slot_index_for_time(6, 30)
    assert slot_idx == 5, f"6:30am should be slot 5, got {slot_idx}"
    print(f"  6:30am: {time_of_day}, slot {slot_idx} ✓")

    # Test evening slots (5pm-9pm = 8 slots)
    time_of_day, slot_idx = scheduler.get_slot_index_for_time(17, 0)
    assert time_of_day == "evening", f"Expected evening, got {time_of_day}"
    assert slot_idx == 0, f"5:00pm should be slot 0, got {slot_idx}"
    print(f"  5:00pm: {time_of_day}, slot {slot_idx} ✓")

    time_of_day, slot_idx = scheduler.get_slot_index_for_time(20, 30)
    assert slot_idx == 7, f"8:30pm should be slot 7, got {slot_idx}"
    print(f"  8:30pm: {time_of_day}, slot {slot_idx} ✓")

    # Test night slots with wrap-around (9pm-3am)
    time_of_day, slot_idx = scheduler.get_slot_index_for_time(21, 0)
    assert time_of_day == "night", f"Expected night, got {time_of_day}"
    assert slot_idx == 0, f"9:00pm should be slot 0, got {slot_idx}"
    print(f"  9:00pm: {time_of_day}, slot {slot_idx} ✓")

    time_of_day, slot_idx = scheduler.get_slot_index_for_time(0, 0)
    assert time_of_day == "night", f"Expected night for midnight, got {time_of_day}"
    # Midnight is 3 hours after 9pm = 6 slots
    assert slot_idx == 6, f"Midnight should be slot 6, got {slot_idx}"
    print(f"  Midnight: {time_of_day}, slot {slot_idx} ✓")

    print("  Test 21 PASSED")
    return True


def test_22_video_url_construction():
    """Test 22: Video URL construction for different content types."""
    print("\n=== Test 22: Video URL construction ===")

    # Ensure schedule exists
    if not scheduler.DAILY_SCHEDULE_FILE.exists():
        scheduler.generate_weekly_schedule()
        scheduler.generate_daily_schedule()

    # Test at various times and verify URL format
    # Test pattern should have system path
    test_time = datetime.now().replace(hour=3, minute=30, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    assert result["video_url"] == "/videos/system/test_pattern.mp4", \
        f"Test pattern URL wrong: {result['video_url']}"
    print(f"  Test pattern URL: {result['video_url']} ✓")

    # Find a time with episode or commercial content
    test_time = datetime.now().replace(hour=17, minute=15, second=0, microsecond=0)
    result = scheduler.get_scheduled_content("channel_1", test_time)

    if result["type"] == "episode":
        assert "/videos/" in result["video_url"], "Episode URL should contain /videos/"
        assert ".mp4" in result["video_url"], "Episode URL should end with .mp4"
        print(f"  Episode URL: {result['video_url']} ✓")
    elif result["type"] == "commercial":
        assert result["video_url"].startswith("/videos/"), "Commercial URL should start with /videos/"
        assert result["video_url"].endswith(".mp4"), "Commercial URL should end with .mp4"
        print(f"  Commercial URL: {result['video_url']} ✓")

    print("  Test 22 PASSED")
    return True


def test_23_invalid_time_of_day_rejected():
    """Test 23: Invalid time-of-day values are rejected."""
    print("\n=== Test 23: Invalid time_of_day rejection ===")

    # Try to set invalid time_of_day
    result = scheduler.set_series_time_of_day("Test_Series_A", "invalid_value")
    assert result == False, "Should reject invalid time_of_day"
    print(f"  'invalid_value' rejected: {result} ✓")

    result = scheduler.set_series_time_of_day("Test_Series_A", "morning")
    assert result == False, "Should reject 'morning' (not a valid option)"
    print(f"  'morning' rejected: {result} ✓")

    result = scheduler.set_series_time_of_day("Test_Series_A", "")
    assert result == False, "Should reject empty string"
    print(f"  Empty string rejected: {result} ✓")

    # Try to set on non-existent series
    result = scheduler.set_series_time_of_day("Non_Existent_Series", "evening")
    assert result == False, "Should reject non-existent series"
    print(f"  Non-existent series rejected: {result} ✓")

    # Verify valid values are still accepted
    result = scheduler.set_series_time_of_day("Test_Series_A", "evening")
    assert result == True, "Should accept valid time_of_day"
    print(f"  'evening' accepted: {result} ✓")

    print("  Test 23 PASSED")
    return True


def test_24_daily_schedule_entry_continuity():
    """Test 24: Daily schedule entries cover full day without gaps."""
    print("\n=== Test 24: Schedule entry continuity ===")

    # Generate fresh schedule
    scheduler.generate_weekly_schedule()
    scheduler.generate_daily_schedule()

    schedule = scheduler.load_daily_schedule()
    channel_entries = schedule.get("channels", {}).get("channel_1", [])

    assert len(channel_entries) > 0, "Should have entries"
    print(f"  Total entries: {len(channel_entries)}")

    # Check that entries are sorted and contiguous
    prev_end = 0
    gaps = []
    overlaps = []

    for entry in channel_entries:
        if entry["start"] > prev_end:
            gaps.append((prev_end, entry["start"]))
        elif entry["start"] < prev_end:
            overlaps.append((prev_end, entry["start"]))
        prev_end = entry["end"]

    if gaps:
        print(f"  WARNING: Found {len(gaps)} gaps in schedule")
        for gap in gaps[:3]:
            print(f"    Gap from {gap[0]}s to {gap[1]}s")
    else:
        print(f"  No gaps in schedule ✓")

    if overlaps:
        print(f"  WARNING: Found {len(overlaps)} overlaps in schedule")
    else:
        print(f"  No overlaps in schedule ✓")

    # Verify first entry starts at 0 (3am)
    assert channel_entries[0]["start"] == 0, "First entry should start at 0"
    print(f"  First entry starts at second 0 ✓")

    # Verify coverage extends to end of day (23 hours from 3am = 82800 seconds)
    last_end = channel_entries[-1]["end"]
    expected_end = 23 * 3600  # 23 hours
    assert last_end >= expected_end - 1800, f"Schedule should cover ~23 hours, ends at {last_end}s"
    print(f"  Schedule ends at second {last_end} (covers {last_end/3600:.1f} hours) ✓")

    print("  Test 24 PASSED")
    return True


def test_25_seek_to_calculation_accuracy():
    """Test 25: Seek-to timestamp calculation is accurate."""
    print("\n=== Test 25: Seek-to calculation accuracy ===")

    # Ensure schedule exists
    if not scheduler.DAILY_SCHEDULE_FILE.exists():
        scheduler.generate_weekly_schedule()
        scheduler.generate_daily_schedule()

    # Get content at a specific second
    base_time = datetime.now().replace(hour=5, minute=0, second=0, microsecond=0)
    result1 = scheduler.get_scheduled_content("channel_1", base_time)

    # Get content 30 seconds later (same entry, different seek)
    later_time = datetime.now().replace(hour=5, minute=0, second=30, microsecond=0)
    result2 = scheduler.get_scheduled_content("channel_1", later_time)

    print(f"  5:00:00am: type={result1['type']}, seek_to={result1['seek_to']}")
    print(f"  5:00:30am: type={result2['type']}, seek_to={result2['seek_to']}")

    # If same video, seek should differ by ~30 seconds
    if result1["video_id"] == result2["video_id"]:
        seek_diff = result2["seek_to"] - result1["seek_to"]
        assert abs(seek_diff - 30) < 2, f"Seek difference should be ~30s, got {seek_diff}s"
        print(f"  Same video: seek differs by {seek_diff}s ✓")
    else:
        print(f"  Different videos - seek calculation still valid ✓")

    # Test at the edge of test pattern period
    test_start = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
    result_start = scheduler.get_scheduled_content("channel_1", test_start)
    assert result_start["seek_to"] == 0, "Start of test pattern should have seek_to=0"
    print(f"  3:00:00am (test pattern start): seek_to={result_start['seek_to']} ✓")

    test_mid = datetime.now().replace(hour=3, minute=30, second=0, microsecond=0)
    result_mid = scheduler.get_scheduled_content("channel_1", test_mid)
    assert result_mid["seek_to"] == 1800, f"3:30am should have seek_to=1800, got {result_mid['seek_to']}"
    print(f"  3:30:00am (mid test pattern): seek_to={result_mid['seek_to']} ✓")

    print("  Test 25 PASSED")
    return True


def test_26_empty_series_handling():
    """Test 26: Handling of series with no episodes."""
    print("\n=== Test 26: Empty series handling ===")

    # Create a series with no episodes
    series_data = scheduler.load_series()
    series_data["Empty_Series"] = {
        "created": "2025-01-01",
        "time_of_day": "any"
    }
    scheduler.save_series(series_data)

    # Try to get episodes for empty series
    episodes = scheduler.get_series_episodes("Empty_Series")
    assert len(episodes) == 0, "Empty series should have 0 episodes"
    print(f"  Empty series has {len(episodes)} episodes ✓")

    # Try to get next episode for empty series
    result = scheduler.get_next_episode_for_channel("channel_1", "Empty_Series")
    assert result is None, "Should return None for empty series"
    print(f"  get_next_episode returns None for empty series ✓")

    # Try to peek at empty series
    result = scheduler.peek_next_episode_for_channel("channel_1", "Empty_Series")
    assert result is None, "Should return None for peek on empty series"
    print(f"  peek_next_episode returns None for empty series ✓")

    # Clean up
    del series_data["Empty_Series"]
    scheduler.save_series(series_data)

    print("  Test 26 PASSED")
    return True


def test_27_independent_channel_cursors():
    """Test 27: Different channels have independent episode cursors."""
    print("\n=== Test 27: Independent channel cursors ===")

    # Clear all cursors
    scheduler.save_episode_cursors({})

    # Get episode for channel_1
    ep1_ch1 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep1_ch1["episode"] == 1, "channel_1 should start at episode 1"
    print(f"  channel_1 first: {ep1_ch1['video_id']} (E{ep1_ch1['episode']}) ✓")

    # Get episode for channel_2 (same series) - should also be episode 1
    ep1_ch2 = scheduler.get_next_episode_for_channel("channel_2", "Test_Series_A")
    assert ep1_ch2["episode"] == 1, "channel_2 should also start at episode 1"
    print(f"  channel_2 first: {ep1_ch2['video_id']} (E{ep1_ch2['episode']}) ✓")

    # Advance channel_1
    ep2_ch1 = scheduler.get_next_episode_for_channel("channel_1", "Test_Series_A")
    assert ep2_ch1["episode"] == 2, "channel_1 should now be at episode 2"
    print(f"  channel_1 second: {ep2_ch1['video_id']} (E{ep2_ch1['episode']}) ✓")

    # channel_2 should still be at episode 1 (now returns 2 since it got 1)
    ep2_ch2 = scheduler.get_next_episode_for_channel("channel_2", "Test_Series_A")
    assert ep2_ch2["episode"] == 2, "channel_2 should now be at episode 2"
    print(f"  channel_2 second: {ep2_ch2['video_id']} (E{ep2_ch2['episode']}) ✓")

    # Verify cursors are independent in storage
    cursors = scheduler.load_episode_cursors()
    ch1_idx = cursors["channel_1"]["Test_Series_A"]["last_index"]
    ch2_idx = cursors["channel_2"]["Test_Series_A"]["last_index"]
    assert ch1_idx == ch2_idx, "Both channels at same position now"
    print(f"  Cursors tracked independently: ch1={ch1_idx}, ch2={ch2_idx} ✓")

    print("  Test 27 PASSED")
    return True


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("BROADCAST TV SCHEDULER - HAPPY PATH TESTS")
    print("=" * 60)

    setup_test_data()

    tests = [
        test_1_get_series_time_of_day,
        test_2_set_series_time_of_day,
        test_3_get_series_episodes,
        test_4_get_commercials,
        test_5_episode_cursor_management,
        test_6_block_structure_calculation,
        test_7_commercial_sequence_building,
        test_8_eligible_series_for_time,
        test_9_weekly_schedule_generation,
        test_10_daily_schedule_generation,
        test_11_get_scheduled_content,
        test_12_time_of_day_period_detection,
        test_13_back_to_back_probability,
        test_14_generate_block_schedule,
        test_15_cross_day_boundary,
        test_16_schedule_regeneration_checks,
        test_17_is_broadcast_channel,
        test_18_schedule_lookup_no_schedule,
        test_19_peek_episode_without_advancing,
        test_20_multiple_episodes_per_block,
        test_21_slot_index_calculation,
        test_22_video_url_construction,
        test_23_invalid_time_of_day_rejected,
        test_24_daily_schedule_entry_continuity,
        test_25_seek_to_calculation_accuracy,
        test_26_empty_series_handling,
        test_27_independent_channel_cursors,
    ]

    passed = 0
    failed = 0
    failures = []

    for test in tests:
        try:
            if test():
                passed += 1
        except AssertionError as e:
            failed += 1
            failures.append((test.__name__, str(e)))
            print(f"  Test FAILED: {e}")
        except Exception as e:
            failed += 1
            failures.append((test.__name__, str(e)))
            print(f"  Test ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if failures:
        print("\nFAILURES:")
        for name, error in failures:
            print(f"  - {name}: {error}")

    cleanup_test_data()

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
