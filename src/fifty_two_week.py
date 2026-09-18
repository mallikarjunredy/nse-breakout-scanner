"""Stocks making a new 52-week high or low, for the Home page's
"52-Week High / Low" card. A pure function over already-downloaded
OHLCV (see pre_breakout.py's call site, which reuses the same bulk
download it already does for the Upside Buy Movement scan rather than
fetching the whole universe a second time).
"""

import pandas as pd

TRADING_DAYS_PER_YEAR = 252  # ~52 weeks of trading sessions -- the standard proxy


def compute_fifty_two_week_lists(
    history: dict[str, pd.DataFrame], min_price: float, period: int = TRADING_DAYS_PER_YEAR,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (new_highs_df, new_lows_df): stocks whose latest session's
    High/Low reached or exceeded their own trailing `period`-session
    High/Low (i.e. actually made a new 52-week extreme today, not just
    "is close to" one). A ticker with fewer than `period` sessions of
    history is skipped entirely -- there's no way to know its genuine
    52-week extreme from a shorter window, so it's left out rather than
    mislabeled.
    """
    high_rows, low_rows = [], []
    for ticker, df in history.items():
        if len(df) < period:
            continue
        price = float(df["Close"].iloc[-1])
        if price <= min_price:
            continue

        window = df.iloc[-period:]
        week_high = float(window["High"].max())
        week_low = float(window["Low"].min())
        today_high = float(df["High"].iloc[-1])
        today_low = float(df["Low"].iloc[-1])

        prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else None

        if today_high >= week_high:
            high_rows.append({
                "Ticker": ticker, "Current Price": round(price, 2),
                "52W High": round(week_high, 2), "Change %": change_pct,
            })
        if today_low <= week_low:
            low_rows.append({
                "Ticker": ticker, "Current Price": round(price, 2),
                "52W Low": round(week_low, 2), "Change %": change_pct,
            })

    columns_high = ["Ticker", "Company Name", "Current Price", "52W High", "Change %"]
    columns_low = ["Ticker", "Company Name", "Current Price", "52W Low", "Change %"]
    high_df = pd.DataFrame(high_rows, columns=[c for c in columns_high if c != "Company Name"])
    low_df = pd.DataFrame(low_rows, columns=[c for c in columns_low if c != "Company Name"])
    if not high_df.empty:
        high_df = high_df.sort_values("Change %", ascending=False, na_position="last").reset_index(drop=True)
    if not low_df.empty:
        low_df = low_df.sort_values("Change %", ascending=True, na_position="last").reset_index(drop=True)
    return high_df, low_df
