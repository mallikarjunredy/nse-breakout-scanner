"""Breakout Flag Continuation: a stock that already broke out above a
meaningful resistance on volume, pulled back only shallowly (a "flag"),
held above that resistance through the pullback, and is now resuming or
has resumed toward a fresh high. Built from a real example (RPG Life
Sciences) that fell through the gaps of every other strategy: a huge,
volume-confirmed breakout candle (a sharp gap, not a smooth climb) made
Daily Rising Channel's and Triple EMA Golden Cross's channel-fit gates
fail (a single gap candle breaks the parallel-channel geometry those
need), while Resistance Breakout's own 8%+ deep-base requirement doesn't
fit a shallow 2-10% flag either.

Distinct from Resistance Breakout in two ways: the resistance here is a
plain trailing rolling-max High (not a swing-point search), so a single
sharp gap candle doesn't break the fit the way it can for the channel-
based strategies; and the pullback this strategy wants is *shallow*
(2-10% by default) and *recent* (within `breakout_lookback_days` of a
fresh, volume-confirmed breakout), not the deep multi-month base
Resistance Breakout is built around.

Adjustable from the UI (like Rising Channel / Resistance Breakout /
Triple EMA Golden Cross) -- see default_params() / config.py's
BREAKOUT_FLAG_* defaults -- since this pattern came from one real
example, not a numbered spec.

Three mutually-exclusive stages:

- "Flag Watchlist" -- a fresh, volume-confirmed breakout happened, the
  pullback since then is shallow and still holding above the breakout
  resistance (within a small retest tolerance), but today's close
  hasn't yet challenged the post-breakout high again.
- "Confirmed Continuation" -- today's close breaks back above the
  post-breakout high (the "flagpole high") by the configured buffer,
  on volume.
- "Continuation — Volume Unconfirmed" -- the identical price break,
  without the volume confirmation.
"""

import pandas as pd

from . import config, indicators, scanner, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All Stocks"),
}


def default_params() -> dict:
    """The page's slider defaults -- every value can be overridden by the
    caller (the Streamlit page passes live widget values once touched).
    """
    return {
        "resistance_lookback_days": config.BREAKOUT_FLAG_RESISTANCE_LOOKBACK_DAYS,
        "breakout_lookback_days": config.BREAKOUT_FLAG_BREAKOUT_LOOKBACK_DAYS,
        "breakout_volume_mult": config.BREAKOUT_FLAG_BREAKOUT_VOLUME_MULT,
        "breakout_volume_avg_period": config.BREAKOUT_FLAG_BREAKOUT_VOLUME_AVG_PERIOD,
        "flag_pullback_min_pct": config.BREAKOUT_FLAG_PULLBACK_MIN_PCT,
        "flag_pullback_max_pct": config.BREAKOUT_FLAG_PULLBACK_MAX_PCT,
        "retest_tolerance_pct": config.BREAKOUT_FLAG_RETEST_TOLERANCE_PCT,
        "continuation_min_pct": config.BREAKOUT_FLAG_CONTINUATION_MIN_PCT,
        "continuation_volume_mult": config.BREAKOUT_FLAG_CONTINUATION_VOLUME_MULT,
        "rsi_min": config.BREAKOUT_FLAG_RSI_MIN,
        "rsi_max": config.BREAKOUT_FLAG_RSI_MAX,
    }


def _find_recent_breakout(df: pd.DataFrame, today_idx: int, params: dict) -> tuple[int, float] | None:
    """The most recent session within `breakout_lookback_days` (strictly
    before today, so there's at least one day of room for a pullback)
    where close cleared the trailing `resistance_lookback_days`-session
    rolling-max High, on `breakout_volume_mult`x volume. Returns
    (breakout_idx, resistance_level), preferring the *most recent*
    qualifying day if several exist -- the freshest flagpole.
    """
    lb = params["breakout_lookback_days"]
    res_lb = params["resistance_lookback_days"]
    vol_period = params["breakout_volume_avg_period"]
    best = None
    window_start = max(res_lb, today_idx - lb + 1)
    for i in range(window_start, today_idx):
        resistance_level = float(df["High"].iloc[i - res_lb:i].max())
        if resistance_level <= 0 or float(df["Close"].iloc[i]) <= resistance_level:
            continue
        if i - vol_period < 0:
            continue
        avg_vol = float(df["Volume"].iloc[i - vol_period:i].mean())
        if avg_vol <= 0:
            continue
        vol_ratio = float(df["Volume"].iloc[i]) / avg_vol
        if vol_ratio < params["breakout_volume_mult"]:
            continue
        best = (i, resistance_level)  # keep overwriting -> last (most recent) wins
    return best


def evaluate_ticker(ticker: str, df: pd.DataFrame, params: dict, min_price: float) -> dict | None:
    """Evaluates one ticker for the Breakout Flag Continuation pattern.
    Returns None if there's no fresh volume-confirmed breakout, no shallow
    pullback since it, the pullback broke down through the original
    resistance, or RSI is outside the healthy range.
    """
    n = len(df)
    min_required = (
        params["resistance_lookback_days"] + params["breakout_lookback_days"]
        + params["breakout_volume_avg_period"] + 10
    )
    if n < min_required:
        return None

    today_idx = n - 1
    price_today = float(df["Close"].iloc[today_idx])
    if price_today <= min_price:
        return None

    rsi_series = indicators.compute_rsi(df["Close"])
    rsi_now = float(rsi_series.iloc[today_idx]) if pd.notna(rsi_series.iloc[today_idx]) else None
    if rsi_now is None or not (params["rsi_min"] <= rsi_now <= params["rsi_max"]):
        return None

    found = _find_recent_breakout(df, today_idx, params)
    if found is None:
        return None
    breakout_idx, resistance_level = found
    breakout_date = df.index[breakout_idx].strftime("%Y-%m-%d")
    breakout_age_days = today_idx - breakout_idx

    # The flagpole high (the peak reached at/after the breakout) and the
    # flag low (the lowest point since that peak) -- if the stock is
    # still making fresh highs every day since the breakout, there's no
    # pullback low yet to measure, so this isn't a "flag" yet.
    post_breakout = df.iloc[breakout_idx:today_idx + 1]
    flagpole_high = float(post_breakout["High"].max())
    flagpole_high_idx = breakout_idx + int(post_breakout["High"].to_numpy().argmax())
    if flagpole_high_idx >= today_idx:
        return None
    low_window = df["Low"].iloc[flagpole_high_idx + 1:today_idx + 1]
    flag_low = float(low_window.min())
    flag_low_idx = flagpole_high_idx + 1 + int(low_window.to_numpy().argmin())

    pullback_pct = (flagpole_high - flag_low) / flagpole_high * 100
    if not (params["flag_pullback_min_pct"] <= pullback_pct <= params["flag_pullback_max_pct"]):
        return None

    # Must still be holding the breakout: the flag low can retest down
    # toward the original resistance, but not meaningfully break it --
    # otherwise this was a failed breakout, not a flag.
    if flag_low < resistance_level * (1 - params["retest_tolerance_pct"] / 100):
        return None

    vol_period = params["breakout_volume_avg_period"]
    vol_ratio_today = None
    if today_idx - vol_period >= 0:
        avg_vol = float(df["Volume"].iloc[today_idx - vol_period:today_idx].mean())
        if avg_vol > 0:
            vol_ratio_today = float(df["Volume"].iloc[today_idx]) / avg_vol

    base_row = {
        "Ticker": ticker,
        "Signal Date": df.index[today_idx].strftime("%Y-%m-%d"),
        "Current Price": round(price_today, 2),
        "Resistance Level": round(resistance_level, 2),
        "Breakout Date": breakout_date,
        "Flagpole High": round(flagpole_high, 2),
        "Flag Low": round(flag_low, 2),
        "Pullback %": round(pullback_pct, 2),
        "RSI": round(rsi_now, 1),
        "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
        "_flag_low_idx": flag_low_idx,
        "_breakout_idx": breakout_idx,
        "_signal_idx": today_idx,
    }

    base_why = [
        f"✓ Fresh volume breakout on {breakout_date} ({breakout_age_days} sessions ago): close cleared the "
        f"trailing {params['resistance_lookback_days']}-session high (₹{resistance_level:.2f})",
        f"✓ Shallow flag since then: pulled back {pullback_pct:.1f}% to ₹{flag_low:.2f}, still holding "
        f"above the original resistance",
        f"✓ RSI {rsi_now:.1f} within the {params['rsi_min']:.0f}-{params['rsi_max']:.0f} healthy range",
    ]

    # --- Continuation: close breaks back above the flagpole high, on volume ---
    if today_idx >= 1:
        price_prev = float(df["Close"].iloc[today_idx - 1])
        min_clearance = flagpole_high * params["continuation_min_pct"] / 100
        if price_prev <= flagpole_high + min_clearance < price_today:
            confirmed = (
                vol_ratio_today is not None and vol_ratio_today >= params["continuation_volume_mult"]
            )
            status = "Confirmed Continuation" if confirmed else "Continuation — Volume Unconfirmed"
            why = base_why + [
                f"✓ Yesterday's close (₹{price_prev:.2f}) was at/below the flagpole high (₹{flagpole_high:.2f})",
                f"✓ Today's close (₹{price_today:.2f}) cleared it by more than {params['continuation_min_pct']:.1f}%",
            ]
            if vol_ratio_today is not None:
                tick = "✓" if confirmed else "✗"
                why.append(
                    f"{tick} Volume {vol_ratio_today:.2f}x the prior {vol_period}-session average "
                    f"({'meets' if confirmed else 'below'} the {params['continuation_volume_mult']:.1f}x requirement)"
                )
            else:
                why.append("✗ Not enough prior sessions to compute the volume-confirmation ratio")
            return {
                **base_row,
                "Setup Status": status,
                "% Below Flagpole High": round((flagpole_high - price_today) / flagpole_high * 100, 2),
                "Why Qualified": "\n".join(why),
            }

    # --- Flag Watchlist: still inside the flag, hasn't broken out again yet ---
    if price_today <= flagpole_high:
        why = base_why + [
            f"✓ Close (₹{price_today:.2f}) is still inside the flag, at/below the flagpole high "
            f"(₹{flagpole_high:.2f}) -- watching for a resumption",
        ]
        return {
            **base_row,
            "Setup Status": "Flag Watchlist",
            "% Below Flagpole High": round((flagpole_high - price_today) / flagpole_high * 100, 2),
            "Why Qualified": "\n".join(why),
        }

    return None


_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Breakout Date", "Flagpole High", "Flag Low", "Pullback %",
    "% Below Flagpole High", "RSI", "Volume Ratio", "Why Qualified",
]


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["% Below Flagpole High"] if r["% Below Flagpole High"] is not None else 999)
    df_out = pd.DataFrame(filtered, columns=[c for c in _DISPLAY_COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_DISPLAY_COLUMNS)
    return df_out


def scan_breakout_flag(
    universe_name: str = "nifty500", min_price: float | None = None,
    params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs the Breakout Flag Continuation strategy over a chosen universe."""
    if min_price is None:
        min_price = config.BREAKOUT_FLAG_MIN_PRICE_INR
    if params is None:
        params = default_params()
    if universe_name not in _UNIVERSE_LOADERS:
        raise ValueError(f"Unknown universe_name: {universe_name}")
    loader, universe_label = _UNIVERSE_LOADERS[universe_name]
    tickers, source = loader()

    if progress_callback:
        progress_callback(0.05, f"Downloading price history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers)

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
        "flag_watchlist": _build_table(rows, "Flag Watchlist"),
        "confirmed_continuation": _build_table(rows, "Confirmed Continuation"),
        "volume_unconfirmed": _build_table(rows, "Continuation — Volume Unconfirmed"),
        "breakout_idx": {r["Ticker"]: r["_breakout_idx"] for r in rows},
        "flag_low_idx": {r["Ticker"]: r["_flag_low_idx"] for r in rows},
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "universe_label": universe_label,
        "data_asof_date": data_asof_date,
    }


def build_flag_chart(
    df: pd.DataFrame, breakout_idx: int, resistance_val: float,
    flag_low_idx: int, flag_low_val: float, signal_idx: int, ticker: str,
):
    """Candlestick + the flat breakout-resistance line (dashed red, drawn
    from the resistance's own lookback start out to the signal candle) +
    a breakout-day marker + flag-low marker + a Volume panel with the
    breakout day and the signal day both highlighted, so the original
    volume spike and any continuation volume are both visible.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    plot_start = max(0, breakout_idx - 15)
    plot_end = min(len(df) - 1, signal_idx + 5)
    plot_df = df.iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.06,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Breakout Flag Continuation", "Volume"),
    )
    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[breakout_idx], df.index[signal_idx]], y=[resistance_val, resistance_val],
        mode="lines", name="Breakout Resistance", line=dict(color="#FF6B6B", width=2, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[breakout_idx]], y=[float(df["High"].iloc[breakout_idx])], mode="markers",
        name="Breakout Day", marker=dict(color="#FFB020", size=11, symbol="star"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[flag_low_idx]], y=[flag_low_val], mode="markers", name="Flag Low",
        marker=dict(color="#3ECF8E", size=10, symbol="triangle-up"),
    ), row=1, col=1)

    vol_colors = []
    for i in range(plot_start, plot_end + 1):
        if i == signal_idx:
            vol_colors.append("#FFB020")
        elif i == breakout_idx:
            vol_colors.append("#4FD1E8")
        else:
            vol_colors.append("#4F7CFF")
    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color=vol_colors), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=560, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
