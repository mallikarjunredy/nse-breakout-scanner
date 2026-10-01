"""Daily Trend + Consolidation Breakout -- Version 2.

Version 1 (`src/trend_consolidation.py`) is completely untouched by this
module -- it keeps its own page, its own cache, and its own historical
scan results exactly as they were. This is a genuinely different rule
set within the same breakout family, built from a detailed written quant
spec rather than a chart or screenshot, and specifically NOT a copy with
different numbers:

- **Trend filter**: EMA(50)/EMA(200) (V1 uses SMA), and there is no
  relative-strength-vs-Nifty-50 gate at all -- the spec only requires
  Close > EMA50, EMA50 > EMA200, and EMA50 rising vs 5 sessions ago, for
  *both* the stock and the Nifty 50 benchmark. V1 instead requires only
  Nifty's Close > its own SMA200, plus a separate stock-beats-benchmark
  relative-strength check that V2 doesn't have.
- **Stop placement**: anchored to the stored consolidation *support*
  (`support - stop_atr_mult x signal-day ATR14`), not to the entry price
  the way V1's `entry - stop_atr_mult x ATR14` is. This is a structural
  "stop just below the base" placement, not a volatility distance from
  wherever the fill happened to land -- support is itself frozen at
  signal time (the `consolidation_period` window strictly before the
  signal candle), so today's own bar can never move its own stop.
- **New risk-distance cap**: a signal is skipped entirely if the
  resulting entry-to-stop distance would exceed `max_risk_distance_pct`
  of the entry price -- V1 has no equivalent cap.
- **New per-position size cap**: any one position is additionally capped
  at `max_position_pct` of current equity (on top of V1's existing
  risk-based and cash-available caps), so a single trade can never
  dominate the paper portfolio even if the risk-based sizing alone would
  allow a larger position.
- **Allocation tie-break**: when several signals compete for limited
  cash on the same entry day, V2 prioritizes by the preceding 20-session
  average daily traded value (liquidity) -- V1 prioritizes by the
  signal day's own volume-ratio spike instead.
- **Open positions at period end** are returned separately from the
  closed-trade list (never silently dropped from the count the way V1's
  own `run_backtest` implicitly does), and `compute_metrics` adds an
  average-holding-sessions figure and a by-year trade breakdown that
  V1's version of the same function doesn't track.

`compute_metrics`'s *closed-trade* statistics (win rate, expectancy,
profit factor, drawdown, ...) reuse `trend_consolidation.compute_metrics`
directly rather than reimplementing the same arithmetic a second time --
that function has no V1-specific coupling, and reusing it guarantees a
V1-vs-V2 comparison is computed identically on both sides.
"""

import datetime

import numpy as np
import pandas as pd

from . import config, indicators, scanner, trend_consolidation, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All NSE Stocks (₹100+)"),
}


def default_params() -> dict:
    return {
        "min_price": config.TREND_CONSOL_V2_MIN_PRICE_INR,
        "min_traded_value": config.TREND_CONSOL_V2_MIN_TRADED_VALUE_INR,
        "traded_value_avg_days": config.TREND_CONSOL_V2_TRADED_VALUE_AVG_DAYS,
        "ema_fast": config.TREND_CONSOL_V2_EMA_FAST,
        "ema_slow": config.TREND_CONSOL_V2_EMA_SLOW,
        "ema_fast_rising_lookback": config.TREND_CONSOL_V2_EMA_FAST_RISING_LOOKBACK,
        "consolidation_period": config.TREND_CONSOL_V2_CONSOLIDATION_PERIOD,
        "max_width_pct": config.TREND_CONSOL_V2_MAX_WIDTH_PCT,
        "prebreakout_distance_min_pct": config.TREND_CONSOL_V2_PREBREAKOUT_DISTANCE_MIN_PCT,
        "prebreakout_distance_max_pct": config.TREND_CONSOL_V2_PREBREAKOUT_DISTANCE_MAX_PCT,
        "breakout_buffer_pct": config.TREND_CONSOL_V2_BREAKOUT_BUFFER_PCT,
        "volume_avg_days": config.TREND_CONSOL_V2_VOLUME_AVG_DAYS,
        "volume_multiplier": config.TREND_CONSOL_V2_VOLUME_MULTIPLIER,
        "atr_period": config.TREND_CONSOL_V2_ATR_PERIOD,
        "benchmark_index": config.TREND_CONSOL_V2_BENCHMARK_INDEX,
        "min_history_sessions": config.TREND_CONSOL_V2_MIN_HISTORY_SESSIONS,
        # Backtest-only knobs (ignored by the live scan):
        "initial_equity": config.TREND_CONSOL_V2_BACKTEST_INITIAL_EQUITY_INR,
        "risk_pct": config.TREND_CONSOL_V2_BACKTEST_RISK_PCT,
        "stop_atr_mult": config.TREND_CONSOL_V2_BACKTEST_STOP_ATR_MULT,
        "max_risk_distance_pct": config.TREND_CONSOL_V2_BACKTEST_MAX_RISK_DISTANCE_PCT,
        "target_rr_mult": config.TREND_CONSOL_V2_BACKTEST_TARGET_RR_MULT,
        "max_position_pct": config.TREND_CONSOL_V2_BACKTEST_MAX_POSITION_PCT,
        "max_holding_sessions": config.TREND_CONSOL_V2_BACKTEST_MAX_HOLDING_SESSIONS,
        "entry_gap_max_pct": config.TREND_CONSOL_V2_BACKTEST_ENTRY_GAP_MAX_PCT,
        "brokerage_pct": config.TREND_CONSOL_V2_BACKTEST_BROKERAGE_PCT,
        "transaction_charges_pct": config.TREND_CONSOL_V2_BACKTEST_TRANSACTION_CHARGES_PCT,
        "slippage_pct": config.TREND_CONSOL_V2_BACKTEST_SLIPPAGE_PCT,
    }


# ---------------------------------------------------------------------------
# Vectorized signal frame -- shared by the live scan and the backtest, same
# discipline as V1: every rolling calculation is `.shift(1)` BEFORE
# `.rolling(...)`, so the signal session's own bar never contributes to its
# own resistance/support/volume-baseline/ADTV.
# ---------------------------------------------------------------------------

def _compute_signal_frame(
    df: pd.DataFrame, nifty_close: pd.Series, nifty_ema_fast: pd.Series, nifty_ema_slow: pd.Series,
    nifty_ema_fast_prior: pd.Series, params: dict,
) -> pd.DataFrame:
    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

    ema_fast = indicators.compute_ema(close, params["ema_fast"])
    ema_slow = indicators.compute_ema(close, params["ema_slow"])
    ema_fast_prior = ema_fast.shift(params["ema_fast_rising_lookback"])

    resistance = high.shift(1).rolling(params["consolidation_period"]).max()
    support = low.shift(1).rolling(params["consolidation_period"]).min()
    consolidation_width_pct = (resistance / support - 1) * 100

    vol_avg = volume.shift(1).rolling(params["volume_avg_days"]).mean()
    volume_ratio = volume / vol_avg
    # Traded value is an estimate (Close x Volume) -- yfinance has no
    # separate provider-supplied traded-value field, so this is always
    # labelled as an estimate in the UI, never presented as exchange data.
    adtv = (close * volume).shift(1).rolling(params["traded_value_avg_days"]).mean()

    nifty_close_aligned = nifty_close.reindex(df.index).ffill()
    nifty_ema_fast_aligned = nifty_ema_fast.reindex(df.index).ffill()
    nifty_ema_slow_aligned = nifty_ema_slow.reindex(df.index).ffill()
    nifty_ema_fast_prior_aligned = nifty_ema_fast_prior.reindex(df.index).ffill()

    market_trend_ok = (
        (nifty_close_aligned > nifty_ema_fast_aligned)
        & (nifty_ema_fast_aligned > nifty_ema_slow_aligned)
        & (nifty_ema_fast_aligned > nifty_ema_fast_prior_aligned)
    )

    price_ok = close > params["min_price"]
    liquidity_ok = adtv > params["min_traded_value"]
    stock_trend_ok = (close > ema_fast) & (ema_fast > ema_slow) & (ema_fast > ema_fast_prior)
    consolidation_ok = consolidation_width_pct <= params["max_width_pct"]

    base_ok = price_ok & liquidity_ok & stock_trend_ok & consolidation_ok & market_trend_ok

    distance_pct = (resistance - close) / resistance * 100
    prebreakout_ok = (
        base_ok & (close <= resistance)
        & (distance_pct >= params["prebreakout_distance_min_pct"])
        & (distance_pct <= params["prebreakout_distance_max_pct"])
    )

    # "Previous close at/below the stored resistance" -- checked
    # explicitly per the spec, even though for this fixed trailing-window
    # definition it's true by construction (yesterday's High, and hence
    # its Close, is itself part of the window whose max defines today's
    # resistance).
    breakout_price_ok = (
        base_ok
        & (close.shift(1) <= resistance)
        & (close > resistance * (1 + params["breakout_buffer_pct"] / 100))
    )
    volume_confirmed = volume_ratio >= params["volume_multiplier"]
    confirmed_breakout_ok = breakout_price_ok & volume_confirmed
    volume_unconfirmed_ok = breakout_price_ok & (~volume_confirmed)

    return pd.DataFrame({
        "close": close, "ema_fast": ema_fast, "ema_slow": ema_slow,
        "resistance": resistance, "support": support,
        "consolidation_width_pct": consolidation_width_pct,
        "volume_ratio": volume_ratio, "adtv": adtv,
        "distance_pct": distance_pct,
        "price_ok": price_ok.fillna(False), "liquidity_ok": liquidity_ok.fillna(False),
        "stock_trend_ok": stock_trend_ok.fillna(False), "consolidation_ok": consolidation_ok.fillna(False),
        "market_trend_ok": market_trend_ok.fillna(False), "volume_confirmed": volume_confirmed.fillna(False),
        "prebreakout_ok": prebreakout_ok.fillna(False),
        "confirmed_breakout_ok": confirmed_breakout_ok.fillna(False),
        "volume_unconfirmed_ok": volume_unconfirmed_ok.fillna(False),
    }, index=df.index)


# ---------------------------------------------------------------------------
# Live scan
# ---------------------------------------------------------------------------

_DISPLAY_COLUMNS = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance", "Support", "% Below Resistance", "Consolidation Width %", "Volume Ratio",
    "ADTV (₹ Cr, est.)", "EMA50", "EMA200", "Why Qualified",
]


def _evaluate_ticker_latest(ticker: str, df: pd.DataFrame, sig: pd.DataFrame, params: dict) -> dict | None:
    if len(df) < params["consolidation_period"] + params["ema_slow"] + 10:
        return None
    last = sig.iloc[-1]

    if bool(last["confirmed_breakout_ok"]):
        status = "Confirmed Breakout"
    elif bool(last["volume_unconfirmed_ok"]):
        status = "Price Breakout — Volume Unconfirmed"
    elif bool(last["prebreakout_ok"]):
        status = "Pre-Breakout Watchlist"
    else:
        return None

    if any(pd.isna(last[c]) for c in ("resistance", "support", "ema_fast", "ema_slow", "volume_ratio")):
        return None

    insufficient_history = len(df) < config.TREND_CONSOL_V2_MIN_HISTORY_SESSIONS
    missing_volume = bool(df["Volume"].iloc[-5:].isna().any()) or bool((df["Volume"].iloc[-5:] == 0).all())
    stale_data = (datetime.date.today() - df.index[-1].date()).days > 5

    # Every condition's actual computed value, shown explicitly regardless
    # of pass/fail -- the spec asks for failed conditions to be shown, not
    # just a final match/no-match verdict. Only reachable here for rows
    # that *did* ultimately qualify for one of the three statuses, but
    # each individual sub-condition's own tick still reflects its own
    # real pass/fail state on the signal day.
    checklist = [
        (last["price_ok"], f"Close ₹{last['close']:.2f} > min. price ₹{params['min_price']:.0f}"),
        (last["liquidity_ok"], f"20D ADTV (est.) ₹{last['adtv']/1_00_00_000:.2f} Cr > ₹{params['min_traded_value']/1_00_00_000:.0f} Cr"),
        (last["stock_trend_ok"], f"Close > EMA{params['ema_fast']} (₹{last['ema_fast']:.2f}) > EMA{params['ema_slow']} "
                                  f"(₹{last['ema_slow']:.2f}), EMA{params['ema_fast']} rising"),
        (last["consolidation_ok"], f"Consolidation width {last['consolidation_width_pct']:.2f}% "
                                    f"(<= {params['max_width_pct']:.1f}% over the preceding "
                                    f"{params['consolidation_period']} sessions)"),
        (last["market_trend_ok"], "Nifty 50 close > EMA50 > EMA200, with EMA50 rising"),
    ]
    why = [f"{'✓' if ok else '✗'} {text}" for ok, text in checklist]

    if status == "Pre-Breakout Watchlist":
        why.append(f"✓ Within {last['distance_pct']:.2f}% of resistance (₹{last['resistance']:.2f})")
    else:
        confirmed = status == "Confirmed Breakout"
        tick = "✓" if confirmed else "✗"
        why.append(
            f"✓ Close (₹{last['close']:.2f}) cleared resistance (₹{last['resistance']:.2f}) by at least "
            f"{params['breakout_buffer_pct']:.1f}%, from at/below resistance the previous session"
        )
        why.append(
            f"{tick} Volume {last['volume_ratio']:.2f}x the prior {params['volume_avg_days']}-session average "
            f"({'meets' if confirmed else 'below'} the {params['volume_multiplier']:.1f}x requirement)"
        )

    return {
        "Ticker": ticker,
        "Setup Status": status,
        "Signal Date": df.index[-1].strftime("%Y-%m-%d"),
        "Current Price": round(float(last["close"]), 2),
        "Resistance": round(float(last["resistance"]), 2),
        "Support": round(float(last["support"]), 2),
        "% Below Resistance": round(float(last["distance_pct"]), 2),
        "Consolidation Width %": round(float(last["consolidation_width_pct"]), 2),
        "Volume Ratio": round(float(last["volume_ratio"]), 2),
        "ADTV (₹ Cr, est.)": round(float(last["adtv"]) / 1_00_00_000, 2) if pd.notna(last["adtv"]) else None,
        "EMA50": round(float(last["ema_fast"]), 2),
        "EMA200": round(float(last["ema_slow"]), 2),
        "Why Qualified": "\n".join(why),
        "_signal_idx": len(df) - 1,
        "_insufficient_history": insufficient_history,
        "_missing_volume": missing_volume,
        "_stale_data": stale_data,
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


def scan_trend_consolidation_v2(
    universe_name: str = "nifty500", params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs Daily Trend + Consolidation Breakout V2 over a chosen
    universe. If the Nifty 50 benchmark can't be downloaded, the
    market-trend condition can't be verified -- rather than silently
    passing it, the scan returns empty result tables with
    `benchmark_available=False` so the page can say so.
    """
    if params is None:
        params = default_params()

    loader, universe_label = _UNIVERSE_LOADERS[universe_name]
    tickers, source = loader()

    if progress_callback:
        progress_callback(0.03, f"Downloading adjusted price history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers, auto_adjust=True)

    if progress_callback:
        progress_callback(0.1, "Downloading Nifty 50 index history...")
    nifty_hist = scanner.download_history([params["benchmark_index"]], auto_adjust=True)
    nifty_df = nifty_hist.get(params["benchmark_index"])
    benchmark_available = nifty_df is not None and len(nifty_df) >= params["ema_slow"] + params["ema_fast_rising_lookback"]

    rows = []
    if benchmark_available:
        nifty_close = nifty_df["Close"]
        nifty_ema_fast = indicators.compute_ema(nifty_close, params["ema_fast"])
        nifty_ema_slow = indicators.compute_ema(nifty_close, params["ema_slow"])
        nifty_ema_fast_prior = nifty_ema_fast.shift(params["ema_fast_rising_lookback"])

        total = len(history)
        for i, (ticker, df) in enumerate(history.items()):
            sig = _compute_signal_frame(df, nifty_close, nifty_ema_fast, nifty_ema_slow, nifty_ema_fast_prior, params)
            row = _evaluate_ticker_latest(ticker, df, sig, params)
            if row is not None:
                rows.append(row)
            if progress_callback and total:
                progress_callback(0.15 + 0.7 * (i + 1) / total, f"Evaluating {ticker}...")

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
        "volume_unconfirmed": _build_table(rows, "Price Breakout — Volume Unconfirmed"),
        "signal_idx": {r["Ticker"]: r["_signal_idx"] for r in rows},
        "resistance_by_ticker": {r["Ticker"]: r["Resistance"] for r in rows},
        "support_by_ticker": {r["Ticker"]: r["Support"] for r in rows},
        "universe_size": len(tickers),
        "scanned": len(history),
        "universe_label": universe_label,
        "source": source,
        "benchmark_available": benchmark_available,
        "data_asof_date": data_asof_date,
        "insufficient_history_count": sum(1 for r in rows if r["_insufficient_history"]),
        "missing_volume_count": sum(1 for r in rows if r["_missing_volume"]),
        "stale_data_count": sum(1 for r in rows if r["_stale_data"]),
    }


# ---------------------------------------------------------------------------
# Charting
# ---------------------------------------------------------------------------

def build_trend_chart(
    df: pd.DataFrame, ticker: str, signal_idx: int, resistance: float, support: float, params: dict,
    trade: dict | None = None,
):
    """Same consolidation-window visual as V1's chart (candlestick +
    frozen resistance/support + shaded band + signal marker), with
    EMA50/EMA200 instead of SMA50/SMA200, plus -- when a `trade` dict is
    passed (entry/stop/target/exit, if this signal was actually paper-
    traded in a backtest run) -- the executed trade levels drawn as
    dashed lines, clearly labelled as estimates/paper-trade levels, not
    guaranteed prices.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    consolidation_period = params["consolidation_period"]
    window_start_idx = max(0, signal_idx - consolidation_period)
    plot_end_idx = signal_idx + (params.get("max_holding_sessions", 15) + 5 if trade else 10)
    plot_start = max(0, window_start_idx - 15)
    plot_end = min(len(df) - 1, plot_end_idx)
    plot_df = df.iloc[plot_start:plot_end + 1]

    ema_fast = indicators.compute_ema(df["Close"], params["ema_fast"]).iloc[plot_start:plot_end + 1]
    ema_slow = indicators.compute_ema(df["Close"], params["ema_slow"]).iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.05,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Trend + Consolidation Breakout V2", "Volume"),
    )

    fig.add_trace(go.Candlestick(
        x=plot_df.index, open=plot_df["Open"], high=plot_df["High"], low=plot_df["Low"], close=plot_df["Close"],
        increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
    ), row=1, col=1)

    window_start_date = df.index[window_start_idx]
    window_end_date = df.index[signal_idx]

    fig.add_shape(
        type="rect", x0=window_start_date, x1=window_end_date, y0=support, y1=resistance,
        fillcolor="rgba(79,209,232,0.10)", line=dict(width=0), row=1, col=1,
    )
    fig.add_trace(go.Scatter(
        x=[window_start_date, window_end_date], y=[resistance, resistance], mode="lines",
        name="Resistance (signal)", line=dict(color="#FF6B6B", width=2),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[window_start_date, window_end_date], y=[support, support], mode="lines",
        name="Support (signal)", line=dict(color="#3ECF8E", width=2, dash="dot"),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ema_fast.index, y=ema_fast, mode="lines", name="EMA50", line=dict(color="#FFB020", width=1.3),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ema_slow.index, y=ema_slow, mode="lines", name="EMA200", line=dict(color="#B26BFF", width=1.3),
    ), row=1, col=1)

    sig_date = df.index[signal_idx]
    sig_high = float(df["High"].iloc[signal_idx])
    fig.add_annotation(
        x=sig_date, y=sig_high, text="Signal", showarrow=True, arrowhead=2, yshift=18,
        font=dict(color="#EAF2FA"), arrowcolor="#FFB020", row=1, col=1,
    )

    if trade is not None:
        entry_date, exit_date = trade["entry_date"], trade["exit_date"]
        fig.add_trace(go.Scatter(
            x=[entry_date, exit_date], y=[trade["entry_price"]] * 2, mode="lines",
            name="Entry (paper trade, est.)", line=dict(color="#4F7CFF", width=2, dash="dash"),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[entry_date, exit_date], y=[trade["stop"]] * 2, mode="lines",
            name="Stop (paper trade, est.)", line=dict(color="#FF6B6B", width=2, dash="dash"),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[entry_date, exit_date], y=[trade["target"]] * 2, mode="lines",
            name="Target (paper trade, est.)", line=dict(color="#3ECF8E", width=2, dash="dash"),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[exit_date], y=[trade["exit_price"]], mode="markers", name=f"Exit ({trade['exit_reason']})",
            marker=dict(color="#FFB020", size=11, symbol="x"),
        ), row=1, col=1)

    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df["Volume"], name="Volume", marker_color="#4F7CFF"), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=600, margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    return fig


# ---------------------------------------------------------------------------
# Paper-trading / backtest engine
# ---------------------------------------------------------------------------

def generate_backtest_signals(history: dict[str, pd.DataFrame], nifty_df: pd.DataFrame, params: dict) -> list[dict]:
    """Every historical Confirmed Breakout date, across the whole
    universe, with the data needed to simulate an entry (signal close,
    resistance, support, ATR14, volume ratio, ADTV at signal time).
    """
    nifty_close = nifty_df["Close"]
    nifty_ema_fast = indicators.compute_ema(nifty_close, params["ema_fast"])
    nifty_ema_slow = indicators.compute_ema(nifty_close, params["ema_slow"])
    nifty_ema_fast_prior = nifty_ema_fast.shift(params["ema_fast_rising_lookback"])

    signals = []
    for ticker, df in history.items():
        if len(df) < params["min_history_sessions"]:
            continue
        sig = _compute_signal_frame(df, nifty_close, nifty_ema_fast, nifty_ema_slow, nifty_ema_fast_prior, params)
        atr = indicators.compute_atr(df, period=params["atr_period"])
        confirmed_mask = sig["confirmed_breakout_ok"].to_numpy()
        confirmed_positions = np.flatnonzero(confirmed_mask)
        for idx in confirmed_positions:
            if idx + 1 >= len(df):
                continue  # no next session yet to enter on
            atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else None
            support_val = float(sig["support"].iloc[idx]) if pd.notna(sig["support"].iloc[idx]) else None
            adtv_val = float(sig["adtv"].iloc[idx]) if pd.notna(sig["adtv"].iloc[idx]) else 0.0
            if atr_val is None or atr_val <= 0 or support_val is None:
                continue
            signals.append({
                "ticker": ticker,
                "signal_idx": int(idx),
                "signal_date": df.index[idx],
                "signal_close": float(df["Close"].iloc[idx]),
                "resistance": float(sig["resistance"].iloc[idx]),
                "support": support_val,
                "atr": atr_val,
                "volume_ratio": float(sig["volume_ratio"].iloc[idx]),
                "adtv": adtv_val,
            })
    return signals


def _entry_cost(price: float, shares: int, params: dict) -> float:
    gross = price * shares
    return gross + gross * (params["brokerage_pct"] + params["transaction_charges_pct"] + params["slippage_pct"]) / 100


def _exit_proceeds(price: float, shares: int, params: dict) -> float:
    gross = price * shares
    return gross - gross * (params["brokerage_pct"] + params["transaction_charges_pct"] + params["slippage_pct"]) / 100


def run_backtest(
    signals: list[dict], history: dict[str, pd.DataFrame], params: dict,
    start_date: pd.Timestamp, end_date: pd.Timestamp,
) -> tuple[list[dict], list[tuple], list[dict]]:
    """Same walk-forward mechanics as V1's `run_backtest` (one open
    position per ticker, entries fill at the next session's open,
    exits checked against each day's own OHLC, equity marked to market
    daily), with V2's own differences:

    - Stop = stored support - `stop_atr_mult` x signal-day ATR14 (not
      entry - mult x ATR14), computed once at signal time and frozen.
    - A signal is skipped if the resulting entry-to-stop distance
      exceeds `max_risk_distance_pct` of entry.
    - Position size is additionally capped at `max_position_pct` of
      current equity, on top of the existing risk-based and cash-based
      caps.
    - Same-day cash competition is resolved by ADTV (liquidity)
      descending, not by volume-ratio spike.
    - Positions still open at `end_date` are returned separately (never
      silently folded into the closed-trade stats).

    Returns (closed_trades, equity_curve, open_positions_at_end).
    """
    initial_equity = params["initial_equity"]
    all_dates = sorted({d for df in history.values() for d in df.index if start_date <= d <= end_date})

    entries_by_date: dict[pd.Timestamp, list[dict]] = {}
    for s in signals:
        df = history.get(s["ticker"])
        if df is None:
            continue
        entry_idx = s["signal_idx"] + 1
        if entry_idx >= len(df):
            continue
        entry_date = df.index[entry_idx]
        if not (start_date <= entry_date <= end_date):
            continue
        entry = dict(s)
        entry["entry_idx"] = entry_idx
        entry["entry_date"] = entry_date
        entries_by_date.setdefault(entry_date, []).append(entry)

    cash = initial_equity
    open_positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve: list[tuple] = []

    for date in all_dates:
        # --- Entries: fill at today's open for yesterday's signals -----
        candidates = [s for s in entries_by_date.get(date, []) if s["ticker"] not in open_positions]
        valid = []
        for s in candidates:
            df = history[s["ticker"]]
            entry_open = float(df["Open"].iloc[s["entry_idx"]])
            if entry_open > s["signal_close"] * (1 + params["entry_gap_max_pct"] / 100):
                continue  # gapped up too far above the signal close
            if entry_open <= s["resistance"]:
                continue  # opened back at/below the breakout level -- not a real follow-through
            s["entry_open"] = entry_open
            valid.append(s)
        # Deterministic allocation: highest preceding-20-session ADTV
        # (liquidity) first, ticker alphabetical as the tie-break.
        valid.sort(key=lambda s: (-s["adtv"], s["ticker"]))

        mtm_open_positions = 0.0
        for t, p in open_positions.items():
            df_t = history[t]
            if date in df_t.index:
                mtm_open_positions += float(df_t["Close"].iloc[df_t.index.get_loc(date)]) * p["shares"]
            else:
                mtm_open_positions += p["entry_price"] * p["shares"]
        equity_now = cash + mtm_open_positions

        for s in valid:
            entry_price = s["entry_open"]
            initial_stop = s["support"] - params["stop_atr_mult"] * s["atr"]
            if initial_stop >= entry_price:
                continue  # stop at/above entry -- not a usable signal
            per_share_risk = entry_price - initial_stop
            risk_distance_pct = per_share_risk / entry_price * 100
            if risk_distance_pct > params["max_risk_distance_pct"]:
                continue  # stop too far away relative to entry -- skip, don't force a trade
            target = entry_price + params["target_rr_mult"] * per_share_risk

            risk_amount = equity_now * params["risk_pct"] / 100
            risk_based_shares = int(risk_amount / per_share_risk)
            fill_cost_per_share = entry_price * (1 + params["slippage_pct"] / 100)
            cash_based_shares = int(cash / fill_cost_per_share) if fill_cost_per_share > 0 else 0
            position_cap_value = equity_now * params["max_position_pct"] / 100
            position_cap_shares = int(position_cap_value / fill_cost_per_share) if fill_cost_per_share > 0 else 0
            shares = min(risk_based_shares, cash_based_shares, position_cap_shares)
            if shares <= 0:
                continue

            cost_basis = _entry_cost(entry_price, shares, params)
            if cost_basis > cash:
                continue
            cash -= cost_basis
            open_positions[s["ticker"]] = {
                "entry_date": date, "entry_price": entry_price, "shares": shares,
                "stop": initial_stop, "target": target, "cost_basis": cost_basis,
                "holding_sessions": 1,
            }

        # --- Exits: check every open position against today's OHLC ------
        for ticker in list(open_positions.keys()):
            pos = open_positions[ticker]
            df = history[ticker]
            if date not in df.index:
                continue
            if pos["entry_date"] != date:
                pos["holding_sessions"] += 1
            idx = df.index.get_loc(date)
            o = float(df["Open"].iloc[idx])
            h = float(df["High"].iloc[idx])
            l = float(df["Low"].iloc[idx])
            c = float(df["Close"].iloc[idx])

            exit_price, exit_reason, ambiguous = None, None, False
            if o <= pos["stop"]:
                exit_price, exit_reason = o, "Stop (gap)"
            else:
                hit_stop = l <= pos["stop"]
                hit_target = h >= pos["target"]
                if hit_stop and hit_target:
                    exit_price, exit_reason, ambiguous = pos["stop"], "Stop (ambiguous same-day stop+target)", True
                elif hit_stop:
                    exit_price, exit_reason = pos["stop"], "Stop"
                elif hit_target:
                    exit_price, exit_reason = pos["target"], "Target"
                elif pos["holding_sessions"] >= params["max_holding_sessions"]:
                    exit_price, exit_reason = c, "Time"

            if exit_price is not None:
                proceeds = _exit_proceeds(exit_price, pos["shares"], params)
                cash += proceeds
                pnl = proceeds - pos["cost_basis"]
                trades.append({
                    "ticker": ticker, "entry_date": pos["entry_date"], "exit_date": date,
                    "entry_price": pos["entry_price"], "exit_price": exit_price, "shares": pos["shares"],
                    "stop": pos["stop"], "target": pos["target"],
                    "pnl": pnl, "pnl_pct": pnl / pos["cost_basis"] * 100 if pos["cost_basis"] else 0.0,
                    "exit_reason": exit_reason, "ambiguous_stop_target": ambiguous,
                    "holding_sessions": pos["holding_sessions"],
                })
                del open_positions[ticker]

        # --- Mark to market for the equity curve -------------------------
        mtm = cash
        for t, p in open_positions.items():
            df_t = history[t]
            if date in df_t.index:
                mtm += float(df_t["Close"].iloc[df_t.index.get_loc(date)]) * p["shares"]
            else:
                mtm += p["entry_price"] * p["shares"]
        equity_curve.append((date, mtm))

    open_at_end = [
        {"ticker": t, "entry_date": p["entry_date"], "entry_price": p["entry_price"], "shares": p["shares"],
         "stop": p["stop"], "target": p["target"], "holding_sessions": p["holding_sessions"]}
        for t, p in open_positions.items()
    ]
    return trades, equity_curve, open_at_end


def compute_metrics(trades: list[dict], equity_curve: list[tuple], initial_equity: float) -> dict:
    """Reuses V1's `compute_metrics` for every closed-trade statistic it
    already computes (identical formulas, so a V1-vs-V2 comparison is
    apples-to-apples), then adds the figures the written V2 spec asks
    for that V1's version doesn't track: average holding period and a
    by-year breakdown of closed trades.
    """
    base = trend_consolidation.compute_metrics(trades, equity_curve, initial_equity)
    if not trades:
        base["avg_holding_sessions"] = None
        base["by_year"] = {}
        return base

    base["avg_holding_sessions"] = round(sum(t["holding_sessions"] for t in trades) / len(trades), 1)

    by_year: dict[int, dict] = {}
    for t in trades:
        year = pd.Timestamp(t["exit_date"]).year
        yr = by_year.setdefault(year, {"trade_count": 0, "wins": 0, "pnl": 0.0})
        yr["trade_count"] += 1
        yr["wins"] += 1 if t["pnl"] > 0 else 0
        yr["pnl"] += t["pnl"]
    base["by_year"] = {
        year: {
            "trade_count": v["trade_count"],
            "win_rate": round(v["wins"] / v["trade_count"] * 100, 1) if v["trade_count"] else None,
            "net_pnl": round(v["pnl"], 2),
        }
        for year, v in sorted(by_year.items())
    }
    return base


def run_full_backtest(
    universe_name: str,
    params: dict,
    dev_start: pd.Timestamp, dev_end: pd.Timestamp,
    oos_start: pd.Timestamp, oos_end: pd.Timestamp,
    progress_callback=None,
) -> dict:
    """Runs the development and out-of-sample windows as two independent
    simulations, same boundary discipline as V1's `run_full_backtest`.

    Survivorship-bias note: same caveat as V1 -- the universe list is
    today's Nifty 500/All-NSE membership, not a historical point-in-time
    snapshot (no free reliable source for one exists), so a stock added
    to or dropped from the index during the backtest window is still (or
    already) treated as a member for dates before (or after) that
    actually happened. Disclosed on the page, not corrected for.
    """
    loader, universe_label = _UNIVERSE_LOADERS[universe_name]
    tickers, source = loader()

    if progress_callback:
        progress_callback(0.05, f"Downloading adjusted history for {len(tickers)} tickers...")
    history = scanner.download_history(tickers, auto_adjust=True)

    if progress_callback:
        progress_callback(0.15, "Downloading Nifty 50 index history...")
    nifty_hist = scanner.download_history([params["benchmark_index"]], auto_adjust=True)
    nifty_df = nifty_hist.get(params["benchmark_index"])
    if nifty_df is None:
        return {"error": "Nifty 50 index data was unavailable -- the market-trend condition can't be "
                          "verified, so the backtest can't run."}

    if progress_callback:
        progress_callback(0.3, "Generating historical signals across the full universe history...")
    signals = generate_backtest_signals(history, nifty_df, params)

    if progress_callback:
        progress_callback(0.6, "Simulating the development period...")
    dev_trades, dev_curve, dev_open = run_backtest(signals, history, params, dev_start, dev_end)

    if progress_callback:
        progress_callback(0.85, "Simulating the out-of-sample period...")
    oos_trades, oos_curve, oos_open = run_backtest(signals, history, params, oos_start, oos_end)

    if progress_callback:
        progress_callback(1.0, "Done.")

    return {
        "dev_trades": dev_trades, "dev_curve": dev_curve, "dev_open": dev_open,
        "dev_metrics": compute_metrics(dev_trades, dev_curve, params["initial_equity"]),
        "oos_trades": oos_trades, "oos_curve": oos_curve, "oos_open": oos_open,
        "oos_metrics": compute_metrics(oos_trades, oos_curve, params["initial_equity"]),
        "universe_label": universe_label, "universe_size": len(tickers), "scanned": len(history),
        "signal_count": len(signals),
    }
