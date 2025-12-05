#!/usr/bin/env python3
"""
VCR State Manager for TVArgenta

Handles all VCR state management including:
- Runtime state (tape inserted, position, pause, rewind)
- Persistent tape registry (NFC UID to video mapping)
- Position persistence across sessions
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from settings import (
    VCR_STATE_FILE,
    VCR_TRIGGER_FILE,
    TAPES_FILE,
    METADATA_FILE,
    CONTENT_DIR,
)

# Rewind speed: 2 minutes (120 sec) per 90 minutes (5400 sec) of playback
# Formula: rewind_duration = position_sec / 45
REWIND_SEC_PER_PLAYBACK_SEC = 45.0  # 45 seconds of playback = 1 second of rewind
MIN_REWIND_DURATION_SEC = 36.0  # Minimum to match rewind audio intro + outro duration

# How often to persist position to disk (seconds)
POSITION_PERSIST_INTERVAL = 30.0


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON atomically using temp file + rename."""
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def _read_json(path: Path, default: dict = None) -> dict:
    """Read JSON file, returning default if missing or invalid."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


# -----------------------------------------------------------------------------
# Runtime VCR State (in /tmp, volatile)
# -----------------------------------------------------------------------------

def get_default_vcr_state() -> dict:
    """Return a default VCR state structure."""
    return {
        "reader_attached": False,
        "tape_inserted": False,
        "tape_uid": None,
        "video_id": None,
        "title": None,
        "duration_sec": 0.0,
        "position_sec": 0.0,
        "is_paused": False,
        "is_rewinding": False,
        "rewind_started_at": None,
        "rewind_duration_sec": 0.0,  # Calculated based on position
        "rewind_original_position": 0.0,  # Position when rewind started
        "unknown_tape_uid": None,  # For unregistered tapes
        "updated_at": datetime.now().isoformat(),
    }


def load_vcr_state() -> dict:
    """Load current VCR state from /tmp."""
    return _read_json(VCR_STATE_FILE, get_default_vcr_state())


def save_vcr_state(state: dict) -> None:
    """Save VCR state to /tmp."""
    state["updated_at"] = datetime.now().isoformat()
    _write_json_atomic(VCR_STATE_FILE, state)


def trigger_vcr_update() -> None:
    """Touch the VCR trigger file to notify frontend of state change."""
    _write_json_atomic(VCR_TRIGGER_FILE, {"timestamp": time.time()})


def clear_stale_vcr_state() -> None:
    """
    Clear any stale VCR state on app startup.
    Resets rewind/pause states that may have persisted from a previous session.
    """
    state = load_vcr_state()
    changed = False

    # Clear any in-progress rewind (can't resume a rewind across restarts)
    if state.get("is_rewinding"):
        state["is_rewinding"] = False
        state["rewind_started_at"] = None
        state["rewind_duration_sec"] = 0.0
        state["rewind_original_position"] = 0.0
        changed = True

    # Clear pause state (tape needs to be re-inserted anyway)
    if state.get("is_paused"):
        state["is_paused"] = False
        changed = True

    # Clear tape state (NFC reader will re-detect if tape is present)
    if state.get("tape_inserted"):
        state["tape_inserted"] = False
        state["tape_uid"] = None
        state["video_id"] = None
        state["title"] = None
        state["duration_sec"] = 0.0
        state["position_sec"] = 0.0
        changed = True

    if changed:
        save_vcr_state(state)


def set_reader_attached(attached: bool) -> None:
    """Update whether NFC reader is physically attached."""
    state = load_vcr_state()
    if state["reader_attached"] != attached:
        state["reader_attached"] = attached
        if not attached:
            # Reader unplugged - clear tape state too
            state["tape_inserted"] = False
            state["tape_uid"] = None
            state["video_id"] = None
            state["title"] = None
            state["unknown_tape_uid"] = None
        save_vcr_state(state)
        trigger_vcr_update()


def set_tape_inserted(uid: str, video_id: str, title: str,
                      duration_sec: float, position_sec: float) -> None:
    """Update state when a registered tape is inserted."""
    state = load_vcr_state()
    state["tape_inserted"] = True
    state["tape_uid"] = uid
    state["video_id"] = video_id
    state["title"] = title
    state["duration_sec"] = duration_sec
    state["position_sec"] = position_sec
    state["is_paused"] = False
    state["is_rewinding"] = False
    state["rewind_started_at"] = None
    state["rewind_duration_sec"] = 0.0
    state["rewind_original_position"] = 0.0
    state["unknown_tape_uid"] = None
    save_vcr_state(state)
    trigger_vcr_update()


def set_unknown_tape(uid: str) -> None:
    """Update state when an unregistered tape is detected."""
    state = load_vcr_state()
    state["tape_inserted"] = False
    state["tape_uid"] = None
    state["video_id"] = None
    state["title"] = None
    state["unknown_tape_uid"] = uid
    save_vcr_state(state)
    trigger_vcr_update()


def set_tape_removed() -> None:
    """Update state when tape is removed. Position is persisted by caller."""
    state = load_vcr_state()

    # Save position before clearing
    if state["tape_uid"] and state["tape_inserted"]:
        save_tape_position(state["tape_uid"], state["position_sec"])

    state["tape_inserted"] = False
    state["tape_uid"] = None
    state["video_id"] = None
    state["title"] = None
    state["duration_sec"] = 0.0
    state["position_sec"] = 0.0
    state["is_paused"] = False
    state["is_rewinding"] = False
    state["rewind_started_at"] = None
    state["rewind_duration_sec"] = 0.0
    state["rewind_original_position"] = 0.0
    state["unknown_tape_uid"] = None
    save_vcr_state(state)
    trigger_vcr_update()


def toggle_pause() -> bool:
    """Toggle pause state. Returns new pause state."""
    state = load_vcr_state()
    if not state["tape_inserted"] or state["is_rewinding"]:
        return state.get("is_paused", False)

    state["is_paused"] = not state["is_paused"]
    save_vcr_state(state)
    trigger_vcr_update()
    return state["is_paused"]


def calculate_rewind_duration(position_sec: float) -> float:
    """
    Calculate how long rewind should take based on current position.
    Formula: 2 minutes per 90 minutes of playback (position_sec / 45).
    """
    duration = position_sec / REWIND_SEC_PER_PLAYBACK_SEC
    return max(MIN_REWIND_DURATION_SEC, duration)


def start_rewind() -> bool:
    """Start the rewind process. Returns True if started."""
    state = load_vcr_state()
    if not state["tape_inserted"] or state["is_rewinding"]:
        return False

    position = state.get("position_sec", 0.0)
    rewind_duration = calculate_rewind_duration(position)

    state["is_paused"] = False
    state["is_rewinding"] = True
    state["rewind_started_at"] = time.time()
    state["rewind_duration_sec"] = rewind_duration
    state["rewind_original_position"] = position
    save_vcr_state(state)
    trigger_vcr_update()
    return True


def check_rewind_progress() -> dict:
    """Check rewind progress. Returns progress info."""
    state = load_vcr_state()
    if not state["is_rewinding"]:
        return {"rewinding": False, "progress": 0.0, "complete": False}

    started = state.get("rewind_started_at", 0)
    elapsed = time.time() - started

    # Use stored rewind duration (calculated based on position when rewind started)
    rewind_duration = state.get("rewind_duration_sec", MIN_REWIND_DURATION_SEC)
    if rewind_duration <= 0:
        rewind_duration = MIN_REWIND_DURATION_SEC

    progress = min(1.0, elapsed / rewind_duration)

    # Calculate position based on rewind progress
    # Position decreases linearly from original_position to 0
    original_position = state.get("rewind_original_position", state.get("position_sec", 0))
    current_position = original_position * (1.0 - progress)

    complete = elapsed >= rewind_duration

    return {
        "rewinding": True,
        "progress": progress,
        "elapsed_sec": elapsed,
        "total_duration_sec": rewind_duration,
        "remaining_sec": max(0, rewind_duration - elapsed),
        "position_sec": current_position,
        "complete": complete,
    }


def complete_rewind() -> None:
    """Mark rewind as complete, reset position to 0."""
    state = load_vcr_state()
    state["is_rewinding"] = False
    state["rewind_started_at"] = None
    state["rewind_duration_sec"] = 0.0
    state["rewind_original_position"] = 0.0
    state["position_sec"] = 0.0
    state["is_paused"] = True  # Paused at start, ready to play
    save_vcr_state(state)

    # Persist position to disk
    if state["tape_uid"]:
        save_tape_position(state["tape_uid"], 0.0)

    trigger_vcr_update()


def increment_position(delta_sec: float) -> float:
    """
    Increment the tape position by delta_sec.
    Only increments if tape is playing (not paused, not rewinding).
    Returns the new position.
    """
    state = load_vcr_state()

    if not state["tape_inserted"]:
        return 0.0
    if state["is_paused"]:
        return state["position_sec"]
    if state["is_rewinding"]:
        return state["position_sec"]

    new_pos = state["position_sec"] + delta_sec
    duration = state.get("duration_sec", 0)

    # Cap at duration (video ended)
    if duration > 0 and new_pos >= duration:
        new_pos = duration
        state["is_paused"] = True  # Auto-pause at end

    state["position_sec"] = new_pos
    save_vcr_state(state)

    return new_pos


def seek_to_position(position_sec: float) -> float:
    """Seek to a specific position. Returns actual position."""
    state = load_vcr_state()
    if not state["tape_inserted"]:
        return 0.0

    duration = state.get("duration_sec", 0)
    position_sec = max(0.0, position_sec)
    if duration > 0:
        position_sec = min(position_sec, duration)

    state["position_sec"] = position_sec
    save_vcr_state(state)
    trigger_vcr_update()

    return position_sec


# -----------------------------------------------------------------------------
# Tape Registry (persistent in content/tapes.json)
# -----------------------------------------------------------------------------

def get_default_tapes() -> dict:
    """Return default tapes structure."""
    return {
        "tapes": {},
        "positions": {},
    }


def load_tapes() -> dict:
    """Load the tape registry from disk."""
    return _read_json(TAPES_FILE, get_default_tapes())


def save_tapes(data: dict) -> None:
    """Save the tape registry to disk."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(TAPES_FILE, data)


def register_tape(uid: str, video_id: str, title: str = None) -> dict:
    """
    Register a new tape mapping.
    Returns the tape entry.
    """
    data = load_tapes()

    # Get title from metadata if not provided
    if title is None:
        metadata = _read_json(METADATA_FILE, {})
        video_meta = metadata.get(video_id, {})
        title = video_meta.get("title", video_id)

    data["tapes"][uid] = {
        "video_id": video_id,
        "title": title,
        "registered_at": datetime.now().isoformat(),
    }

    # Initialize position to 0
    data["positions"][uid] = {
        "position_sec": 0.0,
        "updated_at": datetime.now().isoformat(),
    }

    save_tapes(data)
    return data["tapes"][uid]


def unregister_tape(uid: str) -> bool:
    """Remove a tape mapping. Returns True if removed."""
    data = load_tapes()

    removed = False
    if uid in data["tapes"]:
        del data["tapes"][uid]
        removed = True
    if uid in data["positions"]:
        del data["positions"][uid]

    if removed:
        save_tapes(data)

    return removed


def get_tape_info(uid: str) -> Optional[dict]:
    """
    Get tape info by UID.
    Returns None if not registered.
    """
    data = load_tapes()
    tape = data["tapes"].get(uid)
    if tape is None:
        return None

    # Include position
    pos_data = data["positions"].get(uid, {})
    return {
        **tape,
        "uid": uid,
        "position_sec": pos_data.get("position_sec", 0.0),
    }


def get_all_tapes() -> list:
    """Get all registered tapes with their info."""
    data = load_tapes()
    result = []

    for uid, tape in data["tapes"].items():
        pos_data = data["positions"].get(uid, {})
        result.append({
            **tape,
            "uid": uid,
            "position_sec": pos_data.get("position_sec", 0.0),
        })

    return result


def get_tape_position(uid: str) -> float:
    """Get saved position for a tape."""
    data = load_tapes()
    pos_data = data["positions"].get(uid, {})
    return pos_data.get("position_sec", 0.0)


def save_tape_position(uid: str, position_sec: float) -> None:
    """Persist tape position to disk."""
    data = load_tapes()

    if uid not in data["positions"]:
        data["positions"][uid] = {}

    data["positions"][uid]["position_sec"] = position_sec
    data["positions"][uid]["updated_at"] = datetime.now().isoformat()

    save_tapes(data)


# -----------------------------------------------------------------------------
# Video Metadata Integration
# -----------------------------------------------------------------------------

def get_video_duration(video_id: str) -> float:
    """Get video duration from metadata."""
    metadata = _read_json(METADATA_FILE, {})
    video = metadata.get(video_id, {})
    return video.get("duracion", 0.0)


def get_video_info(video_id: str) -> Optional[dict]:
    """Get video info from metadata."""
    metadata = _read_json(METADATA_FILE, {})
    return metadata.get(video_id)


# -----------------------------------------------------------------------------
# Position Tracking State (for background thread)
# -----------------------------------------------------------------------------

_last_position_persist_time = 0.0


def should_persist_position() -> bool:
    """Check if enough time has passed to persist position."""
    global _last_position_persist_time
    now = time.time()
    if now - _last_position_persist_time >= POSITION_PERSIST_INTERVAL:
        return True
    return False


def mark_position_persisted() -> None:
    """Mark that position was just persisted."""
    global _last_position_persist_time
    _last_position_persist_time = time.time()


def persist_current_position() -> None:
    """Persist current position to tapes.json if tape is inserted."""
    state = load_vcr_state()
    if state["tape_inserted"] and state["tape_uid"]:
        save_tape_position(state["tape_uid"], state["position_sec"])
        mark_position_persisted()
