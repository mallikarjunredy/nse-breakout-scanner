"""Technical indicator and resistance/breakout detection helpers.

All functions operate on a single ticker's OHLCV DataFrame (columns:
Open, High, Low, Close, Volume; ascending date index) and look only at
the most recent bar as "today".
"""

import numpy as np
import pandas as pd

from . import config


def compute_rsi(close: pd.Series, period: int = config.RSI_PERIOD) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def compute_volume_ratio(volume: pd.Series, avg_period: int = config.VOLUME_AVG_PERIOD) -> float:
    """Today's volume divided by the average of the prior `avg_period` days."""
    if len(volume) < avg_period + 1:
        return np.nan
    avg_vol = volume.iloc[-(avg_period + 1):-1].mean()
    today_vol = volume.iloc[-1]
    if not avg_vol or np.isnan(avg_vol):
        return np.nan
    return float(today_vol / avg_vol)


def is_uptrend(df: pd.DataFrame, sma_fast: int = config.SMA_FAST, sma_slow: int = config.SMA_SLOW) -> bool:
    """True if price is in a confirmed uptrend (above its fast SMA, with
    the fast SMA above the slow SMA when enough history exists). Defaults
    to the daily 50/200 convention; pass config.WEEKLY_SMA_FAST/SLOW for a
    weekly-bar evaluation.
    """
    close = df["Close"]
    if len(close) < sma_fast:
        return False
    sma_fast_val = close.rolling(sma_fast).mean().iloc[-1]
    price = close.iloc[-1]
    if len(close) >= sma_slow:
        sma_slow_val = close.rolling(sma_slow).mean().iloc[-1]
        return bool(price > sma_fast_val > sma_slow_val)
    return bool(price > sma_fast_val)


def find_resistance_signals(
    df: pd.DataFrame,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
    periods: list[str] | None = None,
    lookbacks: dict | None = None,
) -> list[dict]:
    """For each configured lookback window, compares today's close to the
    highest High of the prior N bars (today excluded) and flags whether
    today is a breakout above, or "near" (within near_pct) that level.

    `periods`, if given, restricts the check to just those lookback labels
    (e.g. ["1M", "3M"]) instead of the full set -- the "Breakout Period"
    scan parameter. `lookbacks` selects which label->bar-count mapping to
    use at all: defaults to config.RESISTANCE_LOOKBACKS (daily bars, bar
    count = trading days); pass config.WEEKLY_RESISTANCE_LOOKBACKS when
    `df` holds weekly-resampled bars (bar count = weeks).

    Returns a list of dicts: {period, type ("breakout"|"near"), resistance, pct}
    where pct is the % close is above (breakout) or below (near) the level.
    """
    signals = []
    close = df["Close"].iloc[-1]
    high = df["High"]
    lookbacks = lookbacks if lookbacks is not None else config.RESISTANCE_LOOKBACKS
    if periods:
        lookbacks = {label: days for label, days in lookbacks.items() if label in periods}
    for label, period in lookbacks.items():
        if len(high) < period + 1:
            continue
        prior_high = high.iloc[-(period + 1):-1].max()
        if not prior_high or np.isnan(prior_high) or prior_high <= 0:
            continue
        if close > prior_high:
            pct = (close - prior_high) / prior_high * 100
            signals.append({"period": label, "period_days": period, "type": "breakout",
                             "resistance": float(prior_high), "pct": float(pct)})
        elif close >= prior_high * (1 - near_pct):
            pct = (prior_high - close) / prior_high * 100
            signals.append({"period": label, "period_days": period, "type": "near",
                             "resistance": float(prior_high), "pct": float(pct)})
    return signals


def classify(signals: list[dict]) -> tuple[str | None, dict | None]:
    """Picks an overall category and the single "best" signal to display.

    Breakout takes priority over near-breakout. Among breakouts, the
    longest lookback period wins (a breakout above a 3-year high is more
    significant than one above a 5-day high). Among near-breakouts, the
    one closest to triggering (smallest pct) wins.
    """
    breakouts = [s for s in signals if s["type"] == "breakout"]
    if breakouts:
        best = max(breakouts, key=lambda s: s["period_days"])
        return "Breakout", best

    nears = [s for s in signals if s["type"] == "near"]
    if nears:
        best = min(nears, key=lambda s: s["pct"])
        return "Near Breakout", best

    return None, None


def compute_52w_high(df: pd.DataFrame, period: int = 252) -> float:
    """Highest High over the trailing `period` trading days (~1 year),
    including today.
    """
    high = df["High"]
    window = high.iloc[-period:] if len(high) >= period else high
    return float(window.max())


def compute_quality_score(
    df: pd.DataFrame,
    category: str,
    signal_pct: float,
    near_pct: float,
    rsi: float,
    rsi_threshold: float,
    vol_ratio: float,
    volume_multiplier: float,
    sma_fast: int = config.SMA_FAST,
    sma_slow: int = config.SMA_SLOW,
) -> int:
    """Deterministic 0-100 Breakout Quality Score. Every input is a value
    already computed elsewhere in the scan (no extra data, no randomness).
    Five sub-scores, each 0-100, combined with config.QUALITY_SCORE_WEIGHTS:

    - resistance: for a Breakout, how far price has cleared the resistance
      level (more = stronger, capped at +5% = 100). For a Near Breakout,
      how close price is to triggering (closer to the level = higher,
      scaled against the configured near-breakout distance).
    - volume: today's volume ratio relative to the required minimum
      (exactly at the minimum = 50, double the minimum = 100).
    - rsi: RSI's distance from a healthy momentum sweet spot
      (rsi_threshold + 15), penalized for drifting toward overbought.
    - trend: how far price sits above its 50-day SMA and how far the
      50-day SMA sits above the 200-day SMA (uptrend strength).
    - close_strength: where today's close landed within today's
      High-Low range (near the high = buyers in control).
    """
    close = float(df["Close"].iloc[-1])
    high = float(df["High"].iloc[-1])
    low = float(df["Low"].iloc[-1])

    if category == "Breakout":
        resistance_score = max(0.0, min(100.0, signal_pct * 20))
    else:
        near_pct_pct = near_pct * 100
        resistance_score = max(0.0, 100.0 * (1 - signal_pct / near_pct_pct)) if near_pct_pct > 0 else 0.0

    volume_score = max(0.0, min(100.0, (vol_ratio / volume_multiplier) * 50)) if volume_multiplier > 0 else 0.0

    rsi_sweet_spot = rsi_threshold + 15
    rsi_score = max(0.0, min(100.0, 100 - abs(rsi - rsi_sweet_spot) * 2))

    sma_fast_val = df["Close"].rolling(sma_fast).mean().iloc[-1]
    pct_above_sma_fast = (
        (close - sma_fast_val) / sma_fast_val * 100 if pd.notna(sma_fast_val) and sma_fast_val > 0 else 0.0
    )
    if len(df) >= sma_slow:
        sma_slow_val = df["Close"].rolling(sma_slow).mean().iloc[-1]
        pct_sma_fast_above_slow = (
            (sma_fast_val - sma_slow_val) / sma_slow_val * 100 if pd.notna(sma_slow_val) and sma_slow_val > 0 else 0.0
        )
    else:
        pct_sma_fast_above_slow = 0.0
    trend_score = max(0.0, min(100.0, pct_above_sma_fast * 10 + pct_sma_fast_above_slow * 10))

    close_strength_score = (close - low) / (high - low) * 100 if high > low else 50.0

    weights = config.QUALITY_SCORE_WEIGHTS
    total = (
        weights["resistance"] * resistance_score
        + weights["volume"] * volume_score
        + weights["rsi"] * rsi_score
        + weights["trend"] * trend_score
        + weights["close_strength"] * close_strength_score
    )
    return round(max(0.0, min(100.0, total)))


def find_nearest_resistance(df: pd.DataFrame, lookbacks: dict | None = None) -> float | None:
    """Nearest resistance above today's close, across the given lookback
    windows (defaults to config.RESISTANCE_LOOKBACKS -- daily bars; pass
    config.WEEKLY_RESISTANCE_LOOKBACKS for weekly-resampled bars),
    regardless of how close it currently is. Unlike find_resistance_signals
    (which only flags near/breakout cases against a threshold), this
    always returns the closest overhead level when one exists -- used for
    display on watchlist stocks that aren't currently near or above
    resistance.
    """
    lookbacks = lookbacks if lookbacks is not None else config.RESISTANCE_LOOKBACKS
    close = df["Close"].iloc[-1]
    high = df["High"]
    candidates = []
    for _label, period in lookbacks.items():
        if len(high) < period + 1:
            continue
        prior_high = high.iloc[-(period + 1):-1].max()
        if not prior_high or np.isnan(prior_high) or prior_high <= 0:
            continue
        if prior_high > close:
            candidates.append(float(prior_high))
    if not candidates:
        return None
    return min(candidates)


def find_nearest_support(df: pd.DataFrame, lookbacks: dict | None = None) -> float | None:
    """Nearest support below today's close: the highest prior swing low
    (across the given lookback windows, same default/override rule as
    find_nearest_resistance) that still sits below the current price.
    Mirrors find_resistance_signals but on Lows instead of Highs, and
    picks the closest one below price instead of classifying breakout/near.
    """
    lookbacks = lookbacks if lookbacks is not None else config.RESISTANCE_LOOKBACKS
    close = df["Close"].iloc[-1]
    low = df["Low"]
    candidates = []
    for _label, period in lookbacks.items():
        if len(low) < period + 1:
            continue
        prior_low = low.iloc[-(period + 1):-1].min()
        if not prior_low or np.isnan(prior_low) or prior_low <= 0:
            continue
        if prior_low < close:
            candidates.append(float(prior_low))
    if not candidates:
        return None
    return max(candidates)
