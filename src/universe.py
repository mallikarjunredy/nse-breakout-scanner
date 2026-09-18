"""Loads the Nifty 500 ticker universe, with on-disk caching so the app
doesn't have to hit NSE's archives on every run.
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

# Small fallback list used only if both the live fetch and the on-disk
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


def get_nifty_500() -> tuple[list[str], str]:
    """Returns (['RELIANCE.NS', ...], source_description)."""
    name = "nifty500"
    cached = _read_cache(name)
    if cached is not None and cached[1] < _CACHE_MAX_AGE_SECONDS:
        symbols, source = cached[0], "cached list (< 7 days old)"
    else:
        try:
            symbols = _fetch_nifty500_live()
            _write_cache(name, symbols)
            source = "freshly fetched"
        except Exception:
            if cached is not None:
                symbols, source = cached[0], "stale cached list (live fetch failed)"
            else:
                symbols, source = _FALLBACK_NIFTY500, "built-in fallback list (live fetch failed, no cache)"
    return [f"{s}.NS" for s in symbols], source
