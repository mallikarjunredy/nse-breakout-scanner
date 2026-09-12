"""Benchmark index snapshot for the Market Overview section. Advance/
decline counts are NOT computed here -- they come from scan_market's
actual scanned universe (src/scanner.py) so they're never fabricated or
inconsistent with what was really scanned.
"""

import yfinance as yf

from . import config


def get_index_snapshot(market: str) -> dict | None:
    """Returns {name, level, change_pct} for the market's benchmark index,
    or None if the data isn't available right now. Never fabricates a value.
    """
    ticker = config.BENCHMARK_INDEX.get(market)
    name = config.BENCHMARK_NAME.get(market)
    if not ticker:
        return None

    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="1d")
    except Exception:
        return None

    if hist is None or len(hist) < 2:
        return None

    last_close = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2])
    if prev_close == 0:
        return None

    change_pct = (last_close - prev_close) / prev_close * 100
    return {"name": name, "level": last_close, "change_pct": change_pct}
