"""Forecast Accuracy History for the Global News Impact dashboard.

Records each directional call (Positive/Negative/Mixed/Unclear) made by
`news_impact.assess_stock_impact()` *before* its target trading
session, then -- once that session's real data exists -- evaluates it
against the actual close-to-close return. This log starts empty: there
is no historical track record to show yet, which is the honest state
the written spec itself asks for ("if evidence is insufficient...")
rather than a fabricated backtest-style accuracy percentage.

Point-in-time discipline: `evaluate_pending()` only resolves an entry
once `target_session` is strictly in the past relative to the latest
price bar available, and reads only that session's own OHLC (and the
prior session's close, to compute the return) -- never any later data.
"Mixed"/"Unclear" directional calls are recorded and scored as neither
correct nor incorrect (there's no single predicted sign to check them
against); only Positive/Negative calls count toward directional
accuracy.

v1 scope: directional accuracy only. There is no numeric % forecast in
this phase (see news_impact.NO_PERCENT_FORECAST_MSG), so there is no
mean-absolute-error or prediction-range-coverage statistic to compute
yet -- those fields are left for a later phase once a validated
numeric model exists.
"""

import json
import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, scanner

IST = ZoneInfo("Asia/Kolkata")
_PATH = config.DATA_DIR / "forecast_log.json"


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


def record_forecast(ticker: str, direction: str, confidence: str, target_session: str, context: str) -> bool:
    """Records one forecast ahead of `target_session` (YYYY-MM-DD).
    Skips (returns False) if this exact (ticker, target_session) pair
    is already logged, so re-rendering the page doesn't duplicate
    entries -- mirrors breakout_flag_tracker.add_matches()'s own
    dedup-on-click pattern.
    """
    entries = load()
    key = (ticker, target_session)
    if any((e["ticker"], e["target_session"]) == key for e in entries):
        return False
    entries.append({
        "id": f"{ticker}_{target_session}_{time.time()}",
        "ticker": ticker, "direction": direction, "confidence": confidence,
        "target_session": target_session, "context": context,
        "recorded_at": time.time(),
        "recorded_at_ist": datetime_now_ist_str(),
        "status": "pending", "actual_return_pct": None, "correct": None,
    })
    _save(entries)
    return True


def datetime_now_ist_str() -> str:
    import datetime
    return datetime.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def evaluate_pending() -> int:
    """Resolves every "pending" entry whose target session now has real
    completed price data, by comparing that session's close to the
    prior session's close. Only Positive/Negative calls are scored as
    correct/incorrect; Mixed/Unclear are marked "evaluated" with
    `correct=None` (not applicable). Returns how many entries were
    newly evaluated.
    """
    import datetime

    entries = load()
    pending = [e for e in entries if e["status"] == "pending"]
    if not pending:
        return 0

    tickers = sorted({e["ticker"] for e in pending})
    history = scanner.download_history(tickers)
    evaluated = 0

    for e in pending:
        df = history.get(e["ticker"])
        if df is None or df.empty:
            continue
        try:
            target_date = datetime.datetime.strptime(e["target_session"], "%Y-%m-%d").date()
        except Exception:
            continue

        session_dates = [d.date() for d in df.index]
        if target_date not in session_dates:
            continue  # that session hasn't happened/settled in the data yet
        idx = session_dates.index(target_date)
        if idx == 0:
            continue  # no prior close to compute a return from
        prev_close = float(df["Close"].iloc[idx - 1])
        target_close = float(df["Close"].iloc[idx])
        if prev_close <= 0:
            continue
        actual_return_pct = (target_close - prev_close) / prev_close * 100

        if e["direction"] == "Positive":
            correct = actual_return_pct > 0
        elif e["direction"] == "Negative":
            correct = actual_return_pct < 0
        else:
            correct = None  # Mixed/Unclear -- not applicable

        e["status"] = "evaluated"
        e["actual_return_pct"] = round(actual_return_pct, 2)
        e["correct"] = correct
        evaluated += 1

    if evaluated:
        _save(entries)
    return evaluated


def get_accuracy_summary() -> dict:
    """Directional accuracy over every evaluated Positive/Negative
    call. Returns sample_size=0 with a plain "no evaluated forecasts
    yet" state until real history accumulates -- never a fabricated
    percentage.
    """
    entries = load()
    scored = [e for e in entries if e["status"] == "evaluated" and e["correct"] is not None]
    if not scored:
        return {"sample_size": 0, "directional_accuracy_pct": None, "pending_count": sum(1 for e in entries if e["status"] == "pending")}
    correct = sum(1 for e in scored if e["correct"])
    return {
        "sample_size": len(scored),
        "directional_accuracy_pct": round(correct / len(scored) * 100, 1),
        "pending_count": sum(1 for e in entries if e["status"] == "pending"),
    }


def get_log_table() -> pd.DataFrame:
    entries = load()
    if not entries:
        return pd.DataFrame(columns=["Ticker", "Direction", "Confidence", "Target Session", "Status",
                                      "Actual Return %", "Correct", "Recorded"])
    rows = [{
        "Ticker": e["ticker"], "Direction": e["direction"], "Confidence": e["confidence"],
        "Target Session": e["target_session"], "Status": e["status"],
        "Actual Return %": e["actual_return_pct"],
        "Correct": e["correct"], "Recorded": e.get("recorded_at_ist", "N/A"),
    } for e in entries]
    return pd.DataFrame(rows).sort_values("Target Session", ascending=False).reset_index(drop=True)
