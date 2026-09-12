"""Core scan pipeline: download OHLCV for a market's universe, compute
indicators, filter for upward-trending stocks near/above resistance with
a volume spike and RSI > 50, then rank and split into breakout /
near-breakout tables.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf

from . import config, indicators, universe


def _download_history(tickers: list[str]) -> dict:
    """Bulk-downloads daily OHLCV for all tickers and returns a dict of
    ticker -> per-ticker DataFrame (only tickers with usable data included).
    """
    raw = yf.download(
        tickers,
        period=config.HISTORY_PERIOD,
        interval=config.HISTORY_INTERVAL,
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )

    result = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker].dropna(subset=["Close", "High", "Volume"])
            if not df.empty:
                result[ticker] = df
    else:
        # yf.download collapses to a single-level frame when only one
        # ticker was requested.
        df = raw.dropna(subset=["Close", "High", "Volume"])
        if not df.empty and tickers:
            result[tickers[0]] = df
    return result


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates daily OHLCV into weekly bars (week ending Friday) -- no
    extra network call, just a local resample of data already downloaded.
    """
    weekly = df.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
    })
    return weekly.dropna(subset=["Close", "High", "Low"])


def _evaluate_ticker(
    ticker: str,
    df: pd.DataFrame,
    min_price: float | None = None,
    rsi_threshold: float = config.RSI_THRESHOLD,
    rsi_max: float = 100.0,
    volume_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
    periods: list[str] | None = None,
    timeframe: str = "daily",
) -> dict | None:
    """Evaluates one ticker's OHLCV against every scan condition.

    `timeframe` ("daily" or "weekly") selects which SMA/lookback/volume-
    averaging convention to apply -- see config.WEEKLY_* constants. Pass
    a weekly-resampled `df` (via _resample_weekly) together with
    timeframe="weekly" to run the same logic on weekly bars.
    """
    is_weekly = timeframe == "weekly"
    sma_fast = config.WEEKLY_SMA_FAST if is_weekly else config.SMA_FAST
    sma_slow = config.WEEKLY_SMA_SLOW if is_weekly else config.SMA_SLOW
    vol_avg_period = config.WEEKLY_VOLUME_AVG_PERIOD if is_weekly else config.VOLUME_AVG_PERIOD
    lookbacks = config.WEEKLY_RESISTANCE_LOOKBACKS if is_weekly else config.RESISTANCE_LOOKBACKS
    bar_52w = 52 if is_weekly else 252
    unit = "week" if is_weekly else "day"

    if len(df) < sma_fast:
        return None

    price = float(df["Close"].iloc[-1])

    # Strictly greater than min_price: a stock priced at exactly min_price
    # does NOT qualify (e.g. a ₹100.00 close is excluded, ₹100.01 is not).
    if min_price is not None and price <= min_price:
        return None

    if not indicators.is_uptrend(df, sma_fast=sma_fast, sma_slow=sma_slow):
        return None

    rsi = indicators.compute_rsi(df["Close"]).iloc[-1]
    if pd.isna(rsi) or not (rsi_threshold <= rsi <= rsi_max):
        return None

    vol_ratio = indicators.compute_volume_ratio(df["Volume"], avg_period=vol_avg_period)
    if pd.isna(vol_ratio) or vol_ratio <= volume_multiplier:
        return None

    signals = indicators.find_resistance_signals(df, near_pct=near_pct, periods=periods, lookbacks=lookbacks)
    category, best_signal = indicators.classify(signals)
    if category is None:
        return None

    if category == "Breakout":
        rank_score = vol_ratio * (rsi / 100) * (1 + best_signal["pct"] / 100)
        # Already triggered: the entry signal is now, at the current price.
        buy_level = price
    else:
        rank_score = vol_ratio * (rsi / 100) / (best_signal["pct"] + 0.1)
        # Not yet triggered: the entry is a confirmed close above resistance,
        # with a small buffer so a marginal poke through doesn't count.
        buy_level = best_signal["resistance"] * 1.005

    support = indicators.find_nearest_support(df, lookbacks=lookbacks)
    stop_loss = support * (1 - config.STOP_LOSS_BUFFER) if support is not None else buy_level * (1 - 5 * config.STOP_LOSS_BUFFER)
    week52_high = indicators.compute_52w_high(df, period=bar_52w)

    quality_score = indicators.compute_quality_score(
        df, category=category, signal_pct=best_signal["pct"], near_pct=near_pct,
        rsi=rsi, rsi_threshold=rsi_threshold, vol_ratio=vol_ratio, volume_multiplier=volume_multiplier,
        sma_fast=sma_fast, sma_slow=sma_slow,
    )

    tf_label = "Weekly" if is_weekly else "Daily"
    why = []
    if category == "Breakout":
        why.append(
            f"✓ Price closed above the {best_signal['period']} ({tf_label.lower()}) resistance level "
            f"(₹{best_signal['resistance']:.2f})"
        )
    else:
        why.append(
            f"✓ Price is within {near_pct*100:.1f}% of the {best_signal['period']} ({tf_label.lower()}) "
            f"resistance level (₹{best_signal['resistance']:.2f})"
        )
    if min_price is not None:
        why.append(f"✓ Price ₹{price:.2f} is above the minimum ₹{min_price:.2f}")
    why.append(f"✓ Volume ratio {vol_ratio:.2f}x meets the minimum {volume_multiplier:.2f}x ({unit}ly average)")
    why.append(f"✓ RSI {rsi:.0f} is within the configured range ({rsi_threshold:.0f}-{rsi_max:.0f})")
    trend_note = f"above the {sma_fast}-{unit} SMA"
    if len(df) >= sma_slow:
        trend_note += f", which is above the {sma_slow}-{unit} SMA"
    why.append(f"✓ Price is {trend_note}")

    return {
        "Ticker": ticker,
        "Category": category,
        "Signal": "🚀 Breakout" if category == "Breakout" else "👀 Near Breakout",
        "Timeframe": tf_label,
        "Current Price": round(price, 2),
        "Volume Ratio": round(float(vol_ratio), 2),
        "RSI": round(float(rsi), 1),
        "Quality Score": quality_score,
        "Buy Level": round(buy_level, 2),
        "Stop Loss": round(stop_loss, 2),
        "Resistance Period": best_signal["period"],
        "Resistance Level": round(best_signal["resistance"], 2),
        "52W High": round(week52_high, 2),
        "Support Level": round(support, 2) if support is not None else None,
        "% From Resistance": round(best_signal["pct"], 2),
        "Why Qualified": "\n".join(why),
        "_rank_score": rank_score,
    }


def _fetch_candidate_info(tickers: list[str]) -> dict:
    """For the (small) list of stocks that passed the technical filters,
    fetches P/E ratio, company name, and sector in parallel. This is the
    one place in the scan pipeline that calls yfinance's slow per-ticker
    `.info` -- never run it across a whole universe.
    """
    info_by_ticker = {}

    def fetch_one(t):
        try:
            info = yf.Ticker(t).get_info()
            return t, {
                "pe": info.get("trailingPE") or info.get("forwardPE"),
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
            }
        except Exception:
            return t, {"pe": None, "name": None, "sector": None}

    with ThreadPoolExecutor(max_workers=config.PE_FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch_one, t) for t in tickers]
        for fut in as_completed(futures):
            t, data = fut.result()
            info_by_ticker[t] = data

    return info_by_ticker


def evaluate_watchlist_ticker(
    ticker: str,
    df: pd.DataFrame,
    rsi_threshold: float = config.RSI_THRESHOLD,
    rsi_max: float = 100.0,
    volume_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
    min_price: float | None = None,
) -> dict:
    """Like _evaluate_ticker, but always returns a row (never None) so a
    watchlisted or searched-for stock keeps showing up even on days it
    doesn't pass the scan filters -- with a Status/Reason explaining
    exactly where it stands (used by both the Watchlist tab and the
    stock-search box).
    """
    if len(df) < config.SMA_FAST:
        return {"Ticker": ticker, "Status": "⚠️ No data", "Reason": "Not enough price history"}

    price = float(df["Close"].iloc[-1])
    uptrend = indicators.is_uptrend(df)
    rsi = indicators.compute_rsi(df["Close"]).iloc[-1]
    vol_ratio = indicators.compute_volume_ratio(df["Volume"])
    signals = indicators.find_resistance_signals(df, near_pct=near_pct)
    category, best_signal = indicators.classify(signals)

    reasons = []
    if min_price is not None and price <= min_price:
        reasons.append(f"price ₹{price:.2f} does not satisfy Price > ₹{min_price:.2f}")
    if not uptrend:
        reasons.append(f"not above SMA{config.SMA_FAST}/SMA{config.SMA_SLOW}")
    if pd.isna(rsi):
        reasons.append("RSI unavailable")
    elif not (rsi_threshold <= rsi <= rsi_max):
        reasons.append(f"RSI {rsi:.0f} outside configured range {rsi_threshold:.0f}-{rsi_max:.0f}")
    if pd.isna(vol_ratio):
        reasons.append("volume ratio unavailable")
    elif vol_ratio <= volume_multiplier:
        reasons.append(f"volume {vol_ratio:.1f}x <= {volume_multiplier:.1f}x")
    if category is None:
        reasons.append("price has not crossed/isn't near the calculated resistance level")

    # The Status badge reflects ALL scan criteria together, not resistance
    # alone -- a stock near resistance but without a volume spike is still
    # just "Watching", not "Breakout" (that would misleadingly imply it's
    # in today's actual scan results).
    fully_qualifies = not reasons

    if fully_qualifies:
        status = "🚀 Breakout" if category == "Breakout" else "👀 Near Breakout"
        resistance = best_signal["resistance"]
    else:
        status = "⏳ Watching"
        resistance = best_signal["resistance"] if best_signal else indicators.find_nearest_resistance(df)

    if resistance is not None:
        buy_level = price if (fully_qualifies and category == "Breakout") else resistance * 1.005
    else:
        buy_level = None

    support = indicators.find_nearest_support(df)
    if support is not None:
        stop_loss = support * (1 - config.STOP_LOSS_BUFFER)
    elif buy_level is not None:
        stop_loss = buy_level * (1 - 5 * config.STOP_LOSS_BUFFER)
    else:
        stop_loss = None
    week52_high = indicators.compute_52w_high(df)

    return {
        "Ticker": ticker,
        "Status": status,
        "Reason": "Meets all scan criteria" if not reasons else "Missing: " + ", ".join(reasons),
        "Current Price": round(price, 2),
        "RSI": round(float(rsi), 1) if not pd.isna(rsi) else None,
        "Volume Ratio": round(float(vol_ratio), 2) if not pd.isna(vol_ratio) else None,
        "Buy Level": round(buy_level, 2) if buy_level is not None else None,
        "Stop Loss": round(stop_loss, 2) if stop_loss is not None else None,
        "Resistance Level": round(resistance, 2) if resistance is not None else None,
        "52W High": round(week52_high, 2),
        "Support Level": round(support, 2) if support is not None else None,
    }


def get_watchlist_data(
    tickers: list[str],
    rsi_threshold: float = config.RSI_THRESHOLD,
    rsi_max: float = 100.0,
    volume_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
) -> pd.DataFrame:
    """Builds a live snapshot table for arbitrary watchlisted tickers,
    independent of any market's scan results.
    """
    columns = ["Ticker", "Company Name", "Sector", "Status", "Current Price", "P/E Ratio",
               "Volume Ratio", "RSI", "Buy Level", "Stop Loss", "Resistance Level",
               "52W High", "Support Level", "Reason"]
    if not tickers:
        return pd.DataFrame(columns=columns)

    history = _download_history(tickers)
    rows = []
    for ticker in tickers:
        df = history.get(ticker)
        if df is None:
            rows.append({"Ticker": ticker, "Status": "⚠️ No data", "Reason": "Price history unavailable"})
        else:
            rows.append(evaluate_watchlist_ticker(
                ticker, df, rsi_threshold=rsi_threshold, rsi_max=rsi_max,
                volume_multiplier=volume_multiplier, near_pct=near_pct,
            ))

    info_map = _fetch_candidate_info(tickers)
    for row in rows:
        info = info_map.get(row["Ticker"], {})
        pe = info.get("pe")
        row["P/E Ratio"] = round(pe, 2) if isinstance(pe, (int, float)) and not np.isnan(pe) else None
        row["Company Name"] = info.get("name") or row["Ticker"]
        row["Sector"] = info.get("sector") or "N/A"

    status_order = {"🚀 Breakout": 0, "👀 Near Breakout": 1, "⏳ Watching": 2, "⚠️ No data": 3}
    rows.sort(key=lambda r: status_order.get(r["Status"], 9))

    return pd.DataFrame(rows, columns=columns)


def summarize_sectors(breakout_df: pd.DataFrame, near_df: pd.DataFrame) -> dict:
    """Counts qualifying stocks (Breakout + Near Breakout) per sector,
    computed dynamically from this scan's actual results -- never fabricated.
    """
    combined = pd.concat([breakout_df, near_df], ignore_index=True)
    if combined.empty or "Sector" not in combined.columns:
        return {}
    counts = combined["Sector"].fillna("N/A").replace("", "N/A").value_counts()
    return counts.to_dict()


def normalize_ticker(raw: str, market: str) -> str:
    """Turns free-typed search input ("reliance", "RELIANCE.NS", "aapl")
    into a yfinance-ready ticker for the given market.
    """
    t = raw.strip().upper()
    if market in ("NSE", "NSE_ALL") and not t.endswith(".NS"):
        t = f"{t}.NS"
    return t


def search_ticker(
    raw_ticker: str,
    market: str,
    rsi_threshold: float = config.RSI_THRESHOLD,
    rsi_max: float = 100.0,
    volume_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
    min_price: float | None = None,
) -> dict | None:
    """Looks up any single ticker on demand -- independent of whether it's
    in the selected universe's scan results -- and evaluates it under the
    current strategy settings. Returns None only if no price data could be
    found at all (e.g. an invalid symbol); otherwise always returns a row
    (same shape as a watchlist row) explaining whether/why it qualifies.
    """
    ticker = normalize_ticker(raw_ticker, market)
    if min_price is None:
        min_price = config.MIN_PRICE_INR if market in ("NSE", "NSE_ALL") else None

    history = _download_history([ticker])
    df = history.get(ticker)
    if df is None:
        return None

    row = evaluate_watchlist_ticker(
        ticker, df, rsi_threshold=rsi_threshold, rsi_max=rsi_max, volume_multiplier=volume_multiplier,
        near_pct=near_pct, min_price=min_price,
    )
    info = _fetch_candidate_info([ticker]).get(ticker, {})
    pe = info.get("pe")
    row["P/E Ratio"] = round(pe, 2) if isinstance(pe, (int, float)) and not np.isnan(pe) else None
    row["Company Name"] = info.get("name") or ticker
    row["Sector"] = info.get("sector") or "N/A"
    return row


def scan_market(
    market: str,
    progress_callback=None,
    min_price: float | None = None,
    rsi_threshold: float = config.RSI_THRESHOLD,
    rsi_max: float = 100.0,
    volume_multiplier: float = config.VOLUME_SPIKE_MULTIPLIER,
    near_pct: float = config.NEAR_BREAKOUT_PCT,
    periods: list[str] | None = None,
) -> dict:
    """Runs the full scan for a market ("NSE", "NSE_ALL", or "NYSE") on
    BOTH the daily timeframe and a weekly timeframe (resampled locally
    from the same downloaded daily history -- no extra network calls).

    rsi_threshold / volume_multiplier / near_pct let the caller (the
    sidebar "Strategy" controls) override the config.py defaults for a
    single scan, applied identically to both timeframes. min_price
    defaults to config.MIN_PRICE_INR for every NSE market (Nifty 500 and
    All Stocks alike) and to no floor for NYSE, unless explicitly
    overridden. A price exactly equal to min_price does NOT qualify --
    see _evaluate_ticker.

    Returns a dict with:
      breakout / near_breakout: DataFrames (daily timeframe)
      weekly_breakout / weekly_near_breakout: DataFrames (weekly timeframe)
      universe_size: int   -- tickers in the selected universe
      scanned: int         -- tickers with usable price history
      eligible: int        -- of those scanned, how many pass the mandatory
                               price filter (before RSI/volume/resistance)
      advances / declines / unchanged: int -- today's close vs prior close,
                               across all successfully scanned tickers
      source: str  (where the ticker list came from)
    """
    tickers, source = universe.get_universe(market)
    if min_price is None:
        min_price = config.MIN_PRICE_INR if market in ("NSE", "NSE_ALL") else None

    if progress_callback:
        progress_callback(0.05, f"Downloading price history for {len(tickers)} tickers...")

    history = _download_history(tickers)

    if progress_callback:
        progress_callback(0.45, f"Computing daily indicators for {len(history)} tickers...")

    eligible = 0
    advances = declines = unchanged = 0
    daily_candidates = []
    weekly_candidates = []
    for ticker, df in history.items():
        last_close = float(df["Close"].iloc[-1])
        if min_price is None or last_close > min_price:
            eligible += 1

        if len(df) >= 2:
            prev_close = float(df["Close"].iloc[-2])
            if last_close > prev_close:
                advances += 1
            elif last_close < prev_close:
                declines += 1
            else:
                unchanged += 1

        row = _evaluate_ticker(
            ticker, df, min_price=min_price,
            rsi_threshold=rsi_threshold, rsi_max=rsi_max, volume_multiplier=volume_multiplier, near_pct=near_pct,
            periods=periods, timeframe="daily",
        )
        if row is not None:
            daily_candidates.append(row)

        weekly_df = _resample_weekly(df)
        weekly_row = _evaluate_ticker(
            ticker, weekly_df, min_price=min_price,
            rsi_threshold=rsi_threshold, rsi_max=rsi_max, volume_multiplier=volume_multiplier, near_pct=near_pct,
            periods=periods, timeframe="weekly",
        )
        if weekly_row is not None:
            weekly_candidates.append(weekly_row)

    if progress_callback:
        progress_callback(0.75, "Fetching company info for candidates...")

    all_candidates = daily_candidates + weekly_candidates
    all_tickers = sorted({c["Ticker"] for c in all_candidates})
    info_map = _fetch_candidate_info(all_tickers) if all_tickers else {}
    for c in all_candidates:
        info = info_map.get(c["Ticker"], {})
        pe = info.get("pe")
        c["P/E Ratio"] = round(pe, 2) if isinstance(pe, (int, float)) and not np.isnan(pe) else None
        c["Company Name"] = info.get("name") or c["Ticker"]
        c["Sector"] = info.get("sector") or "N/A"

    if progress_callback:
        progress_callback(0.95, "Ranking results...")

    columns = ["Ticker", "Company Name", "Sector", "Signal", "Timeframe", "Current Price", "P/E Ratio",
               "Volume Ratio", "RSI", "Quality Score", "Buy Level", "Stop Loss", "Resistance Period",
               "Resistance Level", "52W High", "% From Resistance", "Support Level", "Why Qualified"]

    def _build_table(candidates, category):
        rows = sorted(
            (c for c in candidates if c["Category"] == category),
            key=lambda c: c["_rank_score"], reverse=True,
        )
        df_out = pd.DataFrame(rows, columns=columns + ["_rank_score"])
        if not df_out.empty:
            df_out.insert(0, "Rank", range(1, len(df_out) + 1))
            df_out = df_out.drop(columns=["_rank_score"])
        return df_out

    breakout_df = _build_table(daily_candidates, "Breakout")
    near_df = _build_table(daily_candidates, "Near Breakout")
    weekly_breakout_df = _build_table(weekly_candidates, "Breakout")
    weekly_near_df = _build_table(weekly_candidates, "Near Breakout")

    if progress_callback:
        progress_callback(1.0, "Done.")

    return {
        "breakout": breakout_df,
        "near_breakout": near_df,
        "weekly_breakout": weekly_breakout_df,
        "weekly_near_breakout": weekly_near_df,
        "universe_size": len(tickers),
        "scanned": len(history),
        "eligible": eligible,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "min_price": min_price,
        "source": source,
    }
