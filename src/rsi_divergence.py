"""RSI Divergence at Support: a stock whose price has been flat-to-
declining near its own 52-week low, tested more than once, while RSI(14)
made a *higher* low across those tests -- a bullish divergence -- and is
now recovering back toward a nearby high (Pre-Breakout Watchlist) or has
already closed above it on volume (Confirmed Breakout / Breakout --
Volume Unconfirmed).

Built from a chart the user shared and described directly: price sliding
sideways/down into the same support level in June and again in
September, RSI clearly rising across those two touches even as price
didn't, then a green breakout candle with a volume spike. That's a
distinct setup from every other strategy in this app -- the channel-
based strategies (Daily Rising Channel, Triple EMA Golden Cross) need a
*rising* price structure, and Bullish Recovery Above EMAs only looks at
a single prior down/flat session, not a multi-month divergence.

Adjustable from the UI (like Rising Channel / Resistance Breakout) --
see default_params() / config.py's RSI_DIVERGENCE_* defaults -- since
"how wide a band counts as the same support level," "how many points of
RSI rise counts as a real divergence," and "how fresh the last touch
must be" are judgment calls, not a fixed numeric spec.

Reuses `rising_channel.find_swing_points` as-is for the leak-free swing-
low detection that finds each "support test" -- the same generic,
strategy-agnostic detector Resistance Breakout and Triple EMA Golden
Cross's channel gate already reuse rather than reimplementing.

The short-term "resistance" a stock needs to clear here is not a long-
past swing high (Resistance Breakout's definition) or a fitted sloped
channel (Rising Channel's) -- it's simply the highest High reached
*since* the most recent support test and *before* today, i.e. the peak
of the current recovery leg off support. That window naturally excludes
today's own bar, so today's breakout candle can never inflate the very
resistance level it's being measured against.
"""

import pandas as pd

from . import config, indicators, rising_channel, scanner, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All Stocks"),
}


def default_params() -> dict:
    """The page's slider defaults -- every value can be overridden by the
    caller (the Streamlit page passes live widget values once touched).
    """
    return {
        "pivot_n": config.RSI_DIVERGENCE_PIVOT_N,
        "support_lookback_days": config.RSI_DIVERGENCE_SUPPORT_LOOKBACK_DAYS,
        "support_zone_pct": config.RSI_DIVERGENCE_SUPPORT_ZONE_PCT,
        "min_touch_separation_days": config.RSI_DIVERGENCE_MIN_TOUCH_SEPARATION_DAYS,
        "recent_touch_max_age_days": config.RSI_DIVERGENCE_RECENT_TOUCH_MAX_AGE_DAYS,
        "price_tolerance_pct": config.RSI_DIVERGENCE_PRICE_TOLERANCE_PCT,
        "min_rsi_rise_pts": config.RSI_DIVERGENCE_MIN_RSI_RISE_PTS,
        "prebreakout_distance_min_pct": config.RSI_DIVERGENCE_PREBREAKOUT_DISTANCE_MIN_PCT,
        "prebreakout_distance_max_pct": config.RSI_DIVERGENCE_PREBREAKOUT_DISTANCE_MAX_PCT,
        "breakout_min_pct": config.RSI_DIVERGENCE_BREAKOUT_MIN_PCT,
        "breakout_volume_mult": config.RSI_DIVERGENCE_VOLUME_MULT,
        "breakout_volume_avg_period": config.RSI_DIVERGENCE_VOLUME_AVG_PERIOD,
    }


def _find_support_divergence(df: pd.DataFrame, today_idx: int, params: dict, rsi_series: pd.Series) -> dict | None:
    """The "two support tests with rising RSI" stage. Returns a dict of
    the 52-week low, both touches' index/price/RSI, or None if there's
    no genuine divergence: fewer than two swing lows inside the support
    zone, the touches aren't far enough apart, the most recent one isn't
    still fresh, price made a meaningfully *higher* low (that's just
    normal bullish structure, not divergence), or RSI didn't rise enough
    between the two.
    """
    lookback = params["support_lookback_days"]
    window_start = max(0, today_idx - lookback + 1)
    window = df.iloc[window_start:today_idx + 1]
    if len(window) < lookback:
        return None
    week_low = float(window["Low"].min())
    if week_low <= 0:
        return None

    _, swing_lows_local = rising_channel.find_swing_points(window, params["pivot_n"])
    if len(swing_lows_local) < 2:
        return None

    zone_ceiling = week_low * (1 + params["support_zone_pct"] / 100)
    touches = [window_start + i for i in swing_lows_local if float(window["Low"].iloc[i]) <= zone_ceiling]
    if len(touches) < 2:
        return None

    earliest_idx, recent_idx = touches[0], touches[-1]
    if recent_idx - earliest_idx < params["min_touch_separation_days"]:
        return None
    if today_idx - recent_idx > params["recent_touch_max_age_days"] or today_idx <= recent_idx:
        return None

    earliest_low = float(df["Low"].iloc[earliest_idx])
    recent_low = float(df["Low"].iloc[recent_idx])
    if recent_low > earliest_low * (1 + params["price_tolerance_pct"] / 100):
        return None  # recent touch is a meaningfully higher low -- normal structure, not divergence

    rsi_earliest = rsi_series.iloc[earliest_idx]
    rsi_recent = rsi_series.iloc[recent_idx]
    if pd.isna(rsi_earliest) or pd.isna(rsi_recent):
        return None
    rsi_earliest, rsi_recent = float(rsi_earliest), float(rsi_recent)
    if rsi_recent - rsi_earliest < params["min_rsi_rise_pts"]:
        return None

    return {
        "week_low": week_low, "earliest_idx": earliest_idx, "recent_idx": recent_idx,
        "earliest_low": earliest_low, "recent_low": recent_low,
        "rsi_earliest": rsi_earliest, "rsi_recent": rsi_recent,
    }


def evaluate_ticker(ticker: str, df: pd.DataFrame, params: dict, min_price: float) -> dict | None:
    """Evaluates one ticker for both a confirmed-breakout signal and a
    pre-breakout (approaching the recovery high) setup. Returns None if
    there's no genuine support-zone RSI divergence, or price is neither
    approaching nor breaking the recovery high -- a ticker with no such
    setup simply doesn't appear in any result table, not an error.
    """
    n = len(df)
    min_required = params["support_lookback_days"] + params["breakout_volume_avg_period"] + 10
    if n < min_required:
        return None

    today_idx = n - 1
    price_today = float(df["Close"].iloc[today_idx])
    if price_today <= min_price:
        return None

    rsi_series = indicators.compute_rsi(df["Close"])
    rsi_now = rsi_series.iloc[today_idx]
    if pd.isna(rsi_now):
        return None
    rsi_now = float(rsi_now)

    divergence = _find_support_divergence(df, today_idx, params, rsi_series)
    if divergence is None:
        return None
    recent_idx = divergence["recent_idx"]

    # The recovery leg's own peak -- the highest High since the most
    # recent support test and before today -- is this strategy's
    # "resistance." Excluding today's own bar means today's breakout
    # candle can never inflate the very level it's being measured against.
    window = df.iloc[recent_idx:today_idx]
    if window.empty:
        return None
    resistance = float(window["High"].max())
    if resistance <= 0:
        return None
    resistance_idx = recent_idx + int(window["High"].to_numpy().argmax())
    buy_level = resistance * (1 + params["breakout_min_pct"] / 100)

    vol_period = params["breakout_volume_avg_period"]
    vol_ratio_today = None
    if today_idx - vol_period >= 0:
        avg_vol = float(df["Volume"].iloc[today_idx - vol_period:today_idx].mean())
        if avg_vol > 0:
            vol_ratio_today = float(df["Volume"].iloc[today_idx]) / avg_vol

    earliest_date = df.index[divergence["earliest_idx"]].strftime("%Y-%m-%d")
    recent_date = df.index[recent_idx].strftime("%Y-%m-%d")
    rsi_rise = divergence["rsi_recent"] - divergence["rsi_earliest"]

    # Informational only, never a gate (same convention as Resistance
    # Breakout's RSI momentum note): has RSI kept rising/holding since
    # the most recent support test, or eased back a bit since then.
    if rsi_now >= divergence["rsi_recent"]:
        rsi_note = (
            f"ℹ️ RSI has continued higher since the {recent_date} support test: "
            f"{rsi_now:.1f} now vs {divergence['rsi_recent']:.1f} then"
        )
    else:
        rsi_note = (
            f"⚠️ RSI has eased a little since the {recent_date} support test: "
            f"{rsi_now:.1f} now vs {divergence['rsi_recent']:.1f} then -- the broader divergence still "
            f"holds, but short-term momentum has cooled slightly"
        )

    base_row = {
        "Ticker": ticker,
        "Signal Date": df.index[today_idx].strftime("%Y-%m-%d"),
        "Current Price": round(price_today, 2),
        "Support Level (52W Low)": round(divergence["week_low"], 2),
        "Resistance Level": round(resistance, 2),
        "Buy Level": round(buy_level, 2),
        "RSI": round(rsi_now, 1),
        "Earliest Touch Date": earliest_date,
        "Earliest Touch RSI": round(divergence["rsi_earliest"], 1),
        "Recent Touch Date": recent_date,
        "Recent Touch RSI": round(divergence["rsi_recent"], 1),
        "RSI Rise (pts)": round(rsi_rise, 1),
        "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
        "_earliest_idx": divergence["earliest_idx"],
        "_recent_idx": recent_idx,
        "_resistance_idx": resistance_idx,
        "_signal_idx": today_idx,
    }

    divergence_why = [
        f"✓ 52-week support zone: low of ₹{divergence['week_low']:.2f} over the trailing "
        f"{params['support_lookback_days']} sessions",
        f"✓ First support test on {earliest_date} at ₹{divergence['earliest_low']:.2f}, RSI {divergence['rsi_earliest']:.1f}",
        f"✓ Second support test on {recent_date} at ₹{divergence['recent_low']:.2f}, RSI {divergence['rsi_recent']:.1f} "
        f"-- price held/lower, but RSI rose {rsi_rise:.1f} points (bullish divergence)",
    ]

    # --- Breakout: close clears the recovery high, on volume -----------
    if today_idx >= 1:
        price_prev = float(df["Close"].iloc[today_idx - 1])
        if price_prev <= buy_level < price_today:
            confirmed = vol_ratio_today is not None and vol_ratio_today >= params["breakout_volume_mult"]
            status = "Confirmed Breakout" if confirmed else "Breakout — Volume Unconfirmed"
            why = divergence_why + [
                f"✓ Recovery high of ₹{resistance:.2f} since the {recent_date} support test",
                f"✓ Yesterday's close (₹{price_prev:.2f}) was at/below the ₹{buy_level:.2f} Buy Level",
                f"✓ Today's close (₹{price_today:.2f}) cleared the ₹{buy_level:.2f} Buy Level "
                f"(resistance + {params['breakout_min_pct']:.1f}%)",
            ]
            if vol_ratio_today is not None:
                tick = "✓" if confirmed else "✗"
                why.append(
                    f"{tick} Volume {vol_ratio_today:.2f}x the prior {vol_period}-session average "
                    f"({'meets' if confirmed else 'below'} the {params['breakout_volume_mult']:.1f}x requirement)"
                )
            else:
                why.append("✗ Not enough prior sessions to compute the volume-confirmation ratio")
            why.append(rsi_note)
            return {
                **base_row,
                "Setup Status": status,
                "Distance %": round((resistance - price_today) / resistance * 100, 2),
                "Why Qualified": "\n".join(why),
            }

    # --- Pre-Breakout Watchlist: approaching, not yet broken out -------
    if price_today <= resistance:
        distance_pct = (resistance - price_today) / resistance * 100
        if params["prebreakout_distance_min_pct"] <= distance_pct <= params["prebreakout_distance_max_pct"]:
            why = divergence_why + [
                f"✓ Recovery high of ₹{resistance:.2f} since the {recent_date} support test",
                f"✓ Close (₹{price_today:.2f}) is {distance_pct:.2f}% below that recovery high -- "
                f"approaching, not yet broken out",
                f"🎯 Buy Level: ₹{buy_level:.2f} -- a close above this price would confirm the breakout",
                rsi_note,
            ]
            return {
                **base_row,
                "Setup Status": "Pre-Breakout Watchlist",
                "Distance %": round(distance_pct, 2),
                "Why Qualified": "\n".join(why),
            }

    return None


_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Support Level (52W Low)", "Resistance Level", "Buy Level", "RSI",
    "Earliest Touch Date", "Earliest Touch RSI", "Recent Touch Date", "Recent Touch RSI", "RSI Rise (pts)",
    "Distance %", "Volume Ratio", "Why Qualified",
]


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["Distance %"] if r["Distance %"] is not None else 999)
    df_out = pd.DataFrame(filtered, columns=[c for c in _DISPLAY_COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_DISPLAY_COLUMNS)
    return df_out


def scan_rsi_divergence(
    universe_name: str = "nifty500", min_price: float | None = None,
    params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs the RSI Divergence at Support strategy over a chosen universe."""
    if min_price is None:
        min_price = config.RSI_DIVERGENCE_MIN_PRICE_INR
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
        "pre_breakout": _build_table(rows, "Pre-Breakout Watchlist"),
        "confirmed_breakout": _build_table(rows, "Confirmed Breakout"),
        "volume_unconfirmed": _build_table(rows, "Breakout — Volume Unconfirmed"),
        "earliest_idx": {r["Ticker"]: r["_earliest_idx"] for r in rows},
        "recent_idx": {r["Ticker"]: r["_recent_idx"] for r in rows},
        "resistance_idx": {r["Ticker"]: r["_resistance_idx"] for r in rows},
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "universe_label": universe_label,
        "data_asof_date": data_asof_date,
    }


def build_divergence_chart(
    df: pd.DataFrame, earliest_idx: int, recent_idx: int, support_level: float,
    resistance_idx: int, resistance_val: float, signal_idx: int, ticker: str,
    buy_level: float | None = None,
):
    """Candlestick + a flat support line (drawn from the first support
    test out to the signal candle) + the recovery-high resistance line
    (drawn from the most recent support test out to the signal candle) +
    a green Buy Level line + ▽ markers at both support tests, over a
    Volume panel (signal day highlighted) and an RSI(14) panel with its
    own markers at both support tests joined by a dotted line -- so the
    "price flat/down, RSI rising" divergence this strategy is built
    around is visible at a glance, not just implied by the numbers.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    plot_start = max(0, earliest_idx - 10)
    plot_end = min(len(df) - 1, signal_idx + 5)
    plot_df = df.iloc[plot_start:plot_end + 1]
    rsi_full = indicators.compute_rsi(df["Close"])
    rsi_plot = rsi_full.iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.2, 0.3], vertical_spacing=0.05,
        subplot_titles=(f"{ticker.replace('.NS', '')} — RSI Divergence at Support", "Volume", "RSI(14)"),
    )
    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[earliest_idx], df.index[signal_idx]], y=[support_level, support_level],
        mode="lines", name="52W Support", line=dict(color="#4FD1E8", width=2, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[recent_idx], df.index[signal_idx]], y=[resistance_val, resistance_val],
        mode="lines", name="Recovery High (Resistance)", line=dict(color="#FF6B6B", width=2, dash="dash"),
    ), row=1, col=1)
    if buy_level is not None:
        fig.add_trace(go.Scatter(
            x=[df.index[recent_idx], df.index[signal_idx]], y=[buy_level, buy_level],
            mode="lines", name="Buy Level", line=dict(color="#3ECF8E", width=2, dash="dash"),
        ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[earliest_idx], df.index[recent_idx]],
        y=[float(df["Low"].iloc[earliest_idx]), float(df["Low"].iloc[recent_idx])],
        mode="markers", name="Support Tests", marker=dict(color="#B26BFF", size=10, symbol="triangle-down"),
    ), row=1, col=1)
    fig.add_annotation(
        x=df.index[signal_idx], y=float(df["High"].iloc[signal_idx]), text="Signal", showarrow=True,
        arrowhead=2, yshift=18, font=dict(color="#EAF2FA"), arrowcolor="#FFB020", row=1, col=1,
    )

    vol_colors = ["#FFB020" if i == signal_idx else "#4F7CFF" for i in range(plot_start, plot_end + 1)]
    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color=vol_colors), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=rsi_plot.index, y=rsi_plot, mode="lines", name="RSI(14)", line=dict(color="#8E24AA", width=1.6),
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[earliest_idx], df.index[recent_idx]],
        y=[float(rsi_full.iloc[earliest_idx]), float(rsi_full.iloc[recent_idx])],
        mode="lines+markers", name="RSI Divergence", line=dict(color="#3ECF8E", width=2, dash="dot"),
        marker=dict(color="#3ECF8E", size=9, symbol="diamond"),
    ), row=3, col=1)
    fig.update_yaxes(range=[0, 100], row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=680, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
