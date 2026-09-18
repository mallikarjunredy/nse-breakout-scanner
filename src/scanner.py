"""Shared market-data utilities: bulk OHLCV download and per-ticker
company-info lookup. These are generic building blocks used by whichever
strategy module needs them (currently `src/pre_breakout.py`) -- this
module deliberately holds no strategy logic of its own.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from . import config


def download_history(tickers: list[str]) -> dict:
    """Bulk-downloads daily OHLCV for all tickers and returns a dict of
    ticker -> per-ticker DataFrame (only tickers with usable data included).
    """
    raw = yf.download(
        tickers,
        period=config.HISTORY_PERIOD,
        interval=config.HISTORY_INTERVAL,
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )

    result = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker].dropna(subset=["Close", "High", "Volume"])
            if not df.empty:
                result[ticker] = df
    else:
        # yf.download collapses to a single-level frame when only one
        # ticker was requested.
        df = raw.dropna(subset=["Close", "High", "Volume"])
        if not df.empty and tickers:
            result[tickers[0]] = df
    return result


def fetch_candidate_info(tickers: list[str]) -> dict:
    """For a (small) list of stocks, fetches P/E ratio, company name, and
    sector in parallel. This is the one place that calls yfinance's slow
    per-ticker `.info` -- never run it across a whole universe.
    """
    info_by_ticker = {}

    def fetch_one(t):
        try:
            info = yf.Ticker(t).get_info()
            return t, {
                "pe": info.get("trailingPE") or info.get("forwardPE"),
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
            }
        except Exception:
            return t, {"pe": None, "name": None, "sector": None}

    with ThreadPoolExecutor(max_workers=config.PE_FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch_one, t) for t in tickers]
        for fut in as_completed(futures):
            t, data = fut.result()
            info_by_ticker[t] = data

    return info_by_ticker
