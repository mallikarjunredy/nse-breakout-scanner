"""Bullish Recovery Above EMAs: a fixed-rule daily scanner, in the same
spirit as "Upside Buy Movement" -- every threshold below comes straight
from this strategy's own definition (see config.py's BULLISH_RECOVERY_*
constants), nothing here is user-adjustable.

This is deliberately NOT described as a "bullish engulfing" pattern: a
real engulfing pattern also requires today's open to sit below
yesterday's close, which this rule set never checks (rule 4 only
requires today's close to be at/above yesterday's *open*) -- so calling
it "engulfing" would overclaim what these conditions actually verify.

Data source is yfinance end-of-day/delayed data, same as every other
strategy in this app -- see scan_bullish_recovery()'s returned
`data_asof_date` for the underlying candle date, which callers must
display alongside the scan timestamp.
"""

import pandas as pd

from . import config, indicators, scanner, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All Stocks"),
}

_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Latest Close", "Change %", "EMA10", "EMA20",
    "Previous Open", "Previous Close", "RSI", "Market Cap (Cr)", "Candle Date",
]


def _evaluate_ticker(ticker: str, df: pd.DataFrame) -> dict | None:
    """Computes every value this strategy needs for one ticker's completed
    daily candles. Returns None only when there isn't enough history to
    get a real EMA20/RSI14 yet -- that's a genuine "missing data" skip,
    counted separately from a ticker that simply fails a condition.
    """
    min_len = max(config.BULLISH_RECOVERY_EMA_SLOW, config.RSI_PERIOD) + 30
    if len(df) < min_len:
        return None

    close = df["Close"]
    ema10 = indicators.compute_ema(close, config.BULLISH_RECOVERY_EMA_FAST)
    ema20 = indicators.compute_ema(close, config.BULLISH_RECOVERY_EMA_SLOW)
    rsi = indicators.compute_rsi(close)

    rsi_now = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None
    if rsi_now is None:
        return None

    latest_close = float(close.iloc[-1])
    prev_open = float(df["Open"].iloc[-2])
    prev_close = float(close.iloc[-2])
    change_pct = round((latest_close - prev_close) / prev_close * 100, 2) if prev_close else None

    return {
        "Ticker": ticker,
        "Latest Close": latest_close,
        "Change %": change_pct,
        "EMA10": float(ema10.iloc[-1]),
        "EMA20": float(ema20.iloc[-1]),
        "Previous Open": prev_open,
        "Previous Close": prev_close,
        "RSI": rsi_now,
        "Candle Date": df.index[-1].strftime("%Y-%m-%d"),
    }


def _conditions_ok(row: dict, min_price: float) -> bool:
    """The six conditions computable straight from OHLCV (everything
    except market cap, which needs a per-ticker .info lookup done only
    for tickers that already clear this bar -- see scan_bullish_recovery).
    """
    return (
        row["Latest Close"] >= row["EMA10"]
        and row["Latest Close"] >= row["EMA20"]
        and row["Previous Close"] <= row["Previous Open"]
        and row["Latest Close"] >= row["Previous Open"]
        and row["RSI"] >= config.BULLISH_RECOVERY_RSI_MIN
        and row["Latest Close"] > min_price
    )


def _build_table(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.DataFrame(rows).sort_values("Change %", ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", range(1, len(df) + 1))
    return df[[c for c in _COLUMNS if c in df.columns]]


def scan_bullish_recovery(
    min_price: float | None = None, progress_callback=None, universe_name: str = "nifty500",
) -> dict:
    """Runs the Bullish Recovery Above EMAs rule set over the chosen
    universe ("nifty500" or "all_nse").

    Price floor: this strategy's own minimum is
    config.BULLISH_RECOVERY_MIN_PRICE_INR (₹25), but the app's existing
    global floor (config.MIN_PRICE_INR, ₹100) always takes precedence
    when it's higher -- effectively ₹100 in this app today, since that
    global floor isn't optional here. Passing a higher `min_price`
    explicitly raises the floor further; it can never lower it below
    config.MIN_PRICE_INR.
    """
    if min_price is None:
        min_price = config.BULLISH_RECOVERY_MIN_PRICE_INR
    min_price = max(min_price, config.MIN_PRICE_INR)

    if universe_name not in _UNIVERSE_LOADERS:
        raise ValueError(f"Unknown universe_name: {universe_name}")
    loader, universe_label = _UNIVERSE_LOADERS[universe_name]
    tickers, source = loader()

    if progress_callback:
        progress_callback(0.05, f"Downloading price history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers)

    pre_candidates = []
    skipped_no_data = len(tickers) - len(history)
    total = len(history)
    for i, (ticker, df) in enumerate(history.items()):
        row = _evaluate_ticker(ticker, df)
        if row is None:
            skipped_no_data += 1
        elif _conditions_ok(row, min_price):
            pre_candidates.append(row)
        if progress_callback and total:
            progress_callback(0.1 + 0.55 * (i + 1) / total, f"Evaluating {ticker}...")

    # Market cap needs a per-ticker .info lookup -- never run across the
    # whole universe (see scanner.fetch_candidate_info's docstring). By
    # this point pre_candidates is already narrowed to tickers passing
    # every other condition, so this only runs for a small set.
    if progress_callback:
        progress_callback(0.7, f"Checking market cap for {len(pre_candidates)} candidates...")
    info_map = scanner.fetch_candidate_info([r["Ticker"] for r in pre_candidates]) if pre_candidates else {}

    matches = []
    skipped_missing_cap = 0
    for row in pre_candidates:
        info = info_map.get(row["Ticker"], {})
        market_cap = info.get("market_cap")
        if market_cap is None:
            skipped_missing_cap += 1
            continue
        market_cap_cr = market_cap / 1_00_00_000  # raw rupees -> INR crore (1 crore = 1e7)
        if market_cap_cr < config.BULLISH_RECOVERY_MIN_MARKET_CAP_CR:
            continue
        row["Market Cap (Cr)"] = round(market_cap_cr, 1)
        row["Company Name"] = info.get("name") or row["Ticker"]
        matches.append(row)

    if progress_callback:
        progress_callback(1.0, "Done.")

    last_dates = [df.index[-1].date() for df in history.values() if len(df)]
    data_asof_date = max(last_dates) if last_dates else None

    return {
        "matches": _build_table(matches),
        "universe_size": len(tickers),
        "scanned": len(history),
        "skipped_no_data": skipped_no_data,
        "skipped_missing_market_cap": skipped_missing_cap,
        "min_price": min_price,
        "source": source,
        "data_asof_date": data_asof_date,
        "universe_label": universe_label,
    }
