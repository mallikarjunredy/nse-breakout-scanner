"""Daily Trend + Consolidation Breakout strategy, plus a walk-forward
paper-trading backtest over the same rules.

Every rolling/shift computation here is deliberately causal (uses
`.shift(1)` before any `.rolling(...)` that feeds a decision) so the
exact same vectorized signal frame can back both the live "what looks
good today" scan and the historical "what would have fired on every
past date" backtest, with no future-data leakage and no duplicated
logic between the two.

Uses split/dividend-adjusted OHLC (`scanner.download_history(...,
auto_adjust=True)`), same reasoning as the Rising Channel strategy: a
raw stock split would otherwise look like a fake consolidation
breakdown or resistance break.
"""

import datetime

import numpy as np
import pandas as pd

from . import config, indicators, scanner, universe

_UNIVERSE_LOADERS = {
    "nifty500": (universe.get_nifty_500, "Nifty 500"),
    "all_nse": (universe.get_nse_all, "All NSE Stocks (₹100+)"),
}


def default_params() -> dict:
    return {
        "min_price": config.TREND_CONSOL_MIN_PRICE_INR,
        "min_traded_value": config.TREND_CONSOL_MIN_TRADED_VALUE_INR,
        "traded_value_avg_days": config.TREND_CONSOL_TRADED_VALUE_AVG_DAYS,
        "sma_fast": config.TREND_CONSOL_SMA_FAST,
        "sma_slow": config.TREND_CONSOL_SMA_SLOW,
        "sma_fast_rising_lookback": config.TREND_CONSOL_SMA_FAST_RISING_LOOKBACK,
        "relative_strength_days": config.TREND_CONSOL_RELATIVE_STRENGTH_DAYS,
        "consolidation_period": config.TREND_CONSOL_CONSOLIDATION_PERIOD,
        "max_width_pct": config.TREND_CONSOL_MAX_WIDTH_PCT,
        "prebreakout_distance_min_pct": config.TREND_CONSOL_PREBREAKOUT_DISTANCE_MIN_PCT,
        "prebreakout_distance_max_pct": config.TREND_CONSOL_PREBREAKOUT_DISTANCE_MAX_PCT,
        "breakout_buffer_pct": config.TREND_CONSOL_BREAKOUT_BUFFER_PCT,
        "volume_avg_days": config.TREND_CONSOL_VOLUME_AVG_DAYS,
        "volume_multiplier": config.TREND_CONSOL_VOLUME_MULTIPLIER,
        "atr_period": config.TREND_CONSOL_ATR_PERIOD,
        "benchmark_index": config.TREND_CONSOL_BENCHMARK_INDEX,
        "min_history_sessions": config.TREND_CONSOL_MIN_HISTORY_SESSIONS,
        # Backtest-only knobs (ignored by the live scan):
        "initial_equity": config.TREND_CONSOL_BACKTEST_INITIAL_EQUITY_INR,
        "risk_pct": config.TREND_CONSOL_BACKTEST_RISK_PCT,
        "stop_atr_mult": config.TREND_CONSOL_BACKTEST_STOP_ATR_MULT,
        "target_rr_mult": config.TREND_CONSOL_BACKTEST_TARGET_RR_MULT,
        "max_holding_sessions": config.TREND_CONSOL_BACKTEST_MAX_HOLDING_SESSIONS,
        "entry_gap_max_pct": config.TREND_CONSOL_BACKTEST_ENTRY_GAP_MAX_PCT,
        "brokerage_pct": config.TREND_CONSOL_BACKTEST_BROKERAGE_PCT,
        "transaction_charges_pct": config.TREND_CONSOL_BACKTEST_TRANSACTION_CHARGES_PCT,
        "slippage_pct": config.TREND_CONSOL_BACKTEST_SLIPPAGE_PCT,
    }


# ---------------------------------------------------------------------------
# Vectorized signal frame -- the single source of truth shared by the live
# scan and the backtest.
# ---------------------------------------------------------------------------

def _compute_signal_frame(
    df: pd.DataFrame, nifty_close: pd.Series, nifty_sma_slow: pd.Series,
    nifty_ret: pd.Series, params: dict,
) -> pd.DataFrame:
    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

    sma_fast = indicators.compute_sma(close, params["sma_fast"])
    sma_slow = indicators.compute_sma(close, params["sma_slow"])
    sma_fast_prior = sma_fast.shift(params["sma_fast_rising_lookback"])

    # Consolidation window: the `consolidation_period` sessions strictly
    # BEFORE today (shift(1) first, then roll) -- today's own bar never
    # contributes to its own resistance/support, by construction.
    resistance = high.shift(1).rolling(params["consolidation_period"]).max()
    support = low.shift(1).rolling(params["consolidation_period"]).min()
    consolidation_width_pct = (resistance - support) / support * 100

    vol_avg = volume.shift(1).rolling(params["volume_avg_days"]).mean()
    volume_ratio = volume / vol_avg
    adtv = (close * volume).shift(1).rolling(params["traded_value_avg_days"]).mean()

    stock_ret = close / close.shift(params["relative_strength_days"]) - 1

    nifty_close_aligned = nifty_close.reindex(df.index).ffill()
    nifty_sma_slow_aligned = nifty_sma_slow.reindex(df.index).ffill()
    nifty_ret_aligned = nifty_ret.reindex(df.index).ffill()

    market_trend_ok = nifty_close_aligned > nifty_sma_slow_aligned
    relative_strength_ok = stock_ret > nifty_ret_aligned

    price_ok = close > params["min_price"]
    liquidity_ok = adtv > params["min_traded_value"]
    stock_trend_ok = (close > sma_fast) & (sma_fast > sma_slow) & (sma_fast > sma_fast_prior)
    consolidation_ok = consolidation_width_pct <= params["max_width_pct"]

    base_ok = (
        price_ok & liquidity_ok & stock_trend_ok & consolidation_ok
        & market_trend_ok & relative_strength_ok
    )

    distance_pct = (resistance - close) / resistance * 100
    prebreakout_ok = (
        base_ok & (close <= resistance)
        & (distance_pct >= params["prebreakout_distance_min_pct"])
        & (distance_pct <= params["prebreakout_distance_max_pct"])
    )

    # "Previous close at/below the resistance used for this signal" --
    # explicit per the spec, even though for this fixed-window definition
    # it's true by construction (yesterday's High, hence >= its Close, is
    # itself part of the window whose max defines today's resistance).
    breakout_price_ok = (
        base_ok
        & (close.shift(1) <= resistance)
        & (close >= resistance * (1 + params["breakout_buffer_pct"] / 100))
    )
    volume_confirmed = volume_ratio >= params["volume_multiplier"]
    confirmed_breakout_ok = breakout_price_ok & volume_confirmed
    volume_unconfirmed_ok = breakout_price_ok & (~volume_confirmed)

    return pd.DataFrame({
        "close": close, "sma_fast": sma_fast, "sma_slow": sma_slow,
        "resistance": resistance, "support": support,
        "consolidation_width_pct": consolidation_width_pct,
        "volume_ratio": volume_ratio, "adtv": adtv,
        "stock_ret_pct": stock_ret * 100, "benchmark_ret_pct": nifty_ret_aligned * 100,
        "distance_pct": distance_pct,
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
    "SMA50", "SMA200", "Stock 63D Return %", "Benchmark 63D Return %", "Why Qualified",
]


def _evaluate_ticker_latest(ticker: str, df: pd.DataFrame, sig: pd.DataFrame, params: dict) -> dict | None:
    if len(df) < params["consolidation_period"] + params["sma_slow"] + 10:
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

    if any(pd.isna(last[c]) for c in ("resistance", "support", "sma_fast", "sma_slow", "volume_ratio")):
        return None

    insufficient_history = len(df) < config.TREND_CONSOL_MIN_HISTORY_SESSIONS
    missing_volume = bool(df["Volume"].iloc[-5:].isna().any()) or bool((df["Volume"].iloc[-5:] == 0).all())
    stale_data = (datetime.date.today() - df.index[-1].date()).days > 5

    if status == "Pre-Breakout Watchlist":
        why = [
            f"✓ Within {last['distance_pct']:.2f}% of resistance (₹{last['resistance']:.2f})",
            f"✓ Consolidation width {last['consolidation_width_pct']:.2f}% "
            f"(<= {params['max_width_pct']:.1f}% over the preceding {params['consolidation_period']} sessions)",
            f"✓ Close above rising SMA50 (₹{last['sma_fast']:.2f}), SMA50 above SMA200 (₹{last['sma_slow']:.2f})",
            f"✓ Nifty 50 above its SMA200, and the stock's {params['relative_strength_days']}-session return "
            f"({last['stock_ret_pct']:.2f}%) beats Nifty 50's ({last['benchmark_ret_pct']:.2f}%)",
        ]
    else:
        confirmed = status == "Confirmed Breakout"
        tick = "✓" if confirmed else "✗"
        why = [
            f"✓ Close (₹{last['close']:.2f}) cleared resistance (₹{last['resistance']:.2f}) by at least "
            f"{params['breakout_buffer_pct']:.1f}%, from at/below resistance the previous session",
            f"{tick} Volume {last['volume_ratio']:.2f}x the prior {params['volume_avg_days']}-session average "
            f"({'meets' if confirmed else 'below'} the {params['volume_multiplier']:.1f}x requirement)",
            f"✓ Consolidation width {last['consolidation_width_pct']:.2f}% before the breakout "
            f"(<= {params['max_width_pct']:.1f}%)",
            f"✓ Trend/market/relative-strength conditions all held on the signal day",
        ]

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
        "SMA50": round(float(last["sma_fast"]), 2),
        "SMA200": round(float(last["sma_slow"]), 2),
        "Stock 63D Return %": round(float(last["stock_ret_pct"]), 2) if pd.notna(last["stock_ret_pct"]) else None,
        "Benchmark 63D Return %": round(float(last["benchmark_ret_pct"]), 2) if pd.notna(last["benchmark_ret_pct"]) else None,
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


def scan_trend_consolidation(
    universe_name: str = "nifty500", params: dict | None = None, progress_callback=None,
) -> dict:
    """Runs the Daily Trend + Consolidation Breakout strategy over a
    chosen universe. If the Nifty 50 benchmark can't be downloaded, the
    market-trend and relative-strength conditions can't be verified --
    rather than silently passing them, the scan returns empty result
    tables with `benchmark_available=False` so the page can say so.
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
    benchmark_available = nifty_df is not None and len(nifty_df) >= params["sma_slow"] + params["relative_strength_days"]

    rows = []
    if benchmark_available:
        nifty_close = nifty_df["Close"]
        nifty_sma_slow = indicators.compute_sma(nifty_close, params["sma_slow"])
        nifty_ret = nifty_close / nifty_close.shift(params["relative_strength_days"]) - 1

        total = len(history)
        for i, (ticker, df) in enumerate(history.items()):
            sig = _compute_signal_frame(df, nifty_close, nifty_sma_slow, nifty_ret, params)
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

def build_trend_chart(df: pd.DataFrame, ticker: str, signal_idx: int, resistance: float, support: float, params: dict):
    """Candlestick + Volume, with the (frozen) flat resistance/support
    drawn across the consolidation window and a shaded band between them,
    SMA50/SMA200, and the signal candle highlighted. Resistance/support
    are passed in as plain numbers captured at scan time, so this chart
    never changes when later data arrives for the same historical signal.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    consolidation_period = params["consolidation_period"]
    window_start_idx = max(0, signal_idx - consolidation_period)
    plot_start = max(0, window_start_idx - 15)
    plot_end = min(len(df) - 1, signal_idx + 10)
    plot_df = df.iloc[plot_start:plot_end + 1]

    sma_fast = indicators.compute_sma(df["Close"], params["sma_fast"]).iloc[plot_start:plot_end + 1]
    sma_slow = indicators.compute_sma(df["Close"], params["sma_slow"]).iloc[plot_start:plot_end + 1]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.05,
        subplot_titles=(f"{ticker.replace('.NS', '')} — Trend + Consolidation Breakout", "Volume"),
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
        x=sma_fast.index, y=sma_fast, mode="lines", name="SMA50", line=dict(color="#FFB020", width=1.3),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=sma_slow.index, y=sma_slow, mode="lines", name="SMA200", line=dict(color="#B26BFF", width=1.3),
    ), row=1, col=1)

    sig_date = df.index[signal_idx]
    sig_high = float(df["High"].iloc[signal_idx])
    fig.add_annotation(
        x=sig_date, y=sig_high, text="Signal", showarrow=True, arrowhead=2, yshift=18,
        font=dict(color="#EAF2FA"), arrowcolor="#FFB020", row=1, col=1,
    )

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
#
# This is a walk-forward simulation, not a re-application of "today's"
# scan to the past: `generate_backtest_signals` re-derives every
# historical Confirmed Breakout date directly from each ticker's own
# price history using the exact same `_compute_signal_frame` the live
# scan uses, so there is exactly one place the strategy's rules are
# encoded. Every rolling calculation is `.shift(1)`-first (see above),
# so a signal on date T never sees date T's own bar in its resistance/
# volume baseline, and entries always fill on the *next* session's open
# -- no future-data leakage in either signal generation or execution.
# ---------------------------------------------------------------------------

def generate_backtest_signals(history: dict[str, pd.DataFrame], nifty_df: pd.DataFrame, params: dict) -> list[dict]:
    """Every historical Confirmed Breakout date, across the whole
    universe, with the data needed to simulate an entry (signal close,
    resistance, ATR14, volume ratio at signal time).
    """
    nifty_close = nifty_df["Close"]
    nifty_sma_slow = indicators.compute_sma(nifty_close, params["sma_slow"])
    nifty_ret = nifty_close / nifty_close.shift(params["relative_strength_days"]) - 1

    signals = []
    for ticker, df in history.items():
        if len(df) < params["min_history_sessions"]:
            continue
        sig = _compute_signal_frame(df, nifty_close, nifty_sma_slow, nifty_ret, params)
        atr = indicators.compute_atr(df, period=params["atr_period"])
        confirmed_mask = sig["confirmed_breakout_ok"].to_numpy()
        confirmed_positions = np.flatnonzero(confirmed_mask)
        for idx in confirmed_positions:
            if idx + 1 >= len(df):
                continue  # no next session yet to enter on
            atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else None
            if atr_val is None or atr_val <= 0:
                continue
            signals.append({
                "ticker": ticker,
                "signal_idx": int(idx),
                "signal_date": df.index[idx],
                "signal_close": float(df["Close"].iloc[idx]),
                "resistance": float(sig["resistance"].iloc[idx]),
                "atr": atr_val,
                "volume_ratio": float(sig["volume_ratio"].iloc[idx]),
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
) -> tuple[list[dict], list[tuple]]:
    """Walks the master calendar (the union of every ticker's trading
    days within [start_date, end_date]) day by day: fills entries at that
    day's open for signals generated on the *previous* session, manages
    open positions' stop/target/time exits using that day's own OHLC, and
    marks the whole book to market for the equity curve. One open
    position per ticker; a deterministic allocation rule (strongest
    volume confirmation first, ticker alphabetical as the tie-break)
    decides who gets filled first when cash is limited.
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
        # Deterministic allocation: strongest volume confirmation first,
        # ticker alphabetical as the tie-break.
        valid.sort(key=lambda s: (-s["volume_ratio"], s["ticker"]))

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
            initial_stop = entry_price - params["stop_atr_mult"] * s["atr"]
            per_share_risk = entry_price - initial_stop
            if per_share_risk <= 0:
                continue
            target = entry_price + params["target_rr_mult"] * per_share_risk

            risk_amount = equity_now * params["risk_pct"] / 100
            risk_based_shares = int(risk_amount / per_share_risk)
            fill_cost_per_share = entry_price * (1 + (params["slippage_pct"]) / 100)
            cash_based_shares = int(cash / fill_cost_per_share) if fill_cost_per_share > 0 else 0
            shares = min(risk_based_shares, cash_based_shares)
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

        # --- Exits: check every open position (including ones just ------
        # entered today) against today's OHLC ----------------------------
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

    return trades, equity_curve


def compute_metrics(trades: list[dict], equity_curve: list[tuple], initial_equity: float) -> dict:
    if not trades:
        return {
            "trade_count": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
            "expectancy": None, "profit_factor": None, "total_return_pct": None,
            "max_drawdown_pct": None, "ambiguous_count": 0,
        }
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = sum(t["pnl"] for t in trades) / len(trades)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = None  # undefined (no losing trades) rather than a misleading infinity

    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    total_return_pct = (final_equity / initial_equity - 1) * 100 if initial_equity else None

    peak = -float("inf")
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = min(max_dd, (eq - peak) / peak * 100)

    return {
        "trade_count": len(trades),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "ambiguous_count": sum(1 for t in trades if t.get("ambiguous_stop_target")),
    }


def run_full_backtest(
    universe_name: str,
    params: dict,
    dev_start: pd.Timestamp, dev_end: pd.Timestamp,
    oos_start: pd.Timestamp, oos_end: pd.Timestamp,
    progress_callback=None,
) -> dict:
    """Runs the development and out-of-sample windows as two independent
    simulations (each starting fresh from `params["initial_equity"]"),
    so a position opened near the boundary can never blend dev-period
    state into the out-of-sample report.

    Survivorship-bias note: the universe list is today's Nifty 500/All-
    NSE membership, not a historical point-in-time membership snapshot
    (no free reliable source for that exists) -- so a stock that was
    added to or dropped from the index during the backtest window is
    still (or already) treated as a universe member for dates before
    (or after) that actually happened. Disclosed on the page, not fixed.
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
        return {"error": "Nifty 50 index data was unavailable -- the market-trend and relative-strength "
                          "conditions can't be verified, so the backtest can't run."}

    if progress_callback:
        progress_callback(0.3, "Generating historical signals across the full universe history...")
    signals = generate_backtest_signals(history, nifty_df, params)

    if progress_callback:
        progress_callback(0.6, "Simulating the development period...")
    dev_trades, dev_curve = run_backtest(signals, history, params, dev_start, dev_end)

    if progress_callback:
        progress_callback(0.85, "Simulating the out-of-sample period...")
    oos_trades, oos_curve = run_backtest(signals, history, params, oos_start, oos_end)

    if progress_callback:
        progress_callback(1.0, "Done.")

    return {
        "dev_trades": dev_trades, "dev_curve": dev_curve,
        "dev_metrics": compute_metrics(dev_trades, dev_curve, params["initial_equity"]),
        "oos_trades": oos_trades, "oos_curve": oos_curve,
        "oos_metrics": compute_metrics(oos_trades, oos_curve, params["initial_equity"]),
        "universe_label": universe_label, "universe_size": len(tickers), "scanned": len(history),
        "signal_count": len(signals),
    }
