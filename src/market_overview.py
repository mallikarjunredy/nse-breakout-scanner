"""Home page's "Market Overview" card: the three headline Indian
indices. Never fabricates a value -- an index is simply omitted from
the returned list if its data isn't available right now.
"""

import yfinance as yf

_HOME_INDICES = [
    ("^NSEI", "Nifty 50"),
    ("^NSEBANK", "Bank Nifty"),
    ("^BSESN", "Sensex"),
]


def get_indices_snapshot() -> list[dict]:
    """Returns [{name, level, change_pct}, ...] for Nifty 50, Bank Nifty,
    and Sensex -- skipping (not fabricating) any index whose data isn't
    available right now.
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
        change_pct = (last_close - prev_close) / prev_close * 100
        snapshots.append({"name": name, "level": last_close, "change_pct": change_pct})
    return snapshots
