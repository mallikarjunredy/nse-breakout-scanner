"""Triple EMA Golden Cross: three EMAs (fast/mid/slow) that start tangled
together through a consolidation, "golden cross" (the fast EMA crossing
above the slow EMA), then fan out into full bullish stacking -- Close >
EMA-fast > EMA-mid > EMA-slow, all three rising -- with RSI climbing and
a volume spike confirming the move. This is the pattern behind a common
three-line "MA ribbon" chart: green (fast) / red (mid) / blue (slow),
with a marker at the crossover point.

Three mutually-exclusive stages, matching what that chart actually shows
left to right:

- "Golden Cross Formed" -- the fast EMA has crossed above the slow EMA
  within the lookback window and hasn't reversed since, but the EMAs
  haven't fully stacked into bullish order yet (still tangled/converging).
- "Bullish Alignment" -- fully stacked (Close > fast > mid > slow, all
  three rising) and RSI is in a healthy range, but volume hasn't
  expanded yet -- still building, the way the chart's Aug/Sep climb
  shows flat volume.
- "Confirmed Breakout" -- bullish alignment *plus* today's volume is at
  least `breakout_volume_mult` times its recent average -- the volume
  spike at the chart's right edge.

Adjustable from the UI (like Rising Channel / Trend + Consolidation /
Resistance Breakout) -- see default_params() / config.py's
TRIPLE_EMA_* defaults -- since this pattern came from a visual
description, not a numbered spec, so its exact periods and thresholds
are reasonable defaults the user should be able to tune, not fixed rules.

Momentum gate (added at the user's request after finding a lookalike --
Aditya Birla Capital, a weak just-formed cross where the three EMAs were
all bunched within about a rupee of each other on a stock that had
actually rolled over and gone flat/negative over the prior 10-20
sessions): close must be up at least `min_momentum_pct` over the
trailing `momentum_lookback_days` sessions for a ticker to qualify at
all, in any of the three statuses. Same rate-of-change design as
Resistance Breakout's own momentum gate -- it only asks "has this
actually moved recently."

EMA-spread gate (added after a second lookalike -- Firstsource
Solutions -- slipped past the momentum gate: its 10-session momentum
happened to read +5% purely because of where that window started, while
its 5- and 20-session momentum were both negative and its EMAs were
bunched within 0.3% of each other). A single-window rate-of-change can
be fooled by a noisy reference point; EMA-fast must sit at least
`min_spread_pct` above EMA-slow -- a direct, point-in-time measure of
"has this actually fanned out" rather than an inference from price
history. Verified against real data: 0.5% cleanly separates known
lookalikes (Firstsource 0.30%, Aditya Birla Capital 0.07%, KIMS 0.42%)
from genuine matches (Gabriel India 0.57%, IKS 1.78%).

Long-term trend gate (added after a third lookalike -- Tata Chemicals --
slipped past both prior gates: +11% over 10 sessions and a healthy 1.5%
EMA spread, but that was only a bounce off a new low inside a year-long
downtrend, -32% off its own 252-day high and trading below its own
SMA200). Neither the momentum gate nor the EMA-spread gate can see a
stock's *longer-term* trend context, since both only look at the last
few weeks. Close must be above SMA(`trend_sma_period`, default 200) --
the same "is this a primary uptrend" check Trend + Consolidation already
uses for its own Nifty 50 benchmark gate. Verified against real data:
Patanjali Foods was also trading below its own SMA200 despite similarly
strong short-term numbers (+20% over 10 sessions) and was caught by the
same gate.
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
        "ema_fast": config.TRIPLE_EMA_FAST,
        "ema_mid": config.TRIPLE_EMA_MID,
        "ema_slow": config.TRIPLE_EMA_SLOW,
        "golden_cross_lookback_days": config.TRIPLE_EMA_GOLDEN_CROSS_LOOKBACK_DAYS,
        "ema_rising_lookback_days": config.TRIPLE_EMA_RISING_LOOKBACK_DAYS,
        "rsi_min": config.TRIPLE_EMA_RSI_MIN,
        "rsi_max": config.TRIPLE_EMA_RSI_MAX,
        "breakout_volume_mult": config.TRIPLE_EMA_BREAKOUT_VOLUME_MULT,
        "breakout_volume_avg_period": config.TRIPLE_EMA_BREAKOUT_VOLUME_AVG_PERIOD,
        "momentum_lookback_days": config.TRIPLE_EMA_MOMENTUM_LOOKBACK_DAYS,
        "min_momentum_pct": config.TRIPLE_EMA_MIN_MOMENTUM_PCT,
        "min_spread_pct": config.TRIPLE_EMA_MIN_SPREAD_PCT,
        "trend_sma_period": config.TRIPLE_EMA_TREND_SMA_PERIOD,
    }


def _find_golden_cross(ema_fast: pd.Series, ema_slow: pd.Series, today_idx: int, lookback: int) -> int | None:
    """Most recent index within `lookback` sessions of today where
    ema_fast crossed from at/below ema_slow to strictly above it. Returns
    None if no such crossover happened in the window.
    """
    window_start = max(1, today_idx - lookback + 1)
    cross_idx = None
    for i in range(window_start, today_idx + 1):
        if ema_fast.iloc[i - 1] <= ema_slow.iloc[i - 1] and ema_fast.iloc[i] > ema_slow.iloc[i]:
            cross_idx = i
    return cross_idx


def evaluate_ticker(ticker: str, df: pd.DataFrame, params: dict, min_price: float) -> dict | None:
    """Evaluates one ticker for the Triple EMA Golden Cross pattern.
    Returns None if there's no live (unreversed) golden cross in the
    lookback window, or RSI is outside the healthy range -- a ticker
    with neither simply doesn't appear in any result table.
    """
    n = len(df)
    min_required = (
        max(params["ema_slow"], params["trend_sma_period"]) + params["golden_cross_lookback_days"]
        + params["breakout_volume_avg_period"] + 10
    )
    if n < min_required:
        return None

    today_idx = n - 1
    price_today = float(df["Close"].iloc[today_idx])
    if price_today <= min_price:
        return None

    close = df["Close"]
    ema_fast = indicators.compute_ema(close, params["ema_fast"])
    ema_mid = indicators.compute_ema(close, params["ema_mid"])
    ema_slow = indicators.compute_ema(close, params["ema_slow"])
    rsi = indicators.compute_rsi(close)
    trend_sma = indicators.compute_sma(close, params["trend_sma_period"])

    ema_fast_now = float(ema_fast.iloc[today_idx])
    ema_mid_now = float(ema_mid.iloc[today_idx])
    ema_slow_now = float(ema_slow.iloc[today_idx])
    rsi_now = float(rsi.iloc[today_idx]) if pd.notna(rsi.iloc[today_idx]) else None
    if rsi_now is None:
        return None
    if not (params["rsi_min"] <= rsi_now <= params["rsi_max"]):
        return None

    # Long-term trend gate: neither the momentum gate nor the EMA-spread
    # gate can see a stock's longer-term trend context -- a stock can
    # show strong recent momentum and a healthy EMA spread purely from
    # bouncing off a new low inside a year-long downtrend (e.g. Tata
    # Chemicals: +11% over 10 sessions, but trading well below its own
    # SMA200 after a slide from its 252-day high). Close must be above
    # SMA(trend_sma_period) -- the same "is this a primary uptrend" check
    # Trend + Consolidation already uses for its Nifty 50 benchmark gate.
    trend_sma_now = float(trend_sma.iloc[today_idx]) if pd.notna(trend_sma.iloc[today_idx]) else None
    if trend_sma_now is None or price_today <= trend_sma_now:
        return None

    # A live golden cross: fast is currently above slow, and that
    # relationship began with an actual crossover inside the lookback
    # window (not just "always been above" -- there must be a real cross
    # event to point to and show on the chart).
    if ema_fast_now <= ema_slow_now:
        return None

    # EMA-spread gate: excludes a technically-live but trivial cross
    # where the EMAs are all still bunched within a fraction of a
    # percent of each other (e.g. Firstsource Solutions at 0.3% spread)
    # -- a more robust, direct measure of "has this actually fanned out"
    # than a single-window price-momentum snapshot, which a noisy
    # reference point can fool (Firstsource showed +5% over 10 sessions
    # purely from where that window happened to start, while its 5- and
    # 20-session momentum were both negative).
    spread_pct = (ema_fast_now - ema_slow_now) / ema_slow_now * 100
    if spread_pct < params["min_spread_pct"]:
        return None

    cross_idx = _find_golden_cross(ema_fast, ema_slow, today_idx, params["golden_cross_lookback_days"])
    if cross_idx is None:
        return None
    cross_date = df.index[cross_idx].strftime("%Y-%m-%d")
    cross_age_days = today_idx - cross_idx

    # Momentum gate: excludes a weak, just-formed cross where the EMAs
    # are all bunched together on a flat/rolling-over stock (e.g. Aditya
    # Birla Capital tangling near its EMAs after rolling over from a
    # high) -- close must be up at least min_momentum_pct over the
    # trailing momentum_lookback_days for a ticker to qualify at all.
    mom_lb = params["momentum_lookback_days"]
    if today_idx - mom_lb < 0:
        return None
    price_mom_ago = float(close.iloc[today_idx - mom_lb])
    if price_mom_ago <= 0:
        return None
    momentum_pct = (price_today - price_mom_ago) / price_mom_ago * 100
    if momentum_pct < params["min_momentum_pct"]:
        return None

    rising_lb = params["ema_rising_lookback_days"]
    def _rising(series: pd.Series) -> bool:
        return today_idx - rising_lb >= 0 and series.iloc[today_idx] > series.iloc[today_idx - rising_lb]

    aligned = (
        price_today > ema_fast_now > ema_mid_now > ema_slow_now
        and _rising(ema_fast) and _rising(ema_mid) and _rising(ema_slow)
    )

    vol_period = params["breakout_volume_avg_period"]
    vol_ratio_today = None
    if today_idx - vol_period >= 0:
        avg_vol = float(df["Volume"].iloc[today_idx - vol_period:today_idx].mean())
        if avg_vol > 0:
            vol_ratio_today = float(df["Volume"].iloc[today_idx]) / avg_vol
    volume_confirmed = vol_ratio_today is not None and vol_ratio_today >= params["breakout_volume_mult"]

    if aligned and volume_confirmed:
        status = "Confirmed Breakout"
    elif aligned:
        status = "Bullish Alignment"
    else:
        status = "Golden Cross Formed"

    why = [
        f"✓ Primary uptrend: close (₹{price_today:.2f}) is above its SMA{params['trend_sma_period']} "
        f"(₹{trend_sma_now:.2f})",
        f"✓ Golden Cross: EMA{params['ema_fast']} crossed above EMA{params['ema_slow']} on "
        f"{cross_date} ({cross_age_days} sessions ago), still holding above it",
        f"✓ Fanned out, not tangled: EMA{params['ema_fast']} is {spread_pct:.2f}% above EMA{params['ema_slow']}",
        f"✓ Trending, not flat: close is up {momentum_pct:.1f}% over the last {mom_lb} sessions",
        f"✓ RSI {rsi_now:.1f} within the {params['rsi_min']:.0f}-{params['rsi_max']:.0f} healthy range",
    ]
    if aligned:
        why.append(
            f"✓ Fully stacked and rising: Close (₹{price_today:.2f}) > EMA{params['ema_fast']} "
            f"(₹{ema_fast_now:.2f}) > EMA{params['ema_mid']} (₹{ema_mid_now:.2f}) > EMA{params['ema_slow']} "
            f"(₹{ema_slow_now:.2f})"
        )
    else:
        why.append("✗ Not yet fully stacked in bullish order (Close > EMA-fast > EMA-mid > EMA-slow)")
    if vol_ratio_today is not None:
        tick = "✓" if volume_confirmed else "✗"
        why.append(
            f"{tick} Volume {vol_ratio_today:.2f}x the prior {vol_period}-session average "
            f"({'meets' if volume_confirmed else 'below'} the {params['breakout_volume_mult']:.1f}x requirement)"
        )
    else:
        why.append("✗ Not enough prior sessions to compute the volume-confirmation ratio")

    return {
        "Ticker": ticker,
        "Setup Status": status,
        "Signal Date": df.index[today_idx].strftime("%Y-%m-%d"),
        "Current Price": round(price_today, 2),
        "EMA Fast": round(ema_fast_now, 2),
        "EMA Mid": round(ema_mid_now, 2),
        "EMA Slow": round(ema_slow_now, 2),
        "EMA Spread %": round(spread_pct, 2),
        "Trend SMA": round(trend_sma_now, 2),
        "Golden Cross Date": cross_date,
        "Days Since Cross": cross_age_days,
        "Momentum %": round(momentum_pct, 2),
        "RSI": round(rsi_now, 1),
        "Volume Ratio": round(vol_ratio_today, 2) if vol_ratio_today is not None else None,
        "Why Qualified": "\n".join(why),
        "_cross_idx": cross_idx,
        "_signal_idx": today_idx,
    }


_STATUS_ORDER = {"Confirmed Breakout": 0, "Bullish Alignment": 1, "Golden Cross Formed": 2}
_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "EMA Fast", "EMA Mid", "EMA Slow", "EMA Spread %", "Trend SMA", "Golden Cross Date", "Days Since Cross",
    "Momentum %", "RSI", "Volume Ratio", "Why Qualified",
]


def _build_table(rows: list[dict], status: str) -> pd.DataFrame:
    filtered = [r for r in rows if r["Setup Status"] == status]
    filtered.sort(key=lambda r: r["Days Since Cross"])
    df_out = pd.DataFrame(filtered, columns=[c for c in _DISPLAY_COLUMNS if c != "Rank"])
    if not df_out.empty:
        df_out.insert(0, "Rank", range(1, len(df_out) + 1))
    else:
        df_out = pd.DataFrame(columns=_DISPLAY_COLUMNS)
    return df_out


def scan_triple_ema_golden_cross(
    universe_name: str = "nifty500", min_price: float | None = None,
    params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs the Triple EMA Golden Cross strategy over a chosen universe."""
    if min_price is None:
        min_price = config.TRIPLE_EMA_MIN_PRICE_INR
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
        "golden_cross": _build_table(rows, "Golden Cross Formed"),
        "bullish_alignment": _build_table(rows, "Bullish Alignment"),
        "confirmed_breakout": _build_table(rows, "Confirmed Breakout"),
        "cross_idx": {r["Ticker"]: r["_cross_idx"] for r in rows},
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "min_price": min_price,
        "source": source,
        "universe_label": universe_label,
        "data_asof_date": data_asof_date,
    }


def build_golden_cross_chart(
    df: pd.DataFrame, cross_idx: int, signal_idx: int, params: dict, ticker: str,
):
    """3-row Plotly subplot: candlestick + EMA-fast (green) / EMA-mid
    (red) / EMA-slow (blue) + a marker at the golden-cross candle, /
    Volume (signal day highlighted) / RSI(14) with the strategy's own
    healthy-range band shaded.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    plot_start = max(0, cross_idx - 20)
    plot_end = min(len(df) - 1, signal_idx + 5)
    plot_df = df.iloc[plot_start:plot_end + 1]

    close = df["Close"]
    ema_fast = indicators.compute_ema(close, params["ema_fast"]).iloc[plot_start:plot_end + 1]
    ema_mid = indicators.compute_ema(close, params["ema_mid"]).iloc[plot_start:plot_end + 1]
    ema_slow = indicators.compute_ema(close, params["ema_slow"]).iloc[plot_start:plot_end + 1]
    rsi = indicators.compute_rsi(close).iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, row_heights=[0.55, 0.2, 0.25], vertical_spacing=0.05,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Triple EMA Golden Cross", "Volume", "RSI(14)"),
    )
    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ema_fast.index, y=ema_fast, mode="lines", name=f"EMA{params['ema_fast']}",
        line=dict(color="#3ECF8E", width=1.6),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ema_mid.index, y=ema_mid, mode="lines", name=f"EMA{params['ema_mid']}",
        line=dict(color="#FF6B6B", width=1.6),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ema_slow.index, y=ema_slow, mode="lines", name=f"EMA{params['ema_slow']}",
        line=dict(color="#4F7CFF", width=1.6),
    ), row=1, col=1)
    cross_price = float(indicators.compute_ema(close, params["ema_fast"]).iloc[cross_idx])
    fig.add_trace(go.Scatter(
        x=[df.index[cross_idx]], y=[cross_price],
        mode="markers", name="Golden Cross", marker=dict(color="#4FD1E8", size=12, symbol="cross"),
    ), row=1, col=1)

    vol_colors = ["#FFB020" if i == signal_idx else "#4F7CFF" for i in range(plot_start, plot_end + 1)]
    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color=vol_colors), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=rsi.index, y=rsi, mode="lines", name="RSI(14)", line=dict(color="#B26BFF", width=1.5),
    ), row=3, col=1)
    fig.add_hrect(y0=params["rsi_min"], y1=params["rsi_max"], fillcolor="#B26BFF", opacity=0.08, line_width=0, row=3, col=1)
    fig.add_hline(y=params["rsi_max"], line_dash="dot", line_color="#B0B0B0", row=3, col=1)
    fig.add_hline(y=params["rsi_min"], line_dash="dot", line_color="#B0B0B0", row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=620, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
