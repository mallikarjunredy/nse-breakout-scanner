"""Daily Rising Channel: Pre-Breakout & Breakout Scanner.

Fits an upward-sloping support/resistance channel to a stock's daily
price action (confirmed swing highs/lows, least-squares trend lines,
parallelism/containment/touch-count requirements) and classifies each
candidate as:

- "Pre-Breakout Watchlist" -- inside a valid rising channel, close to
  (but not through) resistance.
- "Confirmed Breakout" -- closed above the channel's *projected*
  resistance (frozen using data through the *previous* session, to
  avoid any lookahead) with enough distance and volume.
- "Breakout — Volume Unconfirmed" -- same price condition as a
  confirmed breakout, but the volume requirement wasn't met.

Every threshold is adjustable (see `default_params()` / config.py's
RISING_CHANNEL_* defaults) -- unlike the Upside Buy Movement strategy,
this one is designed to be tuned from the UI. Uses split/dividend-
adjusted OHLC (`scanner.download_history(..., auto_adjust=True)`) so a
stock split doesn't fake a channel breakdown.

No randomness, no invented matches: a ticker with no valid channel and
no breakout signal simply doesn't appear in any result table.
"""

import datetime

import numpy as np
import pandas as pd

from . import config, indicators, scanner, universe


def default_params() -> dict:
    """The page's slider/checkbox defaults -- every value here can be
    overridden by the caller (the Streamlit page passes live widget
    values instead of these defaults once the user touches a control).
    """
    return {
        "pivot_n": config.RISING_CHANNEL_PIVOT_N,
        "lookback_min": config.RISING_CHANNEL_LOOKBACK_MIN,
        "lookback_max": config.RISING_CHANNEL_LOOKBACK_MAX,
        "lookback_step": config.RISING_CHANNEL_LOOKBACK_STEP,
        "min_touches": config.RISING_CHANNEL_MIN_TOUCHES,
        "min_touch_separation": config.RISING_CHANNEL_MIN_TOUCH_SEPARATION,
        "touch_tolerance_atr_mult": config.RISING_CHANNEL_TOUCH_TOLERANCE_ATR_MULT,
        "parallel_tolerance_pct": config.RISING_CHANNEL_PARALLEL_TOLERANCE_PCT,
        "min_containment_pct": config.RISING_CHANNEL_MIN_CONTAINMENT_PCT,
        "atr_period": config.RISING_CHANNEL_ATR_PERIOD,
        "prebreakout_distance_min_pct": config.RISING_CHANNEL_PREBREAKOUT_DISTANCE_MIN_PCT,
        "prebreakout_distance_max_pct": config.RISING_CHANNEL_PREBREAKOUT_DISTANCE_MAX_PCT,
        "prebreakout_rsi_min": config.RISING_CHANNEL_PREBREAKOUT_RSI_MIN,
        "prebreakout_rsi_max": config.RISING_CHANNEL_PREBREAKOUT_RSI_MAX,
        "sma_fast": config.RISING_CHANNEL_SMA_FAST,
        "sma_slow": config.RISING_CHANNEL_SMA_SLOW,
        "sma_rising_lookback": config.RISING_CHANNEL_SMA_RISING_LOOKBACK,
        "range_recent_days": config.RISING_CHANNEL_RANGE_RECENT_DAYS,
        "range_prior_days": config.RISING_CHANNEL_RANGE_PRIOR_DAYS,
        "volume_recent_days": config.RISING_CHANNEL_VOLUME_RECENT_DAYS,
        "volume_prior_days": config.RISING_CHANNEL_VOLUME_PRIOR_DAYS,
        "breakout_min_pct": config.RISING_CHANNEL_BREAKOUT_MIN_PCT,
        "breakout_atr_mult": config.RISING_CHANNEL_BREAKOUT_ATR_MULT,
        "breakout_volume_mult": config.RISING_CHANNEL_BREAKOUT_VOLUME_MULT,
        "breakout_volume_avg_period": config.RISING_CHANNEL_BREAKOUT_VOLUME_AVG_PERIOD,
        "use_range_contraction_filter": False,
        "use_volume_contraction_filter": False,
    }


# ---------------------------------------------------------------------------
# Swing points and channel fitting
# ---------------------------------------------------------------------------

def find_swing_points(df: pd.DataFrame, n: int) -> tuple[list[int], list[int]]:
    """Confirmed swing highs/lows within `df` (0-based positional
    indices): bar i is a swing high if its High strictly exceeds the
    High of the n bars immediately before AND after it (swing low:
    mirrored on Low). A swing at position i only exists in the returned
    list once n bars after it are present in `df` -- pass only the data
    that was actually available "as of" the date you're evaluating, and
    this is automatically leak-free.
    """
    if len(df) < 2 * n + 1:
        return [], []
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    swing_highs, swing_lows = [], []
    for i in range(n, len(df) - n):
        if highs[i] > highs[i - n:i].max() and highs[i] > highs[i + 1:i + n + 1].max():
            swing_highs.append(i)
        if lows[i] < lows[i - n:i].min() and lows[i] < lows[i + 1:i + n + 1].min():
            swing_lows.append(i)
    return swing_highs, swing_lows


def _fit_line(indices: list[int], prices: list[float]) -> tuple[float, float] | None:
    if len(indices) < 2:
        return None
    x = np.array(indices, dtype=float)
    if np.allclose(x, x[0]):
        return None
    slope, intercept = np.polyfit(x, np.array(prices, dtype=float), 1)
    return float(slope), float(intercept)


def _valid_touches(indices, prices, slope, intercept, tolerance, min_separation) -> list[int]:
    hits = sorted(
        idx for idx, price in zip(indices, prices)
        if abs((slope * idx + intercept) - price) <= tolerance
    )
    filtered = []
    for idx in hits:
        if not filtered or idx - filtered[-1] >= min_separation:
            filtered.append(idx)
    return filtered


def _try_channel(df: pd.DataFrame, start: int, end: int, atr_at_end: float | None, params: dict) -> dict | None:
    """Attempts to fit a valid rising channel to df.iloc[start:end+1].
    Returns None if any hard requirement fails.
    """
    if atr_at_end is None or pd.isna(atr_at_end) or atr_at_end <= 0:
        return None

    window = df.iloc[start:end + 1]
    local_highs, local_lows = find_swing_points(window, params["pivot_n"])
    sh_idx = [start + i for i in local_highs]
    sl_idx = [start + i for i in local_lows]
    if len(sh_idx) < params["min_touches"] or len(sl_idx) < params["min_touches"]:
        return None

    sh_prices = [float(df["High"].iloc[i]) for i in sh_idx]
    sl_prices = [float(df["Low"].iloc[i]) for i in sl_idx]

    res_fit = _fit_line(sh_idx, sh_prices)
    sup_fit = _fit_line(sl_idx, sl_prices)
    if res_fit is None or sup_fit is None:
        return None
    slope_r, intercept_r = res_fit
    slope_s, intercept_s = sup_fit

    if slope_r <= 0 or slope_s <= 0:
        return None

    avg_slope = (abs(slope_r) + abs(slope_s)) / 2
    if avg_slope <= 0:
        return None
    slope_diff_pct = abs(slope_r - slope_s) / avg_slope * 100
    if slope_diff_pct > params["parallel_tolerance_pct"]:
        return None

    tol = params["touch_tolerance_atr_mult"] * atr_at_end

    res_touches = _valid_touches(sh_idx, sh_prices, slope_r, intercept_r, tol, params["min_touch_separation"])
    sup_touches = _valid_touches(sl_idx, sl_prices, slope_s, intercept_s, tol, params["min_touch_separation"])
    if len(res_touches) < params["min_touches"] or len(sup_touches) < params["min_touches"]:
        return None

    idx_range = list(range(start, end + 1))
    res_vals = [slope_r * i + intercept_r for i in idx_range]
    sup_vals = [slope_s * i + intercept_s for i in idx_range]
    if any(r <= s for r, s in zip(res_vals, sup_vals)):
        return None

    closes = df["Close"].iloc[start:end + 1].to_numpy()
    contained = sum((sup_vals[k] - tol) <= closes[k] <= (res_vals[k] + tol) for k in range(len(closes)))
    containment_pct = contained / len(closes) * 100 if len(closes) else 0.0
    if containment_pct < params["min_containment_pct"]:
        return None

    ref_price = float(df["Close"].iloc[end])
    if ref_price <= 0:
        return None

    def _rel_rmse(idxs, prices, slope, intercept):
        errs = [(slope * idx + intercept) - price for idx, price in zip(idxs, prices)]
        return (sum(e * e for e in errs) / len(errs)) ** 0.5 / ref_price

    fit_error_pct = (_rel_rmse(sh_idx, sh_prices, slope_r, intercept_r)
                      + _rel_rmse(sl_idx, sl_prices, slope_s, intercept_s)) / 2 * 100

    higher_highs = sh_prices[-1] > sh_prices[0] if len(sh_prices) >= 2 else False
    higher_lows = sl_prices[-1] > sl_prices[0] if len(sl_prices) >= 2 else False

    # Deterministic selection score (documented): more touches and higher
    # containment are better, a larger fit error is worse, and a channel
    # that also shows the preferred higher-high/higher-low shape gets a
    # small bonus. Units are chosen so no single term dominates by
    # construction alone (touches ~0-20, containment 0-100, fit error
    # typically a few %, scaled up to matter).
    score = (
        (len(res_touches) + len(sup_touches)) * 5
        + containment_pct
        - fit_error_pct * 10
        + (5 if higher_highs else 0)
        + (5 if higher_lows else 0)
    )

    return {
        "start": start, "end": end, "window": end - start + 1,
        "slope_r": slope_r, "intercept_r": intercept_r,
        "slope_s": slope_s, "intercept_s": intercept_s,
        "swing_highs": sh_idx, "swing_lows": sl_idx,
        "res_touches": res_touches, "sup_touches": sup_touches,
        "containment_pct": containment_pct, "fit_error_pct": fit_error_pct,
        "higher_highs": higher_highs, "higher_lows": higher_lows,
        "score": score,
    }


def select_best_channel(df: pd.DataFrame, as_of_idx: int, params: dict) -> dict | None:
    """Searches lookback windows from lookback_min to lookback_max
    (step lookback_step), all ending exactly at `as_of_idx`, and returns
    the single highest-scoring valid channel, or None if no window
    produces a valid one. Deterministic: same inputs always produce the
    same choice (no randomness, ties broken by whichever window was
    tried first at an equal score -- in practice `score` is a continuous
    float so exact ties are rare).
    """
    if as_of_idx < 0 or as_of_idx >= len(df):
        return None
    atr_series = indicators.compute_atr(df, period=params["atr_period"])
    atr_at_end = float(atr_series.iloc[as_of_idx]) if pd.notna(atr_series.iloc[as_of_idx]) else None

    best = None
    lo, hi, step = params["lookback_min"], params["lookback_max"], params["lookback_step"]
    for window_len in range(lo, hi + 1, step):
        start = as_of_idx - window_len + 1
        if start < 0:
            continue
        candidate = _try_channel(df, start, as_of_idx, atr_at_end, params)
        if candidate is not None and (best is None or candidate["score"] > best["score"]):
            best = candidate
    return best


def _channel_value(channel: dict, idx: int, boundary: str) -> float:
    if boundary == "resistance":
        return channel["slope_r"] * idx + channel["intercept_r"]
    return channel["slope_s"] * idx + channel["intercept_s"]


# ---------------------------------------------------------------------------
# Per-ticker evaluation
# ---------------------------------------------------------------------------

def evaluate_ticker(ticker: str, df: pd.DataFrame, params: dict, min_price: float) -> dict | None:
    """Evaluates one ticker for both a confirmed-breakout signal (using a
    channel frozen through yesterday) and a pre-breakout setup (using
    today's own channel). Returns None if neither applies -- a ticker
    with no valid channel and no breakout is simply not a match, not an
    error.
    """
    n = len(df)
    # Absolute minimum bars needed to even attempt the smallest channel
    # window plus its surrounding lookbacks -- below this, no evaluation
    # is meaningful at all. This is a practical floor, separate from the
    # (non-blocking) "insufficient history" flag below, which compares
    # against the *requested* 250-session target instead.
    min_required = (
        params["lookback_min"] + params["pivot_n"] * 2
        + max(
            params["breakout_volume_avg_period"],
            params["range_recent_days"] + params["range_prior_days"],
            params["volume_recent_days"] + params["volume_prior_days"],
        )
        + 5
    )
    if n < min_required:
        return None

    today_idx = n - 1
    price_today = float(df["Close"].iloc[today_idx])
    if price_today <= min_price:
        return None

    insufficient_history = n < config.RISING_CHANNEL_MIN_HISTORY_SESSIONS
    missing_volume = bool(df["Volume"].iloc[-5:].isna().any()) or bool((df["Volume"].iloc[-5:] == 0).all())
    last_bar_date = df.index[-1].date()
    stale_data = (datetime.date.today() - last_bar_date).days > 5  # generous weekend/holiday buffer

    rsi_series = indicators.compute_rsi(df["Close"])
    sma_fast_series = indicators.compute_sma(df["Close"], params["sma_fast"])
    sma_slow_series = indicators.compute_sma(df["Close"], params["sma_slow"])
    atr_series = indicators.compute_atr(df, period=params["atr_period"])
    rsi_today = float(rsi_series.iloc[today_idx]) if pd.notna(rsi_series.iloc[today_idx]) else None

    vol_period = params["breakout_volume_avg_period"]
    vol_ratio_today = None
    if today_idx - vol_period >= 0:
        avg_vol = float(df["Volume"].iloc[today_idx - vol_period:today_idx].mean())
        if avg_vol > 0:
            vol_ratio_today = float(df["Volume"].iloc[today_idx]) / avg_vol

    row = None

    # --- Confirmed breakout / volume-unconfirmed check -------------------
    # The channel is fit using data available *through yesterday only*,
    # then its (frozen) slope/intercept are projected one session forward
    # to evaluate today's close -- this is what "freeze coordinates
    # before evaluating the signal candle" means, and what prevents
    # today's own price action from leaking into the channel it's being
    # tested against.
    if today_idx >= 1:
        prev_idx = today_idx - 1
        prev_channel = select_best_channel(df, prev_idx, params)
        if prev_channel is not None:
            res_prev = _channel_value(prev_channel, prev_idx, "resistance")
            price_prev = float(df["Close"].iloc[prev_idx])
            if price_prev <= res_prev:
                res_today_proj = _channel_value(prev_channel, today_idx, "resistance")
                sup_today_proj = _channel_value(prev_channel, today_idx, "support")
                atr_prev = float(atr_series.iloc[prev_idx]) if pd.notna(atr_series.iloc[prev_idx]) else None
                if atr_prev is not None and res_today_proj > 0:
                    min_clearance = max(
                        params["breakout_min_pct"] / 100 * res_today_proj,
                        params["breakout_atr_mult"] * atr_prev,
                    )
                    if price_today > res_today_proj + min_clearance:
                        confirmed = vol_ratio_today is not None and vol_ratio_today >= params["breakout_volume_mult"]
                        status = "Confirmed Breakout" if confirmed else "Breakout — Volume Unconfirmed"
                        distance_pct = (res_today_proj - price_today) / res_today_proj * 100
                        why = [
                            f"✓ Previous close (₹{price_prev:.2f}) was at/below the channel's resistance (₹{res_prev:.2f})",
                            f"✓ Today's close (₹{price_today:.2f}) cleared the projected resistance "
                            f"(₹{res_today_proj:.2f}) by more than max(0.5% of resistance, 0.25×ATR14)",
                        ]
                        if vol_ratio_today is not None:
                            tick = "✓" if confirmed else "✗"
                            why.append(
                                f"{tick} Volume {vol_ratio_today:.2f}x the prior {vol_period}-session average "
                                f"({'meets' if confirmed else 'below'} the {params['breakout_volume_mult']:.1f}x requirement)"
                            )
                        else:
                            why.append("✗ Not enough prior sessions to compute the volume-confirmation ratio")
                        row = {
                            "Ticker": ticker,
                            "Setup Status": status,
                            "Signal Date": df.index[today_idx].strftime("%Y-%m-%d"),
                            "Current Price": round(price_today, 2),
                            "Resistance Level": round(res_today_proj, 2),
                            "Support Level": round(sup_today_proj, 2),
                            "% Below Resistance": round(distance_pct, 2),
                            "RSI": round(rsi_today, 1) if rsi_today is not None else None,
                            "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
                            "Channel Age": prev_channel["window"],
                            "Resistance Touches": len(prev_channel["res_touches"]),
                            "Support Touches": len(prev_channel["sup_touches"]),
                            "Why Qualified": "\n".join(why),
                            "_channel": prev_channel,
                            "_signal_idx": today_idx,
                        }

    # --- Pre-breakout check (channel as of today) -------------------------
    if row is None:
        channel = select_best_channel(df, today_idx, params)
        if channel is not None:
            res_today = _channel_value(channel, today_idx, "resistance")
            sup_today = _channel_value(channel, today_idx, "support")
            if res_today > 0 and price_today <= res_today:
                distance_pct = (res_today - price_today) / res_today * 100
                if params["prebreakout_distance_min_pct"] <= distance_pct <= params["prebreakout_distance_max_pct"]:
                    sma_fast_now = sma_fast_series.iloc[today_idx]
                    sma_slow_now = sma_slow_series.iloc[today_idx]
                    lb = params["sma_rising_lookback"]
                    sma_fast_prior = sma_fast_series.iloc[today_idx - lb] if today_idx - lb >= 0 else None
                    sma_slow_prior = sma_slow_series.iloc[today_idx - lb] if today_idx - lb >= 0 else None

                    base_ok = (
                        pd.notna(sma_fast_now) and pd.notna(sma_slow_now)
                        and price_today > sma_fast_now and price_today > sma_slow_now
                        and sma_fast_prior is not None and pd.notna(sma_fast_prior)
                        and sma_slow_prior is not None and pd.notna(sma_slow_prior)
                        and sma_fast_now > sma_fast_prior and sma_slow_now > sma_slow_prior
                        and rsi_today is not None
                        and params["prebreakout_rsi_min"] <= rsi_today <= params["prebreakout_rsi_max"]
                    )

                    range_ok = True
                    if base_ok and params.get("use_range_contraction_filter"):
                        rr, rp = params["range_recent_days"], params["range_prior_days"]
                        if today_idx - rr - rp + 1 >= 0:
                            recent_slice = df.iloc[today_idx - rr + 1:today_idx + 1]
                            prior_slice = df.iloc[today_idx - rr - rp + 1:today_idx - rr + 1]
                            recent_range = (recent_slice["High"].max() - recent_slice["Low"].min()) / recent_slice["Close"].mean()
                            prior_range = (prior_slice["High"].max() - prior_slice["Low"].min()) / prior_slice["Close"].mean()
                            range_ok = recent_range < prior_range
                        else:
                            range_ok = False

                    volume_ok = True
                    if base_ok and range_ok and params.get("use_volume_contraction_filter"):
                        vr, vp = params["volume_recent_days"], params["volume_prior_days"]
                        if today_idx - vr - vp + 1 >= 0:
                            recent_vol = df["Volume"].iloc[today_idx - vr + 1:today_idx + 1].mean()
                            prior_vol = df["Volume"].iloc[today_idx - vr - vp + 1:today_idx - vr + 1].mean()
                            volume_ok = recent_vol < prior_vol
                        else:
                            volume_ok = False

                    if base_ok and range_ok and volume_ok:
                        why = [
                            f"✓ Close (₹{price_today:.2f}) is {distance_pct:.2f}% below the projected "
                            f"resistance (₹{res_today:.2f})",
                            f"✓ Close above rising SMA20 (₹{float(sma_fast_now):.2f}) and "
                            f"SMA50 (₹{float(sma_slow_now):.2f})",
                            f"✓ RSI {rsi_today:.0f} within the healthy 50-65 zone",
                            f"✓ Rising channel confirmed with {len(channel['res_touches'])} resistance / "
                            f"{len(channel['sup_touches'])} support touches over {channel['window']} sessions",
                        ]
                        if params.get("use_range_contraction_filter"):
                            why.append("✓ 10-session range narrower than the preceding 30 sessions")
                        if params.get("use_volume_contraction_filter"):
                            why.append("✓ 5-session average volume below the preceding 60-session average")
                        row = {
                            "Ticker": ticker,
                            "Setup Status": "Pre-Breakout Watchlist",
                            "Signal Date": df.index[today_idx].strftime("%Y-%m-%d"),
                            "Current Price": round(price_today, 2),
                            "Resistance Level": round(res_today, 2),
                            "Support Level": round(sup_today, 2),
                            "% Below Resistance": round(distance_pct, 2),
                            "RSI": round(rsi_today, 1) if rsi_today is not None else None,
                            "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
                            "Channel Age": channel["window"],
                            "Resistance Touches": len(channel["res_touches"]),
                            "Support Touches": len(channel["sup_touches"]),
                            "Why Qualified": "\n".join(why),
                            "_channel": channel,
                            "_signal_idx": today_idx,
                        }

    if row is None:
        return None
    row["_insufficient_history"] = insufficient_history
    row["_missing_volume"] = missing_volume
    row["_stale_data"] = stale_data
    return row


# ---------------------------------------------------------------------------
# Bulk scan orchestration
# ---------------------------------------------------------------------------

_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Support Level", "% Below Resistance", "RSI", "Volume Ratio",
    "Channel Age", "Resistance Touches", "Support Touches", "Why Qualified",
]

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All NSE Stocks (₹50+)"),
}


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["% Below Resistance"] if r["% Below Resistance"] is not None else 999)
    df_out = pd.DataFrame(filtered, columns=[c for c in _DISPLAY_COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_DISPLAY_COLUMNS)
    return df_out


def scan_rising_channel(
    universe_name: str = "nifty500",
    min_price: float | None = None,
    params: dict | None = None,
    progress_callback=None,
) -> dict:
    """Runs the Daily Rising Channel strategy over a chosen universe.
    Uses split/dividend-adjusted OHLC (see scanner.download_history's
    auto_adjust docstring) so a stock split doesn't fake a channel break.
    """
    if min_price is None:
        min_price = config.RISING_CHANNEL_MIN_PRICE_INR
    if params is None:
        params = default_params()
    if universe_name not in _UNIVERSE_LOADERS:
        raise ValueError(f"Unknown universe_name: {universe_name}")
    loader, universe_label = _UNIVERSE_LOADERS[universe_name]

    tickers, source = loader()

    if progress_callback:
        progress_callback(0.05, f"Downloading adjusted price history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers, auto_adjust=True)

    rows = []
    total = len(history)
    for i, (ticker, df) in enumerate(history.items()):
        row = evaluate_ticker(ticker, df, params, min_price)
        if row is not None:
            rows.append(row)
        if progress_callback and total:
            progress_callback(0.1 + 0.75 * (i + 1) / total, f"Evaluating {ticker}...")

    if progress_callback:
        progress_callback(0.9, "Fetching company info for candidates...")
    all_tickers = sorted({r["Ticker"] for r in rows})
    info_map = scanner.fetch_candidate_info(all_tickers) if all_tickers else {}
    for r in rows:
        info = info_map.get(r["Ticker"], {})
        r["Company Name"] = info.get("name") or r["Ticker"]

    last_dates = [df.index[-1].date() for df in history.values() if len(df)]
    data_asof_date = max(last_dates) if last_dates else None

    if progress_callback:
        progress_callback(1.0, "Done.")

    return {
        "pre_breakout": _build_table(rows, "Pre-Breakout Watchlist"),
        "confirmed_breakout": _build_table(rows, "Confirmed Breakout"),
        "volume_unconfirmed": _build_table(rows, "Breakout — Volume Unconfirmed"),
        "channels": {r["Ticker"]: r["_channel"] for r in rows},
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "universe_label": universe_label,
        "data_asof_date": data_asof_date,
        "insufficient_history_count": sum(1 for r in rows if r["_insufficient_history"]),
        "missing_volume_count": sum(1 for r in rows if r["_missing_volume"]),
        "stale_data_count": sum(1 for r in rows if r["_stale_data"]),
    }


# ---------------------------------------------------------------------------
# Charting
# ---------------------------------------------------------------------------

def build_channel_chart(df: pd.DataFrame, channel: dict, signal_idx: int, ticker: str):
    """Candlestick + the sloping channel used at signal time (frozen --
    this never changes when later data arrives) + swing-point markers +
    the highlighted signal candle + SMA20/SMA50 on the price panel, with
    Volume and RSI(14) as separate synced panels below.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    start, end = channel["start"], channel["end"]
    plot_start = max(0, start - 10)
    plot_end = min(len(df) - 1, max(end, signal_idx) + 5)
    plot_df = df.iloc[plot_start:plot_end + 1]

    sma_fast = indicators.compute_sma(df["Close"], config.RISING_CHANNEL_SMA_FAST).iloc[plot_start:plot_end + 1]
    sma_slow = indicators.compute_sma(df["Close"], config.RISING_CHANNEL_SMA_SLOW).iloc[plot_start:plot_end + 1]
    rsi = indicators.compute_rsi(df["Close"]).iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, row_heights=[0.55, 0.2, 0.25], vertical_spacing=0.05,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Rising Channel", "Volume", "RSI(14)"),
    )

    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)

    # The channel's own fitted window may end before the signal (a
    # breakout's channel is frozen through *yesterday*) -- extend the
    # drawn lines up to the signal candle so the projected level used for
    # the decision is visible, not just the historical fit.
    line_end = max(end, signal_idx)
    line_idx = list(range(start, line_end + 1))
    line_dates = [df.index[i] for i in line_idx]
    res_vals = [channel["slope_r"] * i + channel["intercept_r"] for i in line_idx]
    sup_vals = [channel["slope_s"] * i + channel["intercept_s"] for i in line_idx]
    fig.add_trace(go.Scatter(
        x=line_dates, y=res_vals, mode="lines", name="Resistance (channel)",
        line=dict(color="#4FD1E8", width=2),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=line_dates, y=sup_vals, mode="lines", name="Support (channel)",
        line=dict(color="#4FD1E8", width=2, dash="dot"),
    ), row=1, col=1)

    sh_dates = [df.index[i] for i in channel["swing_highs"]]
    sh_prices = [float(df["High"].iloc[i]) for i in channel["swing_highs"]]
    fig.add_trace(go.Scatter(
        x=sh_dates, y=sh_prices, mode="markers", name="Swing High",
        marker=dict(color="#FF6B6B", size=9, symbol="triangle-down"),
    ), row=1, col=1)
    sl_dates = [df.index[i] for i in channel["swing_lows"]]
    sl_prices = [float(df["Low"].iloc[i]) for i in channel["swing_lows"]]
    fig.add_trace(go.Scatter(
        x=sl_dates, y=sl_prices, mode="markers", name="Swing Low",
        marker=dict(color="#3ECF8E", size=9, symbol="triangle-up"),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=sma_fast.index, y=sma_fast, mode="lines", name="SMA20", line=dict(color="#FFB020", width=1.3),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=sma_slow.index, y=sma_slow, mode="lines", name="SMA50", line=dict(color="#B26BFF", width=1.3),
    ), row=1, col=1)

    sig_date = df.index[signal_idx]
    sig_high = float(df["High"].iloc[signal_idx])
    fig.add_annotation(
        x=sig_date, y=sig_high, text="Signal", showarrow=True, arrowhead=2, yshift=18,
        font=dict(color="#EAF2FA"), arrowcolor="#FFB020", row=1, col=1,
    )

    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color="#4F7CFF"), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=rsi.index, y=rsi, mode="lines", name="RSI(14)", line=dict(color="#4FD1E8", width=1.5),
    ), row=3, col=1)
    fig.add_hline(y=65, line_dash="dot", line_color="#FF6B6B", row=3, col=1)
    fig.add_hline(y=50, line_dash="dot", line_color="#3ECF8E", row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=700, margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    return fig
