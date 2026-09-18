""""Upside Buy Movement": the app's one strategy. Scans for stocks that
have NOT yet broken out but look like a stock immediately before a
strong breakout -- tight consolidation just under a meaningful
resistance level, contracting volatility and volume, and a healthy
(not overbought) uptrend. Every threshold here is a fixed rule from
this strategy's own definition (see config.py's PRE_BREAKOUT_*
constants) -- there is nothing user-adjustable by design.

Data source is the same as everywhere else in this app: `yfinance`
end-of-day/delayed data, not an official NSE real-time feed. This module
never predicts that a breakout will happen on any specific future date --
it only reports today's measured technical state.
"""

import pandas as pd

from . import config, fifty_two_week, indicators, scanner, universe


def _find_resistance(df: pd.DataFrame) -> tuple[float | None, int | None]:
    """Resistance across the strategy's 20/40/60-day windows (today
    excluded from each window). Prefers the *shortest* lookback whose
    level places today's close within (or already through) the
    "meaningful" zone -- the most immediately relevant overhead level --
    falling back to the longest window's level just so callers always
    have something to classify "Already Broken Out" against.
    """
    price = float(df["Close"].iloc[-1])
    candidates = []
    for lookback in config.PRE_BREAKOUT_RESISTANCE_LOOKBACKS:
        if len(df) <= lookback:
            continue
        level = float(df["High"].iloc[-lookback - 1:-1].max())
        if level > 0:
            candidates.append((lookback, level))
    if not candidates:
        return None, None
    for lookback, level in candidates:
        pct_below = (level - price) / level * 100
        if pct_below <= config.PRE_BREAKOUT_NEAR_RESISTANCE_PCT:
            return level, lookback
    longest_lookback, longest_level = candidates[-1]
    return longest_level, longest_lookback


def _count_consolidation_days(df: pd.DataFrame, resistance: float) -> int:
    """How many of the most recent trading days (scanning backward from
    today, stopping at the first day that breaks the band) have Close
    within config.PRE_BREAKOUT_CONSOLIDATION_BAND_PCT below `resistance`
    (allowing a small overshoot above it) -- the current consolidation
    "streak" length, not a full swing-detection algorithm.
    """
    band_low = resistance * (1 - config.PRE_BREAKOUT_CONSOLIDATION_BAND_PCT / 100)
    band_high = resistance * 1.02
    closes = df["Close"]
    count = 0
    for i in range(1, len(closes) + 1):
        c = float(closes.iloc[-i])
        if band_low <= c <= band_high:
            count += 1
        else:
            break
    return count


def _relative_strength(df: pd.DataFrame, index_df: pd.DataFrame | None, days: int) -> float | None:
    """Stock's trailing `days`-bar return minus the benchmark index's --
    positive means the stock outperformed the index over that window."""
    if index_df is None or len(df) <= days or len(index_df) <= days:
        return None
    stock_start = float(df["Close"].iloc[-days - 1])
    idx_start = float(index_df["Close"].iloc[-days - 1])
    if stock_start <= 0 or idx_start <= 0:
        return None
    stock_ret = (float(df["Close"].iloc[-1]) / stock_start - 1) * 100
    idx_ret = (float(index_df["Close"].iloc[-1]) / idx_start - 1) * 100
    return round(stock_ret - idx_ret, 2)


def _evaluate_ticker(ticker: str, df: pd.DataFrame, index_df: pd.DataFrame | None, min_price: float) -> dict | None:
    """Evaluates one ticker against every "Upside Buy Movement" condition.
    Returns None if it doesn't qualify as either a pre-breakout candidate
    or an "Already Broken Out" reference case; otherwise a row dict with
    every column the strategy spec calls for.
    """
    ema_slow_period = config.PRE_BREAKOUT_EMA_SLOW
    trend_window = config.PRE_BREAKOUT_TREND_LOOKBACK_DAYS
    if len(df) < max(ema_slow_period, trend_window) + 30:
        return None

    price = float(df["Close"].iloc[-1])
    if price <= min_price:
        return None

    ema20 = indicators.compute_ema(df["Close"], config.PRE_BREAKOUT_EMA_FAST)
    ema50 = indicators.compute_ema(df["Close"], ema_slow_period)
    ema20_now, ema50_now = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    if not (price > ema20_now > ema50_now):
        return None
    if not (ema20.iloc[-1] > ema20.iloc[-6] and ema50.iloc[-1] > ema50.iloc[-6]):
        return None  # both EMAs must be rising, not just stacked

    # Higher-high / higher-low over the trend window, split into two
    # halves -- a deliberate simplification of full swing-point detection,
    # documented as such rather than claiming a more precise read.
    window = df.iloc[-trend_window:]
    half = trend_window // 2
    first_half, second_half = window.iloc[:half], window.iloc[half:]
    higher_high = second_half["High"].max() > first_half["High"].max()
    higher_low = second_half["Low"].min() > first_half["Low"].min()
    if not (higher_high and higher_low):
        return None

    rsi = float(indicators.compute_rsi(df["Close"]).iloc[-1])
    if pd.isna(rsi) or not (config.PRE_BREAKOUT_RSI_MIN <= rsi <= config.PRE_BREAKOUT_RSI_MAX):
        return None

    macd_hist = indicators.compute_macd_histogram(df["Close"])
    macd_now = float(macd_hist.iloc[-1])
    macd_flat_or_improving = macd_now >= float(macd_hist.iloc[-4])
    macd_recently_positive = bool((macd_hist.iloc[-5:] > 0).any())
    if not (macd_flat_or_improving or macd_recently_positive):
        return None

    vol_avg_5 = float(df["Volume"].iloc[-config.PRE_BREAKOUT_VOLUME_AVG_SHORT:].mean())
    vol_avg_20 = float(df["Volume"].iloc[-config.PRE_BREAKOUT_VOLUME_AVG_LONG:].mean())
    if not (vol_avg_20 > 0 and vol_avg_5 < vol_avg_20):
        return None
    vol_ratio_5_20 = vol_avg_5 / vol_avg_20

    atr_pct_series = indicators.compute_atr(df, period=config.PRE_BREAKOUT_ATR_PERIOD) / df["Close"] * 100
    recent_atr_pct = float(atr_pct_series.iloc[-config.PRE_BREAKOUT_ATR_RECENT_DAYS:].mean())
    prior_slice = atr_pct_series.iloc[
        -(config.PRE_BREAKOUT_ATR_RECENT_DAYS + config.PRE_BREAKOUT_ATR_PRIOR_DAYS):-config.PRE_BREAKOUT_ATR_RECENT_DAYS
    ]
    prior_atr_pct = float(prior_slice.mean())
    atr_contracting = recent_atr_pct < prior_atr_pct
    if not atr_contracting:
        return None

    recent_days = config.PRE_BREAKOUT_RANGE_RECENT_DAYS
    prior_days = config.PRE_BREAKOUT_RANGE_PRIOR_DAYS
    range_recent = float(df["High"].iloc[-recent_days:].max() - df["Low"].iloc[-recent_days:].min())
    prior_slice_df = df.iloc[-(recent_days + prior_days):-recent_days]
    range_prior = float(prior_slice_df["High"].max() - prior_slice_df["Low"].min())
    if not (range_recent < range_prior):
        return None

    resistance, resistance_window = _find_resistance(df)
    if resistance is None:
        return None
    pct_below = (resistance - price) / resistance * 100
    already_broken_out = price > resistance

    if already_broken_out:
        consolidation_days = None
        status = "Already Broken Out"
    else:
        if not (0 <= pct_below <= config.PRE_BREAKOUT_NEAR_RESISTANCE_PCT):
            return None
        consolidation_days = _count_consolidation_days(df, resistance)
        if not (config.PRE_BREAKOUT_CONSOLIDATION_MIN_DAYS <= consolidation_days
                <= config.PRE_BREAKOUT_CONSOLIDATION_MAX_DAYS):
            return None
        status = "Near Resistance" if pct_below <= config.PRE_BREAKOUT_NEAR_VS_CONSOLIDATING_SPLIT_PCT else "Consolidating"

    rel_strength = _relative_strength(df, index_df, config.PRE_BREAKOUT_RELATIVE_STRENGTH_DAYS)
    if not already_broken_out and (rel_strength is None or rel_strength <= 0):
        return None  # "positive 20-day relative strength vs Nifty 500" is a hard requirement

    if already_broken_out:
        why = [
            f"✓ Close (₹{price:.2f}) is already above the {resistance_window}D resistance level "
            f"(₹{resistance:.2f}) -- this stock has already moved, shown for reference only",
            f"✓ Close > EMA20 (₹{ema20_now:.2f}) > EMA50 (₹{ema50_now:.2f}), both rising",
            f"✓ RSI {rsi:.0f} still within the healthy 50-65 zone",
        ]
    else:
        why = [
            f"✓ Close > EMA20 (₹{ema20_now:.2f}) > EMA50 (₹{ema50_now:.2f}), both EMAs rising",
            "✓ Higher-high/higher-low trend over the last ~3 months",
            f"✓ Within {pct_below:.2f}% of the {resistance_window}D resistance level (₹{resistance:.2f})",
            f"✓ Consolidating for {consolidation_days} sessions near that level",
            f"✓ 10-day trading range narrower than the preceding 20 sessions, ATR% contracting "
            f"({recent_atr_pct:.2f}% vs {prior_atr_pct:.2f}% prior)",
            f"✓ Volume contracting -- 5-day average is {vol_ratio_5_20:.2f}x the 20-day average",
            f"✓ RSI {rsi:.0f} within the healthy 50-65 momentum zone (not overbought)",
            f"✓ Outperforming the Nifty 50 by {rel_strength:+.2f}% over the last 20 sessions",
        ]

    return {
        "Ticker": ticker,
        "Why Qualified": "\n".join(why),
        "Current Price": round(price, 2),
        "Resistance Level": round(resistance, 2),
        "Resistance Window": f"{resistance_window}D",
        "% Below Resistance": round(pct_below, 2),
        "EMA20": round(ema20_now, 2),
        "EMA50": round(ema50_now, 2),
        "RSI": round(rsi, 1),
        "MACD Histogram": round(macd_now, 3),
        "ATR %": round(recent_atr_pct, 2),
        "ATR Contracting": bool(atr_contracting),
        "5D Avg Volume": round(vol_avg_5),
        "20D Avg Volume": round(vol_avg_20),
        "Volume Ratio (5D/20D)": round(vol_ratio_5_20, 2),
        "Consolidation Days": consolidation_days,
        "20D Relative Strength": rel_strength,
        "Setup Status": status,
    }


_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Sector", "Current Price", "Resistance Level", "Resistance Window",
    "% Below Resistance", "EMA20", "EMA50", "RSI", "MACD Histogram", "ATR %", "ATR Contracting",
    "5D Avg Volume", "20D Avg Volume", "Volume Ratio (5D/20D)", "Consolidation Days",
    "20D Relative Strength", "Setup Status", "Why Qualified",
]


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["% Below Resistance"])
    df_out = pd.DataFrame(filtered, columns=[c for c in _COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_COLUMNS)
    return df_out


_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All NSE Stocks (₹100+)"),
}


def scan_pre_breakout(
    min_price: float | None = None, progress_callback=None, universe_name: str = "nifty500",
) -> dict:
    """Runs the Upside Buy Movement rule set over a chosen universe:

    - "nifty500" -- the "Upside Buy Movement" strategy (Nifty 500 only).
    - "all_nse" -- the "Upside Buy Movement above 100" strategy (NSE's
      broadest official list, "Nifty Total Market", as a practical stand-
      in for "all NSE stocks" -- same rule set, just not restricted to
      the Nifty 500 index membership).
    """
    if min_price is None:
        min_price = config.MIN_PRICE_INR
    if universe_name not in _UNIVERSE_LOADERS:
        raise ValueError(f"Unknown universe_name: {universe_name}")
    loader, universe_label = _UNIVERSE_LOADERS[universe_name]

    tickers, source = loader()

    if progress_callback:
        progress_callback(0.05, f"Downloading price history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers)

    if progress_callback:
        progress_callback(0.15, "Downloading Nifty 50 index history for relative strength...")
    index_history = scanner.download_history([config.PRE_BREAKOUT_BENCHMARK_INDEX])
    index_df = index_history.get(config.PRE_BREAKOUT_BENCHMARK_INDEX)

    rows = []
    total = len(history)
    for i, (ticker, df) in enumerate(history.items()):
        row = _evaluate_ticker(ticker, df, index_df, min_price)
        if row is not None:
            rows.append(row)
        if progress_callback and total:
            progress_callback(0.2 + 0.6 * (i + 1) / total, f"Evaluating {ticker}...")

    # 52-week high/low reuses `history` (already downloaded above for the
    # strategy itself) rather than fetching the universe a second time --
    # this list is otherwise unrelated to the Upside Buy Movement rules,
    # but the underlying OHLCV need is identical.
    if progress_callback:
        progress_callback(0.82, "Finding 52-week highs and lows...")
    week52_high_df, week52_low_df = fifty_two_week.compute_fifty_two_week_lists(history, min_price)

    if progress_callback:
        progress_callback(0.85, "Fetching company info for candidates...")
    week52_tickers = set(week52_high_df["Ticker"]) | set(week52_low_df["Ticker"])
    all_tickers = sorted({r["Ticker"] for r in rows} | week52_tickers)
    info_map = scanner.fetch_candidate_info(all_tickers) if all_tickers else {}
    for r in rows:
        info = info_map.get(r["Ticker"], {})
        r["Company Name"] = info.get("name") or r["Ticker"]
        r["Sector"] = info.get("sector") or "N/A"
    if not week52_high_df.empty:
        week52_high_df["Company Name"] = week52_high_df["Ticker"].map(
            lambda t: info_map.get(t, {}).get("name") or t
        )
    if not week52_low_df.empty:
        week52_low_df["Company Name"] = week52_low_df["Ticker"].map(
            lambda t: info_map.get(t, {}).get("name") or t
        )

    last_dates = [df.index[-1].date() for df in history.values() if len(df)]
    data_asof_date = max(last_dates) if last_dates else None

    if progress_callback:
        progress_callback(1.0, "Done.")

    return {
        "near_resistance": _build_table(rows, "Near Resistance"),
        "consolidating": _build_table(rows, "Consolidating"),
        "already_broken_out": _build_table(rows, "Already Broken Out"),
        "week52_high": week52_high_df,
        "week52_low": week52_low_df,
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "benchmark_missing": index_df is None,
        "data_asof_date": data_asof_date,
        "universe_label": universe_label,
    }


def normalize_ticker(raw: str) -> str:
    """Turns free-typed search input ("reliance", "RELIANCE.NS") into a
    yfinance-ready NSE ticker -- this strategy is Nifty 500 / NSE-only.
    """
    t = raw.strip().upper()
    if not t.endswith(".NS"):
        t = f"{t}.NS"
    return t


_WATCHLIST_COLUMNS = [
    "Ticker", "Company Name", "Sector", "Setup Status", "Current Price", "Resistance Level",
    "% Below Resistance", "EMA20", "EMA50", "RSI", "MACD Histogram", "ATR %",
    "Volume Ratio (5D/20D)", "Consolidation Days", "20D Relative Strength", "Reason",
]


def evaluate_watchlist_ticker(
    ticker: str, df: pd.DataFrame, index_df: pd.DataFrame | None, min_price: float,
) -> dict:
    """Like _evaluate_ticker, but always returns a row (never None) so a
    watchlisted or searched-for stock keeps showing up even on days it
    doesn't match the Upside Buy Movement setup -- with a Reason string
    explaining exactly which conditions are missing (mirrors the old
    scanner.py's evaluate_watchlist_ticker convention).
    """
    ema_slow_period = config.PRE_BREAKOUT_EMA_SLOW
    trend_window = config.PRE_BREAKOUT_TREND_LOOKBACK_DAYS
    if len(df) < max(ema_slow_period, trend_window) + 30:
        return {"Ticker": ticker, "Setup Status": "⚠️ No data", "Reason": "Not enough price history"}

    price = float(df["Close"].iloc[-1])
    reasons = []
    if price <= min_price:
        reasons.append(f"price ₹{price:.2f} does not satisfy Price > ₹{min_price:.2f}")

    ema20 = indicators.compute_ema(df["Close"], config.PRE_BREAKOUT_EMA_FAST)
    ema50 = indicators.compute_ema(df["Close"], ema_slow_period)
    ema20_now, ema50_now = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    if not (price > ema20_now > ema50_now):
        reasons.append("price is not above a rising EMA20 above EMA50")
    elif not (ema20.iloc[-1] > ema20.iloc[-6] and ema50.iloc[-1] > ema50.iloc[-6]):
        reasons.append("EMA20/EMA50 are not both rising")

    window = df.iloc[-trend_window:]
    half = trend_window // 2
    first_half, second_half = window.iloc[:half], window.iloc[half:]
    higher_high = second_half["High"].max() > first_half["High"].max()
    higher_low = second_half["Low"].min() > first_half["Low"].min()
    if not (higher_high and higher_low):
        reasons.append("no higher-high/higher-low trend over the last ~3 months")

    rsi = float(indicators.compute_rsi(df["Close"]).iloc[-1])
    if pd.isna(rsi):
        reasons.append("RSI unavailable")
    elif not (config.PRE_BREAKOUT_RSI_MIN <= rsi <= config.PRE_BREAKOUT_RSI_MAX):
        reasons.append(f"RSI {rsi:.0f} outside the 50-65 zone")

    macd_hist = indicators.compute_macd_histogram(df["Close"])
    macd_now = float(macd_hist.iloc[-1])
    macd_ok = macd_now >= float(macd_hist.iloc[-4]) or bool((macd_hist.iloc[-5:] > 0).any())
    if not macd_ok:
        reasons.append("MACD histogram not flat/improving or recently positive")

    vol_avg_5 = float(df["Volume"].iloc[-config.PRE_BREAKOUT_VOLUME_AVG_SHORT:].mean())
    vol_avg_20 = float(df["Volume"].iloc[-config.PRE_BREAKOUT_VOLUME_AVG_LONG:].mean())
    vol_ratio_5_20 = vol_avg_5 / vol_avg_20 if vol_avg_20 else None
    if not (vol_avg_20 > 0 and vol_avg_5 < vol_avg_20):
        reasons.append("volume is not contracting (5D avg >= 20D avg)")

    atr_pct_series = indicators.compute_atr(df, period=config.PRE_BREAKOUT_ATR_PERIOD) / df["Close"] * 100
    recent_atr_pct = float(atr_pct_series.iloc[-config.PRE_BREAKOUT_ATR_RECENT_DAYS:].mean())
    prior_slice = atr_pct_series.iloc[
        -(config.PRE_BREAKOUT_ATR_RECENT_DAYS + config.PRE_BREAKOUT_ATR_PRIOR_DAYS):-config.PRE_BREAKOUT_ATR_RECENT_DAYS
    ]
    prior_atr_pct = float(prior_slice.mean())
    if not (recent_atr_pct < prior_atr_pct):
        reasons.append("ATR% is not contracting")

    recent_days = config.PRE_BREAKOUT_RANGE_RECENT_DAYS
    prior_days = config.PRE_BREAKOUT_RANGE_PRIOR_DAYS
    range_recent = float(df["High"].iloc[-recent_days:].max() - df["Low"].iloc[-recent_days:].min())
    prior_slice_df = df.iloc[-(recent_days + prior_days):-recent_days]
    range_prior = float(prior_slice_df["High"].max() - prior_slice_df["Low"].min())
    if not (range_recent < range_prior):
        reasons.append("10-day trading range is not narrower than the preceding 20 sessions")

    resistance, _resistance_window = _find_resistance(df)
    pct_below = None
    already_broken_out = False
    consolidation_days = None
    if resistance is None:
        reasons.append("no meaningful 20-60 day resistance level found")
    else:
        pct_below = (resistance - price) / resistance * 100
        already_broken_out = price > resistance
        if not already_broken_out:
            if not (0 <= pct_below <= config.PRE_BREAKOUT_NEAR_RESISTANCE_PCT):
                reasons.append(f"price is {pct_below:.1f}% below resistance, outside the 0-5% zone")
            consolidation_days = _count_consolidation_days(df, resistance)
            if not (config.PRE_BREAKOUT_CONSOLIDATION_MIN_DAYS <= consolidation_days
                    <= config.PRE_BREAKOUT_CONSOLIDATION_MAX_DAYS):
                reasons.append(f"consolidation length {consolidation_days} sessions outside the 7-20 day window")

    rel_strength = _relative_strength(df, index_df, config.PRE_BREAKOUT_RELATIVE_STRENGTH_DAYS)
    if not already_broken_out:
        if rel_strength is None:
            reasons.append("relative strength vs Nifty 50 unavailable")
        elif rel_strength <= 0:
            reasons.append(f"underperforming the Nifty 50 by {abs(rel_strength):.2f}% over 20 days")

    fully_qualifies = not reasons
    if fully_qualifies:
        if already_broken_out:
            status = "Already Broken Out"
        elif pct_below is not None and pct_below <= config.PRE_BREAKOUT_NEAR_VS_CONSOLIDATING_SPLIT_PCT:
            status = "Near Resistance"
        else:
            status = "Consolidating"
    else:
        status = "⏳ Watching"

    return {
        "Ticker": ticker,
        "Current Price": round(price, 2),
        "Resistance Level": round(resistance, 2) if resistance is not None else None,
        "% Below Resistance": round(pct_below, 2) if pct_below is not None else None,
        "EMA20": round(ema20_now, 2),
        "EMA50": round(ema50_now, 2),
        "RSI": round(rsi, 1) if not pd.isna(rsi) else None,
        "MACD Histogram": round(macd_now, 3),
        "ATR %": round(recent_atr_pct, 2),
        "Volume Ratio (5D/20D)": round(vol_ratio_5_20, 2) if vol_ratio_5_20 is not None else None,
        "Consolidation Days": consolidation_days,
        "20D Relative Strength": rel_strength,
        "Setup Status": status,
        "Reason": "Meets all Upside Buy Movement criteria" if fully_qualifies else "Missing: " + ", ".join(reasons),
    }


def search_ticker(raw_ticker: str, min_price: float | None = None) -> dict | None:
    """Looks up any single ticker on demand -- independent of whether it's
    in the Nifty 500 universe's scan results -- and evaluates it under
    the Upside Buy Movement criteria. Returns None only if no price data
    could be found at all (e.g. an invalid symbol); otherwise always
    returns a row (same shape as a watchlist row) explaining whether/why
    it currently matches the setup.
    """
    ticker = normalize_ticker(raw_ticker)
    if min_price is None:
        min_price = config.MIN_PRICE_INR

    history = scanner.download_history([ticker])
    df = history.get(ticker)
    if df is None:
        return None

    index_history = scanner.download_history([config.PRE_BREAKOUT_BENCHMARK_INDEX])
    index_df = index_history.get(config.PRE_BREAKOUT_BENCHMARK_INDEX)

    row = evaluate_watchlist_ticker(ticker, df, index_df, min_price)
    info = scanner.fetch_candidate_info([ticker]).get(ticker, {})
    row["Company Name"] = info.get("name") or ticker
    row["Sector"] = info.get("sector") or "N/A"
    return row


def get_watchlist_data(tickers: list[str], min_price: float | None = None) -> pd.DataFrame:
    """Builds a live snapshot table for arbitrary watchlisted tickers,
    independent of the main Nifty 500 scan -- always up to date with
    today's price data even for a ticker that isn't currently in any of
    the three scan-result tables.
    """
    if not tickers:
        return pd.DataFrame(columns=_WATCHLIST_COLUMNS)
    if min_price is None:
        min_price = config.MIN_PRICE_INR

    history = scanner.download_history(tickers)
    index_history = scanner.download_history([config.PRE_BREAKOUT_BENCHMARK_INDEX])
    index_df = index_history.get(config.PRE_BREAKOUT_BENCHMARK_INDEX)

    rows = []
    for ticker in tickers:
        df = history.get(ticker)
        if df is None:
            rows.append({"Ticker": ticker, "Setup Status": "⚠️ No data", "Reason": "Price history unavailable"})
        else:
            rows.append(evaluate_watchlist_ticker(ticker, df, index_df, min_price))

    info_map = scanner.fetch_candidate_info(tickers)
    for row in rows:
        info = info_map.get(row["Ticker"], {})
        row["Company Name"] = info.get("name") or row["Ticker"]
        row["Sector"] = info.get("sector") or "N/A"

    status_order = {"Near Resistance": 0, "Consolidating": 1, "Already Broken Out": 2, "⏳ Watching": 3, "⚠️ No data": 4}
    rows.sort(key=lambda r: status_order.get(r.get("Setup Status"), 9))

    return pd.DataFrame(rows, columns=_WATCHLIST_COLUMNS)
