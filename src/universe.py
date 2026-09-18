"""Loads NSE ticker universes, with on-disk caching so the app doesn't
have to hit NSE's archives on every run. Two universes are supported:
Nifty 500, and a broader "all NSE stocks" list used by the "Upside Buy
Movement above 100" strategy.
"""

import io
import time

import pandas as pd
import requests

from . import config

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 1 week

# Small fallback lists used only if both the live fetch and the on-disk
# cache are unavailable (e.g. first run with no internet access).
_FALLBACK_NIFTY500 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "BAJFINANCE", "KOTAKBANK", "LT",
    "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA", "WIPRO",
    "ULTRACEMCO", "NESTLEIND",
]


def _cache_path(name: str):
    return config.DATA_DIR / f"{name}.csv"


def _read_cache(name: str):
    path = _cache_path(name)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    try:
        tickers = pd.read_csv(path)["Symbol"].dropna().astype(str).tolist()
    except Exception:
        return None
    if not tickers:
        return None
    return tickers, age


def _write_cache(name: str, tickers: list[str]):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Symbol": tickers}).to_csv(_cache_path(name), index=False)


def _fetch_nifty500_live() -> list[str]:
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
    if not symbols:
        raise ValueError("Empty Nifty 500 list from NSE archives")
    return symbols


def _fetch_nse_all_live() -> list[str]:
    # "Nifty Total Market" is NSE's broadest official index (~750 EQ-series
    # stocks), used here as a practical stand-in for "all NSE stocks" -- it
    # covers virtually the whole liquid, tradeable NSE universe without
    # pulling in suspended/illiquid/trade-to-trade-only symbols that a raw
    # full listing would include and that yfinance mostly can't price anyway.
    url = "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
    if not symbols:
        raise ValueError("Empty NSE Total Market list from NSE archives")
    return symbols


def _get_universe(name: str, fetch_fn, fallback: list[str]) -> tuple[list[str], str]:
    """Returns (symbols, source_description)."""
    cached = _read_cache(name)
    if cached is not None and cached[1] < _CACHE_MAX_AGE_SECONDS:
        return cached[0], "cached list (< 7 days old)"

    try:
        symbols = fetch_fn()
        _write_cache(name, symbols)
        return symbols, "freshly fetched"
    except Exception:
        pass

    if cached is not None:
        return cached[0], "stale cached list (live fetch failed)"

    return fallback, "built-in fallback list (live fetch failed, no cache)"


def get_nifty_500() -> tuple[list[str], str]:
    """Returns (['RELIANCE.NS', ...], source_description)."""
    symbols, source = _get_universe("nifty500", _fetch_nifty500_live, _FALLBACK_NIFTY500)
    return [f"{s}.NS" for s in symbols], source


def get_nse_all() -> tuple[list[str], str]:
    """Returns (['RELIANCE.NS', ...], source_description) for NSE's
    broadest official list (Nifty Total Market), used as "all NSE stocks".
    """
    symbols, source = _get_universe("nse_all", _fetch_nse_all_live, _FALLBACK_NIFTY500)
    return [f"{s}.NS" for s in symbols], source
