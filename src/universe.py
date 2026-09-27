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


# NSE's own official constituent CSVs for the 12 sectoral indices shown
# on the Home page's "Market and Sectors" card -- confirmed live against
# NSE's archives (each returns real, correctly-sized constituent lists,
# e.g. NIFTY Bank -> 14 symbols, NIFTY IT -> 10) rather than assumed from
# naming convention alone. Note NIFTY Private Bank's file uses an
# underscore ("ind_nifty_privatebanklist.csv") where every other sector
# here doesn't -- that's NSE's own inconsistency, not a typo.
_SECTOR_INDEX_FILES = {
    "NIFTY Bank": "ind_niftybanklist.csv",
    "NIFTY IT": "ind_niftyitlist.csv",
    "NIFTY Auto": "ind_niftyautolist.csv",
    "NIFTY Pharma": "ind_niftypharmalist.csv",
    "NIFTY FMCG": "ind_niftyfmcglist.csv",
    "NIFTY Metal": "ind_niftymetallist.csv",
    "NIFTY Realty": "ind_niftyrealtylist.csv",
    "NIFTY Media": "ind_niftymedialist.csv",
    "NIFTY PSU Bank": "ind_niftypsubanklist.csv",
    "NIFTY Private Bank": "ind_nifty_privatebanklist.csv",
    "NIFTY Financial Services": "ind_niftyfinancelist.csv",
    "NIFTY Healthcare": "ind_niftyhealthcarelist.csv",
}


def _fetch_sector_constituents_live(sector_name: str) -> list[str]:
    filename = _SECTOR_INDEX_FILES[sector_name]
    url = f"https://archives.nseindia.com/content/indices/{filename}"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
    if not symbols:
        raise ValueError(f"Empty constituent list for {sector_name} from NSE archives")
    return symbols


def get_sector_constituents(sector_name: str) -> tuple[list[str], str] | None:
    """Returns (['AXISBANK.NS', ...], source_description) for one of the
    12 sectoral indices in `_SECTOR_INDEX_FILES`, or `None` if the name
    isn't one of those (or NSE's list can't be fetched right now and
    there's no usable cache). Deliberately has no built-in fallback list
    the way `get_nifty_500`/`get_nse_all` do -- there's no honest
    stand-in for "the exact member stocks of this one sectoral index,"
    so an unavailable live fetch is reported as unavailable rather than
    guessed at.
    """
    if sector_name not in _SECTOR_INDEX_FILES:
        return None
    cache_name = f"sector_{sector_name.lower().replace(' ', '_')}"
    cached = _read_cache(cache_name)
    if cached is not None and cached[1] < _CACHE_MAX_AGE_SECONDS:
        return [f"{s}.NS" for s in cached[0]], "cached list (< 7 days old)"

    try:
        symbols = _fetch_sector_constituents_live(sector_name)
        _write_cache(cache_name, symbols)
        return [f"{s}.NS" for s in symbols], "freshly fetched"
    except Exception:
        pass

    if cached is not None:
        return [f"{s}.NS" for s in cached[0]], "stale cached list (live fetch failed)"

    return None
