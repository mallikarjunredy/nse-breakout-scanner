"""Resistance Breakout ("Previous Swing High Breakout with Volume
Confirmation"): a stock previously reached a price it could not move
beyond -- a confirmed swing high, which becomes the resistance to watch.
It then fell into a genuine pullback/base, recovered back toward that
old high, and now approaches it (Pre-Breakout Watchlist) or has closed
above it on visibly higher volume (Confirmed Breakout / Breakout --
Volume Unconfirmed).

Deliberately NOT called a "cup-and-handle": that pattern requires a
distinct handle (a second, shallower pullback right before the
breakout), which this rule set never checks for. This is a simpler
previous-high breakout whose recovery leg often *looks* cup-shaped --
nothing more specific is claimed than that.

Adjustable from the UI (like Daily Rising Channel / Daily Trend +
Consolidation) -- see default_params() / config.py's
RESISTANCE_BREAKOUT_* defaults -- since "how far back to look for the
previous high," "how deep a pullback counts as a real base," and "how
much volume is 'visibly higher'" are inherently tunable judgment calls,
not the kind of precise, non-negotiable numeric rule Upside Buy
Movement's or Bullish Recovery Above EMAs' definitions are.

Resistance here is a single, discrete swing-high price (found via
`rising_channel.find_swing_points`, reused as-is -- it's already a
generic, leak-free swing-point detector with no Rising-Channel-specific
coupling), not a fitted sloped line the way Rising Channel's channel
is. Because that swing high sits `min_high_age_days` sessions back by
construction, it cannot itself be affected by yesterday's or today's
bar, so (unlike Rising Channel's frozen-through-yesterday channel fit)
one as-of-today swing search is enough to safely evaluate both
yesterday's and today's close against it -- there's no separate
lookahead risk to freeze against.

Momentum gate (added at the user's request after finding lookalikes like
KIMS/Max Healthcare in the watchlist -- stocks just sitting flat near the
previous high with no real forward movement behind them): close must be
up at least `min_momentum_pct` over the trailing `momentum_lookback_days`
sessions for a ticker to qualify at all, in *either* tier. This is a
simple rate-of-change check, not a trend-quality score -- it only asks
"has this actually moved recently," which is enough to separate a fresh
recovery from a stock stuck oscillating sideways.

Buy Level (added at the user's request): `resistance * (1 +
breakout_min_pct / 100)` -- the exact price a close needs to clear to
confirm the breakout, i.e. the same threshold `evaluate_ticker` already
uses internally, now surfaced as its own column and chart line (green,
dashed) rather than something the user has to compute themselves from
the resistance level and the buffer %. Shown for every match in both
tiers -- for the watchlist it's "buy above this price to confirm";
for a breakout it's the exact level that was cleared.
"""

import pandas as pd

from . import config, rising_channel, scanner, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All Stocks"),
}


def default_params() -> dict:
    """The page's slider defaults -- every value can be overridden by the
    caller (the Streamlit page passes live widget values once touched).
    """
    return {
        "pivot_n": config.RESISTANCE_BREAKOUT_PIVOT_N,
        "resistance_lookback_days": config.RESISTANCE_BREAKOUT_LOOKBACK_DAYS,
        "min_high_age_days": config.RESISTANCE_BREAKOUT_MIN_HIGH_AGE_DAYS,
        "min_pullback_pct": config.RESISTANCE_BREAKOUT_MIN_PULLBACK_PCT,
        "prebreakout_distance_min_pct": config.RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MIN_PCT,
        "prebreakout_distance_max_pct": config.RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MAX_PCT,
        "breakout_min_pct": config.RESISTANCE_BREAKOUT_BREAKOUT_MIN_PCT,
        "breakout_volume_mult": config.RESISTANCE_BREAKOUT_VOLUME_MULT,
        "breakout_volume_avg_period": config.RESISTANCE_BREAKOUT_VOLUME_AVG_PERIOD,
        "momentum_lookback_days": config.RESISTANCE_BREAKOUT_MOMENTUM_LOOKBACK_DAYS,
        "min_momentum_pct": config.RESISTANCE_BREAKOUT_MIN_MOMENTUM_PCT,
    }


def _find_previous_high(df: pd.DataFrame, as_of_idx: int, params: dict) -> tuple[float, int] | None:
    """The "previous high — July" stage: the highest confirmed swing high
    within `resistance_lookback_days` of `as_of_idx`, old enough
    (`min_high_age_days`) that it's a genuine *previous* high rather than
    a swing still forming right into the evaluation date. Returns
    (resistance_price, resistance_idx) in `df`'s own indexing, or None if
    no eligible swing high exists in the window.
    """
    window_start = max(0, as_of_idx - params["resistance_lookback_days"] + 1)
    window_df = df.iloc[window_start:as_of_idx + 1]
    swing_highs, _ = rising_channel.find_swing_points(window_df, params["pivot_n"])
    if not swing_highs:
        return None
    cutoff = (as_of_idx - window_start) - params["min_high_age_days"]
    eligible = [i for i in swing_highs if i <= cutoff]
    if not eligible:
        return None
    best_local_idx = max(eligible, key=lambda i: window_df["High"].iloc[i])
    resistance = float(window_df["High"].iloc[best_local_idx])
    return resistance, window_start + best_local_idx


def _pullback_info(df: pd.DataFrame, resistance_idx: int, as_of_idx: int) -> tuple[float, int] | None:
    """The "pullback — August" stage: the lowest Low between the previous
    high and today. Returns (pullback_low, pullback_idx), or None if
    there's no room between the two (shouldn't happen once
    min_high_age_days has been enforced upstream).
    """
    if as_of_idx <= resistance_idx:
        return None
    window = df["Low"].iloc[resistance_idx + 1:as_of_idx + 1]
    local_idx = int(window.to_numpy().argmin())
    return float(window.iloc[local_idx]), resistance_idx + 1 + local_idx


def evaluate_ticker(ticker: str, df: pd.DataFrame, params: dict, min_price: float) -> dict | None:
    """Evaluates one ticker for both a confirmed-breakout signal and a
    pre-breakout (approaching resistance) setup. Returns None if there's
    no eligible previous high, no real pullback below it, or price is
    neither approaching nor breaking that resistance -- a ticker with no
    such setup simply doesn't appear in any result table, not an error.
    """
    n = len(df)
    min_required = params["resistance_lookback_days"] + params["breakout_volume_avg_period"] + 10
    if n < min_required:
        return None

    today_idx = n - 1
    price_today = float(df["Close"].iloc[today_idx])
    if price_today <= min_price:
        return None

    found = _find_previous_high(df, today_idx, params)
    if found is None:
        return None
    resistance, resistance_idx = found
    if resistance <= 0:
        return None

    pullback = _pullback_info(df, resistance_idx, today_idx)
    if pullback is None:
        return None
    pullback_low, pullback_idx = pullback
    pullback_pct = (resistance - pullback_low) / resistance * 100
    if pullback_pct < params["min_pullback_pct"]:
        return None

    # Momentum gate: excludes stocks just sitting flat/consolidating near
    # the previous high with no real forward movement behind them -- close
    # must be up at least min_momentum_pct over the trailing
    # momentum_lookback_days for a ticker to qualify as "trending" at all.
    mom_lb = params["momentum_lookback_days"]
    if today_idx - mom_lb < 0:
        return None
    price_mom_ago = float(df["Close"].iloc[today_idx - mom_lb])
    if price_mom_ago <= 0:
        return None
    momentum_pct = (price_today - price_mom_ago) / price_mom_ago * 100
    if momentum_pct < params["min_momentum_pct"]:
        return None

    resistance_date = df.index[resistance_idx].strftime("%Y-%m-%d")
    resistance_age_days = today_idx - resistance_idx

    # Buy Level: the exact price a close needs to clear resistance by the
    # strategy's own breakout_min_pct buffer -- shown as a distinct line
    # from the raw resistance level so "buy above this price" is a single
    # concrete number, not something the user has to compute themselves
    # from the resistance level and the buffer %.
    buy_level = resistance * (1 + params["breakout_min_pct"] / 100)

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
        "Resistance Level": round(resistance, 2),
        "Resistance Date": resistance_date,
        "Buy Level": round(buy_level, 2),
        "Pullback Low": round(pullback_low, 2),
        "Pullback %": round(pullback_pct, 2),
        "Momentum %": round(momentum_pct, 2),
        "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
        "_resistance_idx": resistance_idx,
        "_pullback_idx": pullback_idx,
        "_signal_idx": today_idx,
    }

    # --- Breakout — far right: close crosses the old high, on volume -----
    if today_idx >= 1:
        price_prev = float(df["Close"].iloc[today_idx - 1])
        if price_prev <= buy_level < price_today:
            confirmed = vol_ratio_today is not None and vol_ratio_today >= params["breakout_volume_mult"]
            status = "Confirmed Breakout" if confirmed else "Breakout — Volume Unconfirmed"
            why = [
                f"✓ Previous high found on {resistance_date} at ₹{resistance:.2f} ({resistance_age_days} sessions ago)",
                f"✓ Pulled back {pullback_pct:.1f}% to a low of ₹{pullback_low:.2f} before recovering",
                f"✓ Trending, not flat: close is up {momentum_pct:.1f}% over the last {mom_lb} sessions",
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
            return {
                **base_row,
                "Setup Status": status,
                "% Below Resistance": round((resistance - price_today) / resistance * 100, 2),
                "Why Qualified": "\n".join(why),
            }

    # --- Recovery — September: approaching, not yet broken out -----------
    if price_today <= resistance:
        distance_pct = (resistance - price_today) / resistance * 100
        if params["prebreakout_distance_min_pct"] <= distance_pct <= params["prebreakout_distance_max_pct"]:
            why = [
                f"✓ Previous high found on {resistance_date} at ₹{resistance:.2f} ({resistance_age_days} sessions ago)",
                f"✓ Pulled back {pullback_pct:.1f}% to a low of ₹{pullback_low:.2f} before recovering",
                f"✓ Trending, not flat: close is up {momentum_pct:.1f}% over the last {mom_lb} sessions",
                f"✓ Close (₹{price_today:.2f}) is {distance_pct:.2f}% below that resistance -- "
                f"approaching, not yet broken out",
                f"🎯 Buy Level: ₹{buy_level:.2f} -- a close above this price would confirm the breakout",
            ]
            return {
                **base_row,
                "Setup Status": "Pre-Breakout Watchlist",
                "% Below Resistance": round(distance_pct, 2),
                "Why Qualified": "\n".join(why),
            }

    return None


_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Buy Level", "Resistance Date", "Pullback Low", "Pullback %", "Momentum %",
    "% Below Resistance", "Volume Ratio", "Why Qualified",
]


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["% Below Resistance"] if r["% Below Resistance"] is not None else 999)
    df_out = pd.DataFrame(filtered, columns=[c for c in _DISPLAY_COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_DISPLAY_COLUMNS)
    return df_out


def scan_resistance_breakout(
    universe_name: str = "nifty500", min_price: float | None = None,
    params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs the Resistance Breakout strategy over a chosen universe."""
    if min_price is None:
        min_price = config.RESISTANCE_BREAKOUT_MIN_PRICE_INR
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
        "resistance_idx": {r["Ticker"]: r["_resistance_idx"] for r in rows},
        "pullback_idx": {r["Ticker"]: r["_pullback_idx"] for r in rows},
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "universe_label": universe_label,
        "data_asof_date": data_asof_date,
    }


def build_breakout_chart(
    df: pd.DataFrame, resistance_idx: int, resistance_val: float,
    pullback_idx: int, pullback_val: float, signal_idx: int, ticker: str,
    buy_level: float | None = None,
):
    """Candlestick + the flat previous-high resistance line (drawn from
    the previous-high candle out to the signal candle) + a green Buy
    Level line (resistance + the strategy's own breakout buffer -- the
    exact price a close needs to clear to confirm the breakout) +
    previous-high / pullback-low markers + a Volume panel with the
    signal day's bar highlighted, so "visibly higher volume" is
    literally visible.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    plot_start = max(0, resistance_idx - 10)
    plot_end = min(len(df) - 1, signal_idx + 5)
    plot_df = df.iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.06,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Resistance Breakout", "Volume"),
    )
    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=[df.index[resistance_idx], df.index[signal_idx]], y=[resistance_val, resistance_val],
        mode="lines", name="Previous High (Resistance)", line=dict(color="#FF6B6B", width=2, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[resistance_idx]], y=[resistance_val], mode="markers", name="Previous High",
        marker=dict(color="#FF6B6B", size=10, symbol="triangle-down"),
    ), row=1, col=1)
    if buy_level is not None:
        fig.add_trace(go.Scatter(
            x=[df.index[resistance_idx], df.index[signal_idx]], y=[buy_level, buy_level],
            mode="lines", name="Buy Level", line=dict(color="#3ECF8E", width=2, dash="dash"),
        ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[df.index[pullback_idx]], y=[pullback_val], mode="markers", name="Pullback Low",
        marker=dict(color="#3ECF8E", size=10, symbol="triangle-up"),
    ), row=1, col=1)

    fig.add_annotation(
        x=df.index[signal_idx], y=float(df["High"].iloc[signal_idx]), text="Signal", showarrow=True,
        arrowhead=2, yshift=18, font=dict(color="#EAF2FA"), arrowcolor="#FFB020", row=1, col=1,
    )

    vol_colors = ["#FFB020" if i == signal_idx else "#4F7CFF" for i in range(plot_start, plot_end + 1)]
    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color=vol_colors), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=560, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
