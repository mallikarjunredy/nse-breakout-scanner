"""Persistent log of past scans (data/scan_history.json), so users can
see what was scanned and when, across sessions -- not just the latest run.
"""

import datetime
import json
import time
from zoneinfo import ZoneInfo

from . import config

_IST = ZoneInfo("Asia/Kolkata")

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


def record(result: dict, universe_label: str = "Nifty 500") -> None:
    """Appends one Upside Buy Movement scan's summary to the history log
    (either the Nifty 500 or the "above 100" all-NSE variant, identified
    by `universe_label`), keeping only the most recent
    config.SCAN_HISTORY_MAX_ENTRIES entries across both. Older entries
    (from before this app had only one strategy) may carry different
    fields -- callers reading history back should use `.get(...)` with a
    default rather than assuming every field exists.
    """
    entries = load()
    now_ts = time.time()
    universe_size = result.get("universe_size") or 0
    scanned = result.get("scanned") or 0
    entries.append({
        "timestamp": now_ts,
        # IST explicitly -- the server's local time (e.g. a cloud host in
        # UTC) is not necessarily IST, and NSE data is IST-relevant.
        "date": datetime.datetime.fromtimestamp(now_ts, tz=_IST).strftime("%d %b %Y %I:%M %p IST"),
        "market": universe_label,
        "universe_size": universe_size,
        "scanned": scanned,
        "failures": max(0, universe_size - scanned),
        "near_resistance": len(result.get("near_resistance", [])),
        "consolidating": len(result.get("consolidating", [])),
        "already_broken_out": len(result.get("already_broken_out", [])),
    })
    entries = entries[-config.SCAN_HISTORY_MAX_ENTRIES:]

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
