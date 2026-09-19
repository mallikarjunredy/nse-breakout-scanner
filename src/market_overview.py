"""Home page's "Market Overview" card and the top ticker tape. Never
fabricates a value -- an instrument is simply omitted from the returned
list if its data isn't available right now.
"""

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
