"""Home page's "Market and Sectors" card and the top ticker tape. Never
fabricates a value -- an instrument is simply omitted from the returned
list (or, for the Market and Sectors card, marked unavailable) if its
data isn't available right now.
"""

import pandas as pd
import yfinance as yf

from . import scanner

_HOME_INDICES = [
    ("^NSEI", "Nifty 50"),
    ("^NSEBANK", "Bank Nifty"),
    ("^BSESN", "Sensex"),
]


def get_indices_snapshot() -> list[dict]:
    """Returns [{name, ticker, level, change, change_pct}, ...] for
    Nifty 50, Bank Nifty, and Sensex -- skipping (not fabricating) any
    index whose data isn't available right now.
    """
    snapshots = []
    for ticker, name in _HOME_INDICES:
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d")
        except Exception:
            continue
        if hist is None or len(hist) < 2:
            continue
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        if prev_close == 0:
            continue
        change = last_close - prev_close
        change_pct = change / prev_close * 100
        snapshots.append({
            "name": name, "ticker": ticker, "level": last_close, "change": change, "change_pct": change_pct,
        })
    return snapshots


def get_ticker_tape_quotes(tickers: list[str]) -> list[dict]:
    """Returns [{symbol, ticker, price, change, change_pct}, ...] for the
    given tickers (e.g. the user's watchlist), in the same shape as the
    index snapshots above so both can feed one ticker tape. Skips any
    ticker with no usable price history rather than showing a blank/zero
    row for it.
    """
    if not tickers:
        return []
    history = scanner.download_history(tickers)
    quotes = []
    for ticker in tickers:
        df = history.get(ticker)
        if df is None or len(df) < 1:
            continue
        price = float(df["Close"].iloc[-1])
        prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
        change = (price - prev_close) if prev_close else None
        change_pct = (change / prev_close * 100) if prev_close else None
        quotes.append({
            "symbol": ticker.replace(".NS", ""), "ticker": ticker,
            "price": price, "change": change, "change_pct": change_pct,
        })
    return quotes


# ---------------------------------------------------------------------------
# "Market and Sectors" card (Home) -- replaces the old Nifty 50/Bank Nifty/
# Sensex trio with a wider set of NSE broad-market and sectoral indices,
# built from a user-supplied reference screenshot.
#
# Sparklines are built from the most recent trading session's own 5-minute
# intraday bars, NOT multi-day daily closes: several of these indices (NIFTY
# Financial Services, NIFTY Private Bank, NIFTY Healthcare, NIFTY
# Largemidcap 250, NIFTY Midsmallcap 400) have almost no backfilled *daily*
# history on Yahoo Finance -- often just one cached row no matter how far
# back requested -- but every one of them has a full intraday 5-minute
# series for the last several sessions, confirmed by direct testing before
# building this. "Previous close" is the prior session's own last intraday
# bar, not a separate daily-history lookup.
#
# Three rows the reference screenshot shows (NIFTY 100 Largecap/Smallcap/
# Midcap) have no confirmed Yahoo Finance ticker at all -- similarly-named
# indices exist (NIFTY SMLCAP 100, NIFTY MIDCAP 50) but are different
# indices with different values, not the ones shown. Rather than guess or
# mislabel one index as another, those three rows are kept for layout
# completeness but always render as "available": False (an explicit
# "data unavailable" row in the UI), never a fabricated number.
_BROAD_BASED_INDICES = [
    ("NIFTY 50", "^NSEI"),
    ("NIFTY Next 50", "^NSMIDCP"),
    ("NIFTY 100 Largecap", None),
    ("NIFTY 500", "^CRSLDX"),
    ("NIFTY Largemidcap 250", "NIFTY_LARGEMID250.NS"),
    ("NIFTY 100 Smallcap", None),
    ("NIFTY Midsmallcap 400", "NIFTYMIDSML400.NS"),
    ("NIFTY 100 Midcap", None),
]

_SECTORAL_INDICES = [
    ("NIFTY Bank", "^NSEBANK"),
    ("NIFTY IT", "^CNXIT"),
    ("NIFTY Auto", "^CNXAUTO"),
    ("NIFTY Pharma", "^CNXPHARMA"),
    ("NIFTY FMCG", "^CNXFMCG"),
    ("NIFTY Metal", "^CNXMETAL"),
    ("NIFTY Realty", "^CNXREALTY"),
    ("NIFTY Media", "^CNXMEDIA"),
    ("NIFTY PSU Bank", "^CNXPSUBANK"),
    ("NIFTY Financial Services", "NIFTY_FIN_SERVICE.NS"),
    ("NIFTY Private Bank", "NIFTY_PVT_BANK.NS"),
    ("NIFTY Healthcare", "NIFTY_HEALTHCARE.NS"),
]


def _bulk_intraday_5m(tickers: list[str]) -> dict:
    """One bulk yfinance call for 5-minute bars over the last few
    sessions, for every requested ticker -- the same bulk-download
    discipline as scanner.download_history, just at intraday granularity,
    which that function doesn't support (it's hardcoded to daily bars).
    """
    if not tickers:
        return {}
    raw = yf.download(
        tickers, period="5d", interval="5m", group_by="ticker", threads=True, progress=False,
    )
    result = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker].dropna(subset=["Close"])
            if not df.empty:
                result[ticker] = df
    else:
        df = raw.dropna(subset=["Close"])
        if not df.empty and tickers:
            result[tickers[0]] = df
    return result


def _index_row(name: str, ticker: str | None, intraday: dict) -> dict:
    """One row for the Market and Sectors card. Returns
    {"available": False} (never a fabricated value) if there's no
    ticker mapping, or the intraday data doesn't cover at least two
    distinct sessions (needed to compute a real change % against the
    previous close).
    """
    if ticker is None or ticker not in intraday:
        return {"name": name, "ticker": ticker, "available": False}
    df = intraday[ticker]
    idx_dates = df.index.date
    dates = sorted(set(idx_dates))
    if len(dates) < 2:
        return {"name": name, "ticker": ticker, "available": False}
    latest_date, prev_date = dates[-1], dates[-2]
    today_bars = df[idx_dates == latest_date]
    prev_bars = df[idx_dates == prev_date]
    if today_bars.empty or prev_bars.empty:
        return {"name": name, "ticker": ticker, "available": False}
    prev_close = float(prev_bars["Close"].iloc[-1])
    if prev_close <= 0:
        return {"name": name, "ticker": ticker, "available": False}
    latest_value = float(today_bars["Close"].iloc[-1])
    change_pct = (latest_value - prev_close) / prev_close * 100
    return {
        "name": name, "ticker": ticker, "available": True,
        "value": latest_value, "change_pct": change_pct, "prev_close": prev_close,
        "sparkline": [float(v) for v in today_bars["Close"].tolist()],
    }


def get_market_and_sectors_snapshot() -> dict:
    """Returns {"broad_based": [...], "sectoral": [...]}, each a list of
    row dicts with available rows sorted by change % descending (gainers
    first, matching the reference layout) followed by any unavailable
    rows kept for layout completeness.
    """
    all_tickers = [t for _, t in _BROAD_BASED_INDICES + _SECTORAL_INDICES if t]
    intraday = _bulk_intraday_5m(all_tickers)

    def _build(defs):
        rows = [_index_row(name, ticker, intraday) for name, ticker in defs]
        available = sorted((r for r in rows if r["available"]), key=lambda r: r["change_pct"], reverse=True)
        unavailable = [r for r in rows if not r["available"]]
        return available + unavailable

    return {"broad_based": _build(_BROAD_BASED_INDICES), "sectoral": _build(_SECTORAL_INDICES)}
