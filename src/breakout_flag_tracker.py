"""Persistent forward-performance tracker for Breakout Flag Continuation
picks (data/breakout_flag_tracker.json) -- added at the user's request,
explicitly for testing/validation: "as we're choosing these stocks
today, how do they actually perform going forward." A plain price-
since-entry log, not a backtest or paper-trading engine (see Trend +
Consolidation's own backtest for that, with simulated stops/targets/
position sizing) -- this only remembers the signal date and entry
price for whichever matches the user chooses to snapshot, and reports
how price has moved since, using a fresh quote each time it's viewed.

The "Signal Date" saved here is the strategy's own Signal Date field --
already the EOD date of the candle that qualified (yesterday's close,
from the user's "before market open" framing, since a scan run before
today's session opens is necessarily evaluating yesterday's completed
candle), not the calendar date the user happened to click the button.
"""

import datetime
import json
import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, scanner

_IST = ZoneInfo("Asia/Kolkata")
_PATH = config.DATA_DIR / "breakout_flag_tracker.json"

_PERFORMANCE_COLUMNS = [
    "Ticker", "Company Name", "Setup Status", "Signal Date", "Entry Price",
    "Current Price", "Change %", "Days Since Signal", "Tracked Since",
]


def load() -> list[dict]:
    if not _PATH.exists():
        return []
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add_matches(rows: list[dict]) -> int:
    """Adds one tracked entry per row (each a dict with at least Ticker/
    Setup Status/Signal Date/Current Price, e.g. a row from any of the
    strategy's three result tables). Skips a (ticker, signal_date) pair
    that's already tracked, so clicking the button again on the same
    day's scan doesn't duplicate entries. Returns how many were
    actually added (as opposed to skipped as duplicates).
    """
    entries = load()
    existing_keys = {(e["ticker"], e["signal_date"]) for e in entries}
    now_ts = time.time()
    added = 0
    for row in rows:
        key = (row["Ticker"], row["Signal Date"])
        if key in existing_keys:
            continue
        entries.append({
            "ticker": row["Ticker"],
            "company_name": row.get("Company Name") or row["Ticker"],
            "setup_status": row["Setup Status"],
            "signal_date": row["Signal Date"],
            "entry_price": float(row["Current Price"]),
            "added_at": now_ts,
            "added_date_ist": datetime.datetime.fromtimestamp(now_ts, tz=_IST).strftime("%d %b %Y %I:%M %p IST"),
        })
        existing_keys.add(key)
        added += 1
    _save(entries)
    return added


def clear() -> None:
    _save([])


def get_performance() -> pd.DataFrame:
    """Fetches one fresh current price per tracked ticker and returns a
    row per tracked entry with the price change since entry. A ticker
    whose fresh price can't be fetched right now shows N/A for Current
    Price/Change % rather than a stale or fabricated number.
    """
    entries = load()
    if not entries:
        return pd.DataFrame(columns=_PERFORMANCE_COLUMNS)

    tickers = sorted({e["ticker"] for e in entries})
    history = scanner.download_history(tickers)
    today = datetime.datetime.now(_IST).date()

    rows = []
    for e in entries:
        df = history.get(e["ticker"])
        current_price = float(df["Close"].iloc[-1]) if df is not None and len(df) else None
        entry_price = float(e["entry_price"])
        change_pct = (
            round((current_price - entry_price) / entry_price * 100, 2)
            if current_price is not None and entry_price else None
        )
        try:
            signal_date_obj = datetime.datetime.strptime(e["signal_date"], "%Y-%m-%d").date()
            days_since = (today - signal_date_obj).days
        except Exception:
            days_since = None
        rows.append({
            "Ticker": e["ticker"],
            "Company Name": e.get("company_name") or e["ticker"],
            "Setup Status": e["setup_status"],
            "Signal Date": e["signal_date"],
            "Entry Price": round(entry_price, 2),
            "Current Price": round(current_price, 2) if current_price is not None else None,
            "Change %": change_pct,
            "Days Since Signal": days_since,
            "Tracked Since": e.get("added_date_ist", "N/A"),
        })
    return pd.DataFrame(rows, columns=_PERFORMANCE_COLUMNS).sort_values(
        "Signal Date", ascending=False
    ).reset_index(drop=True)
