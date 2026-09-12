"""Persistent log of past scans (data/scan_history.json), so users can
see what was scanned and when, across sessions -- not just the latest run.
"""

import json
import time

from . import config

_PATH = config.DATA_DIR / "scan_history.json"


def load() -> list[dict]:
    if not _PATH.exists():
        return []
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record(market_label: str, result: dict) -> None:
    """Appends one scan's summary to the history log, keeping only the
    most recent config.SCAN_HISTORY_MAX_ENTRIES entries.
    """
    entries = load()
    entries.append({
        "timestamp": time.time(),
        "date": time.strftime("%d %b %Y %H:%M:%S"),
        "market": market_label,
        "universe_size": result.get("universe_size"),
        "scanned": result.get("scanned"),
        "eligible": result.get("eligible"),
        "breakouts": len(result.get("breakout", [])),
        "near_breakouts": len(result.get("near_breakout", [])),
        "weekly_breakouts": len(result.get("weekly_breakout", [])),
        "weekly_near_breakouts": len(result.get("weekly_near_breakout", [])),
    })
    entries = entries[-config.SCAN_HISTORY_MAX_ENTRIES:]

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
