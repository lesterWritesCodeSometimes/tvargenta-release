#!/usr/bin/env python3
"""
Analyze loudness (LUFS) for all videos in the TVArgenta library.
Run once to populate loudness_lufs metadata for existing videos.

Usage:
    python analyze_loudness.py           # Analyze videos missing loudness data
    python analyze_loudness.py --all     # Re-analyze all videos
    python analyze_loudness.py --status  # Just show status, don't analyze
"""

import json
import subprocess
import sys
from pathlib import Path

# Paths
CONTENT_DIR = Path(__file__).parent / "content"
VIDEO_DIR = CONTENT_DIR / "videos"
METADATA_FILE = CONTENT_DIR / "metadata.json"


def load_metadata():
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_metadata(metadata):
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def analyze_loudness(filepath):
    """
    Analyze audio loudness using FFmpeg's ebur128 filter.
    Returns integrated loudness in LUFS, or None if analysis fails.
    """
    try:
        result = subprocess.run([
            "ffmpeg", "-i", filepath,
            "-af", "ebur128=framelog=verbose",
            "-f", "null", "-"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)

        # Parse integrated loudness from stderr
        for line in result.stderr.split('\n'):
            line = line.strip()
            if line.startswith('I:') and 'LUFS' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == 'LUFS' and i > 0:
                        try:
                            return float(parts[i-1])
                        except ValueError:
                            continue
        return None

    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT (video too long?)")
        return None
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


def get_video_path(video_id, info):
    """Determine the file path for a video based on its metadata."""
    if info.get("commercials_path"):
        return VIDEO_DIR / f"{info['commercials_path']}.mp4"
    elif info.get("series_path"):
        return VIDEO_DIR / f"{info['series_path']}.mp4"
    else:
        return VIDEO_DIR / f"{video_id}.mp4"


def show_status(metadata):
    """Display analysis status."""
    total = len(metadata)
    analyzed = sum(1 for info in metadata.values() if info.get("loudness_lufs") is not None)
    pending = total - analyzed

    print(f"\nLoudness Analysis Status")
    print(f"========================")
    print(f"Total videos:    {total}")
    print(f"Analyzed:        {analyzed}")
    print(f"Pending:         {pending}")

    # By category
    by_category = {}
    for video_id, info in metadata.items():
        cat = info.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {"total": 0, "analyzed": 0}
        by_category[cat]["total"] += 1
        if info.get("loudness_lufs") is not None:
            by_category[cat]["analyzed"] += 1

    if by_category:
        print(f"\nBy category:")
        for cat, counts in sorted(by_category.items()):
            print(f"  {cat}: {counts['analyzed']}/{counts['total']}")

    return pending


def main():
    reanalyze_all = "--all" in sys.argv
    status_only = "--status" in sys.argv

    metadata = load_metadata()

    if not metadata:
        print("No videos found in metadata.")
        return

    pending = show_status(metadata)

    if status_only:
        return

    if pending == 0 and not reanalyze_all:
        print("\nAll videos already analyzed!")
        return

    # Build list of videos to analyze
    if reanalyze_all:
        to_analyze = list(metadata.keys())
        print(f"\nRe-analyzing all {len(to_analyze)} videos...")
    else:
        to_analyze = [
            vid for vid, info in metadata.items()
            if info.get("loudness_lufs") is None
        ]
        print(f"\nAnalyzing {len(to_analyze)} videos...")

    print("(This may take a while - ~1 minute per 30 min of video)\n")

    processed = 0
    errors = 0

    for i, video_id in enumerate(to_analyze, 1):
        info = metadata[video_id]
        filepath = get_video_path(video_id, info)

        category = info.get("category", "unknown")
        print(f"[{i}/{len(to_analyze)}] {video_id} ({category})...", end=" ", flush=True)

        if not filepath.exists():
            print(f"FILE NOT FOUND: {filepath}")
            errors += 1
            continue

        lufs = analyze_loudness(str(filepath))

        if lufs is not None:
            metadata[video_id]["loudness_lufs"] = lufs
            print(f"{lufs:.1f} LUFS")
            processed += 1

            # Save after each successful analysis (in case of interruption)
            save_metadata(metadata)
        else:
            print("FAILED")
            errors += 1

    print(f"\nDone!")
    print(f"  Processed: {processed}")
    print(f"  Errors:    {errors}")


if __name__ == "__main__":
    main()
