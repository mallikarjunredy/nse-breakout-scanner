"""Persistent Portfolio (actual holdings, not the Watchlist): tickers the
user owns, with quantity and buy price, stored as a JSON list under
data/portfolio.json -- plus an optional single cash balance in
data/portfolio_cash.json. Built as a prerequisite for the "Global News
Impact & Stock Forecast" feature's "My Portfolio Impact" section, which
needs real holding weights to work from; it didn't exist in this app
before.

Each holding is its own "lot" (one JSON entry per "Add Holding" action,
keyed by a unique id), so buying the same stock twice at different
prices/dates creates two lots rather than silently averaging them --
`get_portfolio()` aggregates lots by ticker (summed quantity, weighted-
average buy price) for display, so the UI still shows one row per stock.
"""

import json
import time

from . import config, scanner

_HOLDINGS_PATH = config.DATA_DIR / "portfolio.json"
_CASH_PATH = config.DATA_DIR / "portfolio_cash.json"

_DISPLAY_COLUMNS = [
    "Ticker", "Company Name", "Quantity", "Avg. Buy Price", "Invested (₹)",
    "Current Price", "Current Value (₹)", "Unrealized P&L (₹)", "Unrealized P&L %", "Weight %",
]


def load_holdings() -> list[dict]:
    if not _HOLDINGS_PATH.exists():
        return []
    try:
        with open(_HOLDINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_holdings(entries: list[dict]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_HOLDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add_holding(ticker: str, quantity: float, buy_price: float, buy_date: str, notes: str = "") -> None:
    entries = load_holdings()
    entries.append({
        "id": f"{ticker}_{time.time()}",
        "ticker": ticker,
        "quantity": float(quantity),
        "buy_price": float(buy_price),
        "buy_date": buy_date,
        "notes": notes,
        "added_at": time.time(),
    })
    _save_holdings(entries)


def remove_holding(lot_id: str) -> None:
    entries = [e for e in load_holdings() if e["id"] != lot_id]
    _save_holdings(entries)


def load_cash() -> float:
    if not _CASH_PATH.exists():
        return 0.0
    try:
        with open(_CASH_PATH, "r", encoding="utf-8") as f:
            return float(json.load(f).get("cash", 0.0))
    except Exception:
        return 0.0


def save_cash(amount: float) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CASH_PATH, "w", encoding="utf-8") as f:
        json.dump({"cash": float(amount)}, f, indent=2)


def get_tickers() -> list[str]:
    """Distinct tickers currently held -- used by the News Impact page to
    look up exposure data without needing a full portfolio valuation.
    """
    return sorted({e["ticker"] for e in load_holdings()})


def get_portfolio() -> dict:
    """Aggregates lots by ticker, fetches one fresh quote per ticker, and
    returns {"table": DataFrame, "total_value": float, "cash": float,
    "total_invested": float}. A ticker whose fresh price can't be
    fetched right now shows N/A for Current Price/Value/P&L rather than
    a stale or fabricated number, and is excluded from the weight-%
    denominator (its invested amount still counts toward the total so
    the portfolio doesn't silently shrink).
    """
    import pandas as pd

    entries = load_holdings()
    cash = load_cash()
    if not entries:
        return {"table": pd.DataFrame(columns=_DISPLAY_COLUMNS), "total_value": cash,
                "cash": cash, "total_invested": 0.0}

    by_ticker: dict[str, dict] = {}
    for e in entries:
        t = e["ticker"]
        agg = by_ticker.setdefault(t, {"quantity": 0.0, "invested": 0.0})
        agg["quantity"] += e["quantity"]
        agg["invested"] += e["quantity"] * e["buy_price"]

    tickers = sorted(by_ticker.keys())
    history = scanner.download_history(tickers)
    info_map = scanner.fetch_candidate_info(tickers)

    rows = []
    priced_value_sum = 0.0
    total_invested = 0.0
    for t in tickers:
        agg = by_ticker[t]
        qty = agg["quantity"]
        invested = agg["invested"]
        avg_buy = invested / qty if qty else 0.0
        total_invested += invested

        df = history.get(t)
        current_price = float(df["Close"].iloc[-1]) if df is not None and len(df) else None
        if current_price is not None:
            current_value = current_price * qty
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested else None
            priced_value_sum += current_value
        else:
            current_value, pnl, pnl_pct = None, None, None

        name = info_map.get(t, {}).get("name") or t
        rows.append({
            "Ticker": t, "Company Name": name, "Quantity": qty, "Avg. Buy Price": round(avg_buy, 2),
            "Invested (₹)": round(invested, 2),
            "Current Price": round(current_price, 2) if current_price is not None else None,
            "Current Value (₹)": round(current_value, 2) if current_value is not None else None,
            "Unrealized P&L (₹)": round(pnl, 2) if pnl is not None else None,
            "Unrealized P&L %": round(pnl_pct, 2) if pnl_pct is not None else None,
        })

    total_value = priced_value_sum + cash
    for r in rows:
        r["Weight %"] = round(r["Current Value (₹)"] / total_value * 100, 2) if (
            r["Current Value (₹)"] is not None and total_value > 0
        ) else None

    table = pd.DataFrame(rows, columns=_DISPLAY_COLUMNS).sort_values("Weight %", ascending=False, na_position="last").reset_index(drop=True)
    return {"table": table, "total_value": total_value, "cash": cash, "total_invested": total_invested}
