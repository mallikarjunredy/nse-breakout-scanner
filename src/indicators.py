"""Technical indicator helpers used by the "Upside Buy Movement" strategy.

All functions operate on a single ticker's OHLCV DataFrame (columns:
Open, High, Low, Close, Volume; ascending date index) or a plain Close/
Volume Series, and look only at the most recent bar as "today". No
Streamlit or I/O in this module -- plain DataFrames/Series in, plain
values out -- so it stays easily testable in isolation.
"""

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


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Standard exponential moving average (span=period)."""
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range: an EWM of the True Range (the largest
    of today's High-Low, |High - yesterday's Close|, |Low - yesterday's
    Close|), smoothing out single-day gaps.
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def compute_macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Standard MACD histogram: (EMA-fast - EMA-slow) minus its own
    `signal`-period EMA (the "signal line"). Positive/rising values mean
    upward momentum is building.
    """
    macd_line = compute_ema(close, fast) - compute_ema(close, slow)
    signal_line = compute_ema(macd_line, signal)
    return macd_line - signal_line
