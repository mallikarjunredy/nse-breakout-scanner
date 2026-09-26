"""Streamlit dashboard: "Upside Buy Movement" pre-breakout scanner for the
Nifty 500.

Run with:  streamlit run app.py --server.port 8501
"""

import datetime
import io
import time
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from streamlit_autorefresh import st_autorefresh

from src import (
    breakout_flag, bullish_recovery, config, deep_dive, detail, indicators, market_overview, pre_breakout,
    resistance_breakout, rising_channel, scan_history, scanner, trend_consolidation, triple_ema_golden_cross,
    watchlist,
)

st.set_page_config(page_title="NSE Scanner", page_icon="📈", layout="wide")

IST = ZoneInfo("Asia/Kolkata")
MARKET = "NSE"
CURRENCY = "₹"
MIN_PRICE = config.MIN_PRICE_INR


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def _to_excel_bytes(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


def _export_buttons(df: pd.DataFrame, file_prefix: str, key_prefix: str):
    if df.empty:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    _, corner = st.columns([12, 1])
    with corner:
        with st.popover("⬇️", width="stretch"):
            st.download_button(
                "CSV", _to_csv_bytes(df), file_name=f"{file_prefix}_{stamp}.csv",
                mime="text/csv", key=f"{key_prefix}_csv", width="stretch",
            )
            st.download_button(
                "Excel", _to_excel_bytes({file_prefix[:31]: df}), file_name=f"{file_prefix}_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{key_prefix}_xlsx", width="stretch",
            )


def _format_financial_df(df: pd.DataFrame, is_inr: bool) -> pd.DataFrame:
    if df.empty:
        return df
    divisor = 1e7 if is_inr else 1e9
    unit = "Cr" if is_inr else "B"
    # astype(object) first: assigning formatted strings back into a
    # float64-typed row raises in recent pandas instead of silently
    # upcasting the column dtype.
    formatted = df.astype(object).copy()
    for idx in formatted.index:
        if "EPS" in idx:
            formatted.loc[idx] = formatted.loc[idx].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "N/A")
        else:
            formatted.loc[idx] = formatted.loc[idx].apply(
                lambda v: f"{v/divisor:,.0f} {unit}" if pd.notna(v) else "N/A"
            )
    return formatted


def _fmt_pct(value) -> str:
    return f"{value*100:.2f}%" if value is not None else "N/A"


def _fmt_ratio(value, suffix: str = "") -> str:
    return f"{value:.2f}{suffix}" if value is not None else "N/A"


def _market_status() -> tuple[str, bool]:
    """NSE OPEN/CLOSED based on trading hours only -- does NOT account for
    market holidays (no holiday calendar is wired up).
    """
    now = datetime.datetime.now(IST)
    open_t = now.replace(hour=config.NSE_OPEN_HOUR, minute=config.NSE_OPEN_MINUTE, second=0, microsecond=0)
    close_t = now.replace(hour=config.NSE_CLOSE_HOUR, minute=config.NSE_CLOSE_MINUTE, second=0, microsecond=0)
    is_open = now.weekday() < 5 and open_t <= now <= close_t
    return "NSE", is_open


# Friendly names shown in the ticker tape / chart-symbol search resolve to
# these index tickers; anything else falls back to pre_breakout's NSE-stock
# normalization (adds ".NS"). Keeps the chart pane's "jump to instrument"
# working for indices, which aren't NSE-suffixed tickers.
_CHART_INDEX_ALIASES = {
    "NIFTY 50": "^NSEI", "NIFTY50": "^NSEI", "NIFTY": "^NSEI",
    "BANK NIFTY": "^NSEBANK", "BANKNIFTY": "^NSEBANK", "NIFTY BANK": "^NSEBANK",
    "SENSEX": "^BSESN", "BSE SENSEX": "^BSESN",
}
_CHART_INDEX_LABELS = {"^NSEI": "Nifty 50", "^NSEBANK": "Bank Nifty", "^BSESN": "Sensex"}


def _resolve_chart_symbol(raw: str) -> str:
    key = raw.strip().upper()
    if key in _CHART_INDEX_ALIASES:
        return _CHART_INDEX_ALIASES[key]
    if key.startswith("^"):
        return key
    return pre_breakout.normalize_ticker(raw)


def _chart_symbol_label(ticker: str) -> str:
    return _CHART_INDEX_LABELS.get(ticker, ticker.replace(".NS", ""))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "pre_breakout_cache" not in st.session_state:
    st.session_state.pre_breakout_cache = None  # (timestamp, result) once a scan has run, else None
if "pre_breakout_all_cache" not in st.session_state:
    st.session_state.pre_breakout_all_cache = None  # same, for the "above 100" all-NSE strategy
if "rising_channel_cache" not in st.session_state:
    st.session_state.rising_channel_cache = {}  # keyed by (universe_name, sorted(params.items()))
if "trend_consol_cache" not in st.session_state:
    st.session_state.trend_consol_cache = {}  # keyed by (universe_name, sorted(params.items()))
if "tc_selected_ticker" not in st.session_state:
    st.session_state.tc_selected_ticker = None
if "trend_consol_backtest_result" not in st.session_state:
    st.session_state.trend_consol_backtest_result = None
if "chart_symbol" not in st.session_state:
    st.session_state.chart_symbol = "^NSEI"  # Home's chart pane defaults to Nifty 50, daily
if "rc_selected_ticker" not in st.session_state:
    st.session_state.rc_selected_ticker = None
if "bullish_recovery_cache" not in st.session_state:
    st.session_state.bullish_recovery_cache = {}  # keyed by universe_name -- (timestamp, result)
if "br_selected_ticker" not in st.session_state:
    st.session_state.br_selected_ticker = None
if "resistance_breakout_cache" not in st.session_state:
    st.session_state.resistance_breakout_cache = {}  # keyed by (universe_name, sorted(params.items()))
if "rbo_selected_ticker" not in st.session_state:
    st.session_state.rbo_selected_ticker = None
if "triple_ema_cache" not in st.session_state:
    st.session_state.triple_ema_cache = {}  # keyed by (universe_name, sorted(params.items()))
if "teg_selected_ticker" not in st.session_state:
    st.session_state.teg_selected_ticker = None
if "breakout_flag_cache" not in st.session_state:
    st.session_state.breakout_flag_cache = {}  # keyed by (universe_name, sorted(params.items()))
if "bf_selected_ticker" not in st.session_state:
    st.session_state.bf_selected_ticker = None
if "watchlist_cache" not in st.session_state:
    st.session_state.watchlist_cache = {}
if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "search_result" not in st.session_state:
    st.session_state.search_result = None
if "search_error" not in st.session_state:
    st.session_state.search_error = None
if "nav_page" not in st.session_state:
    st.session_state.nav_page = "Home"

refresh_minutes = config.AUTOREFRESH_INTERVAL_MS // 60000
refresh_label = f"{refresh_minutes // 60} hrs" if refresh_minutes % 60 == 0 else f"{refresh_minutes} min"


# ---------------------------------------------------------------------------
# Global styles
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(160deg, #060b18 0%, #0b132b 45%, #0d1a33 100%);
        background-attachment: fixed;
    }
    section[data-testid="stSidebar"] {
        background: #0a1024;
        border-right: 1px solid rgba(79,209,232,0.12);
    }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        padding: 0.9rem 1rem;
        box-shadow: 0 4px 16px rgba(0,0,0,0.25);
    }
    div[data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 6px 20px rgba(0,0,0,0.3);
    }
    button[data-baseweb="tab"] { border-radius: 10px 10px 0 0; }
    div[data-testid="stExpander"] {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        background: rgba(255,255,255,0.02);
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid rgba(79,209,232,0.22) !important;
        border-radius: 14px !important;
        background: rgba(255,255,255,0.035) !important;
        box-shadow: 0 4px 16px rgba(0,0,0,0.3);
        padding: 0.4rem 0.2rem;
    }
    .card-header {
        display: flex; align-items: center; gap: 0.5rem; font-weight: 700;
        font-size: 0.95rem; color: #EAF2FA; padding: 0.2rem 0.6rem 0.6rem 0.6rem;
        border-bottom: 1px solid rgba(255,255,255,0.08); margin-bottom: 0.5rem;
    }
    .card-header .badge-dot {
        width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    }
    .app-logo-title { font-size: 1.25rem; font-weight: 700; color: #EAF2FA; margin: 0; }
    .app-logo-sub { font-size: 0.72rem; color: #6FE3D6; margin: 0; letter-spacing: 0.02em; }
    .status-badge {
        display: inline-flex; align-items: center; gap: 0.4rem;
        padding: 0.25rem 0.75rem; border-radius: 999px;
        font-size: 0.75rem; font-weight: 700; letter-spacing: 0.03em; border: 1px solid;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .meta-text { color: #B7C6DE; font-size: 0.75rem; text-align: right; line-height: 1.3; }
    .home-hero { padding: 0.7rem 1rem 0.5rem 1rem; margin-bottom: 0.3rem; }
    .home-hero-title { font-size: 1.5rem; font-weight: 800; color: #EAF2FA; line-height: 1.2; }
    .home-hero-sub { font-size: 0.95rem; color: #CFE3F5; margin-top: 0.15rem; }
    .home-hero-tagline { font-size: 0.85rem; color: #6FE3D6; margin-top: 0.3rem; }
    .condition-pill {
        display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.5rem 0.9rem; border-radius: 10px;
        background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12);
        color: #CFE3F5; font-size: 0.82rem; font-weight: 600; white-space: nowrap;
    }
    .nav-tagline {
        margin-top: 1rem; padding: 0.9rem; border-radius: 12px;
        background: linear-gradient(135deg, rgba(79,209,232,0.14), rgba(28,37,65,0.4));
        border: 1px solid rgba(79,209,232,0.25); color: #CFE3F5; font-size: 0.82rem; line-height: 1.4;
    }
    .sector-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.35rem 0; }
    .sector-label { width: 110px; font-size: 0.78rem; color: #CFE3F5; flex-shrink: 0; }
    .sector-bar-track { flex: 1; background: rgba(255,255,255,0.06); border-radius: 6px; height: 10px; overflow: hidden; }
    .sector-bar-fill { background: linear-gradient(90deg, #4FD1E8, #3ECF8E); height: 100%; border-radius: 6px; }
    .sector-count { width: 70px; text-align: right; font-size: 0.78rem; color: #EAF2FA; font-weight: 600; }
    .metric-icon {
        width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center;
        justify-content: center; font-size: 1rem; margin-bottom: 0.5rem;
    }
    .metric-value { font-size: 1.7rem; font-weight: 800; color: #EAF2FA; line-height: 1; }
    .metric-label { font-size: 0.75rem; color: #A9BBD6; margin-top: 0.25rem; }
    .app-footer {
        display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem;
        color: #7C90AC; font-size: 0.75rem; padding: 1rem 0.2rem; border-top: 1px solid rgba(255,255,255,0.06);
        margin-top: 1.5rem;
    }
    .stock-info-card, .why-card {
        background: rgba(255,255,255,0.035); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px; padding: 1.1rem 1.2rem; height: 100%;
    }
    .stock-price-big { font-size: 1.7rem; font-weight: 800; color: #EAF2FA; }
    .stock-change-pos { color: #3ECF8E; font-weight: 700; font-size: 0.95rem; }
    .stock-change-neg { color: #FF6B6B; font-weight: 700; font-size: 0.95rem; }
    .kv-row { display: flex; justify-content: space-between; font-size: 0.85rem; color: #CFE3F5; margin: 0.45rem 0; }
    .kv-row b { color: #EAF2FA; }
    .why-item { font-size: 0.85rem; color: #CFE3F5; margin: 0.4rem 0; }
    .why-item.bad { color: #FF6B6B; }
    .news-item { font-size: 0.8rem; color: #CFE3F5; margin: 0.3rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar: icon navigation
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("Home", "🏠"), ("Upside Buy Movement", "🎯"), ("Upside Buy Movement above 100", "💹"),
    ("Daily Rising Channel", "📐"), ("Daily Trend + Consolidation", "🧭"),
    ("Trend + Consolidation Backtest", "🧪"), ("Bullish Recovery Above EMAs", "🌅"),
    ("Resistance Breakout", "⛰️"), ("Triple EMA Golden Cross", "🥇"), ("Breakout Flag Continuation", "🚩"),
    ("Watchlist", "⭐"), ("Stock Analysis", "📈"), ("Scan History", "🕐"), ("Help & Support", "❓"),
]

with st.sidebar:
    st.markdown("### 📊 NSE Scanner")
    st.caption("Upside Buy Movement. Trade Smarter.")
    st.write("")
    for name, icon in _NAV_ITEMS:
        is_active = st.session_state.nav_page == name
        if st.button(
            f"{icon}  {name}", key=f"nav_{name}", width="stretch",
            type="primary" if is_active else "secondary",
        ):
            st.session_state.nav_page = name
            st.rerun()
    st.markdown(
        '<div class="nav-tagline">📈 Discipline today,<br>better trades tomorrow.</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(
        '<span style="cursor:help; font-size:0.75rem; color:#9FB3D1;" '
        'title="Data via yfinance (Yahoo Finance). Quotes may be delayed ~15-20 minutes. '
        'Scoped to the Nifty 500 universe only.">'
        "ℹ️ Data source</span>",
        unsafe_allow_html=True,
    )

nav_page = st.session_state.nav_page
exch_name, market_is_open = _market_status()
watchlist_tickers = watchlist.load()


@st.cache_data(ttl=300, show_spinner=False)
def _get_ticker_tape_data(wl_tuple: tuple):
    """Cached separately from the strategy scan cache -- the ticker tape
    needs only a handful of quotes and refreshes on its own short TTL
    rather than waiting on config.SCAN_CACHE_TTL_SECONDS.
    """
    indices = market_overview.get_indices_snapshot()
    wl_quotes = market_overview.get_ticker_tape_quotes(list(wl_tuple))
    return indices, wl_quotes


def render_ticker_tape():
    """Top market ticker strip: Nifty 50 / Bank Nifty / Sensex first, then
    the user's own watchlist -- shown below the header on every page. Each
    instrument is a real st.button (not styled-to-look-clickable HTML, a
    known bug class in this app -- see CLAUDE.md) so clicking it directly
    loads that instrument in the chart pane below, matching the original
    "clicking an instrument loads its chart" requirement one-for-one.
    Streamlit can't scroll a row of buttons horizontally the way plain HTML
    can, so a wide watchlist wraps onto additional rows instead of scrolling
    -- the practical-version tradeoff for this control being genuinely
    clickable rather than a static, non-functional strip.
    """
    indices, wl_quotes = _get_ticker_tape_data(tuple(watchlist_tickers))

    instruments = [
        {"chart_symbol": idx["ticker"], "label": idx["name"], "price_str": f'{idx["level"]:,.2f}',
         "change_pct": idx["change_pct"]}
        for idx in indices
    ] + [
        {"chart_symbol": q["ticker"], "label": q["symbol"], "price_str": f'{CURRENCY}{q["price"]:,.2f}',
         "change_pct": q["change_pct"]}
        for q in wl_quotes if q["change_pct"] is not None
    ]

    if not instruments:
        st.caption("Ticker data unavailable right now.")
        return

    st.caption("👆 Click an instrument to load its chart")
    cols_per_row = 6
    for row_start in range(0, len(instruments), cols_per_row):
        row_items = instruments[row_start:row_start + cols_per_row]
        cols = st.columns(len(row_items))
        for col, inst in zip(cols, row_items):
            arrow = "▲" if inst["change_pct"] >= 0 else "▼"
            color = "green" if inst["change_pct"] >= 0 else "red"
            btn_label = (
                f'**{inst["label"]}**  \n{inst["price_str"]}  '
                f':{color}[{arrow} {abs(inst["change_pct"]):.2f}%]'
            )
            is_active = inst["chart_symbol"] == st.session_state.chart_symbol
            with col:
                if st.button(
                    btn_label, key=f"tape_btn_{inst['chart_symbol']}", width="stretch",
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state.chart_symbol = inst["chart_symbol"]
                    st.rerun()


# ---------------------------------------------------------------------------
# Header bar
# ---------------------------------------------------------------------------

status_color = "#3ECF8E" if market_is_open else "#FF6B6B"

h_left, h_mid, h_right = st.columns([2.2, 3, 2.6])
with h_left:
    st.markdown(
        '<p class="app-logo-title">🕉️ Lifeline Trade</p>'
        '<p class="app-logo-sub">NSE Market Terminal · Delayed Data</p>',
        unsafe_allow_html=True,
    )
with h_mid:
    with st.form("search_form", clear_on_submit=False):
        s1, s2 = st.columns([4, 1])
        search_input = s1.text_input(
            "Search stock", placeholder="🔍 Search stock (e.g. RELIANCE, TCS...)",
            label_visibility="collapsed",
        )
        search_submitted = s2.form_submit_button("Search", width="stretch")
with h_right:
    badge_col, watch_btn_col = st.columns([2.4, 1.3])
    with badge_col:
        st.markdown(
            f'<div style="text-align:right;">'
            f'<span class="status-badge" style="color:{status_color}; border-color:{status_color}66; '
            f'background:{status_color}1F;"><span class="dot" style="background:{status_color};"></span>'
            f'Market: {"OPEN" if market_is_open else "CLOSED"} — {exch_name}</span><br>'
            f'<span class="meta-text">Nifty 500 · {time.strftime("%d %b %Y")}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
    with watch_btn_col:
        if st.button(f"⭐ Watchlist ({len(watchlist_tickers)})", key="header_watchlist_btn", width="stretch"):
            st.session_state.nav_page = "Watchlist"
            st.rerun()

# Collapsed to a small info icon instead of an always-visible text banner --
# the disclosure text still has to be reachable on every page (see
# CLAUDE.md's "never claim real-time/official NSE data" rule), just one
# click away via this popover rather than taking up a full line by default.
disclaimer_col, _ = st.columns([0.05, 0.95])
with disclaimer_col:
    with st.popover("📡", help="Data source & market status disclosure"):
        st.caption(
            "**Data Source:** Yahoo Finance — delayed / cached / EOD data, "
            "not an official NSE real-time feed.\n\n"
            "**Market status** reflects trading hours only, not exchange holidays."
        )

render_ticker_tape()

if search_submitted and search_input.strip():
    with st.spinner(f"Looking up {search_input.strip()}..."):
        found = pre_breakout.search_ticker(search_input, min_price=MIN_PRICE)
    if found is None:
        st.session_state.search_result = None
        st.session_state.search_error = f"Couldn't find price data for '{search_input.strip()}'. Check the symbol and try again."
    else:
        st.session_state.search_result = found
        st.session_state.search_error = None
        st.session_state.selected_ticker = found["Ticker"]
        st.session_state.nav_page = "Stock Analysis"
        # Force a clean rerun: `nav_page` was already captured into a local
        # variable above before this point, so without a rerun the page
        # routing would still act on the stale value.
        st.rerun()

if st.session_state.search_error:
    st.error(st.session_state.search_error)


# ---------------------------------------------------------------------------
# Run (or reuse cached) Upside Buy Movement scan + watchlist snapshot
# ---------------------------------------------------------------------------

st_autorefresh(interval=config.AUTOREFRESH_INTERVAL_MS, key="autorefresh_tick")

pb_cache = st.session_state.pre_breakout_cache
cache_fresh = pb_cache is not None and (time.time() - pb_cache[0]) < config.SCAN_CACHE_TTL_SECONDS
manual_refresh = st.session_state.pop("_trigger_scan", False)
need_scan = manual_refresh or not cache_fresh

if need_scan:
    progress_bar = st.progress(0, text="Starting scan...")

    def _on_progress(frac, text_):
        progress_bar.progress(min(frac, 1.0), text=text_)

    with st.spinner("Scanning the Nifty 500 for Upside Buy Movement setups..."):
        result = pre_breakout.scan_pre_breakout(
            min_price=MIN_PRICE, progress_callback=_on_progress, universe_name="nifty500",
        )
    progress_bar.empty()
    st.session_state.pre_breakout_cache = (time.time(), result)
    scan_history.record(result, universe_label=result["universe_label"])
else:
    result = pb_cache[1]

scan_time = st.session_state.pre_breakout_cache[0]
next_refresh_in = max(0, int(config.SCAN_CACHE_TTL_SECONDS - (time.time() - scan_time)))
next_refresh_label = f"{next_refresh_in // 3600}h {(next_refresh_in % 3600) // 60}m"

watchlist_key = tuple(watchlist_tickers)
wl_cache_entry = st.session_state.watchlist_cache.get(watchlist_key)
wl_cache_fresh = wl_cache_entry is not None and (time.time() - wl_cache_entry[0]) < config.SCAN_CACHE_TTL_SECONDS
if need_scan or not wl_cache_fresh:
    with st.spinner("Refreshing watchlist..."):
        watchlist_df = pre_breakout.get_watchlist_data(watchlist_tickers, min_price=MIN_PRICE)
    st.session_state.watchlist_cache[watchlist_key] = (time.time(), watchlist_df)
else:
    watchlist_df = wl_cache_entry[1]

st.sidebar.download_button(
    "📥 Export All (Excel)",
    _to_excel_bytes({
        "Near Resistance": result["near_resistance"],
        "Consolidating": result["consolidating"],
        "Already Broken Out": result["already_broken_out"],
        "Watchlist": watchlist_df,
    }),
    file_name=f"upside_buy_movement_export_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)


# ---------------------------------------------------------------------------
# Shared column configs
# ---------------------------------------------------------------------------

def _visible(df: pd.DataFrame, order: list) -> list:
    return [c for c in order if c in df.columns]


_PRE_BREAKOUT_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Sector", "Current Price", "Resistance Level", "Resistance Window",
    "% Below Resistance", "EMA20", "EMA50", "RSI", "MACD Histogram", "ATR %", "ATR Contracting",
    "5D Avg Volume", "20D Avg Volume", "Volume Ratio (5D/20D)", "Consolidation Days",
    "20D Relative Strength", "Setup Status",
]


def _pre_breakout_columns():
    return {
        "Rank": st.column_config.NumberColumn("Rank", width="small"),
        "Ticker": st.column_config.TextColumn("Symbol", width="small"),
        "Company Name": st.column_config.TextColumn("Company Name"),
        "Sector": st.column_config.TextColumn("Sector", width="small"),
        "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
        "Resistance Level": st.column_config.NumberColumn("Resistance", format="₹%.2f"),
        "Resistance Window": st.column_config.TextColumn("Window", width="small"),
        "% Below Resistance": st.column_config.NumberColumn("% Below Resistance", format="%.2f%%"),
        "EMA20": st.column_config.NumberColumn("EMA20", format="₹%.2f"),
        "EMA50": st.column_config.NumberColumn("EMA50", format="₹%.2f"),
        "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
        "MACD Histogram": st.column_config.NumberColumn("MACD Histogram", format="%.3f"),
        "ATR %": st.column_config.NumberColumn("ATR%", format="%.2f%%"),
        "ATR Contracting": st.column_config.CheckboxColumn("ATR Contraction"),
        "5D Avg Volume": st.column_config.NumberColumn("5D Avg Volume", format="%d"),
        "20D Avg Volume": st.column_config.NumberColumn("20D Avg Volume", format="%d"),
        "Volume Ratio (5D/20D)": st.column_config.NumberColumn("Volume Ratio", format="%.2fx"),
        "Consolidation Days": st.column_config.NumberColumn("Consolidation Days"),
        "20D Relative Strength": st.column_config.NumberColumn("20D Relative Strength", format="%+.2f%%"),
        "Setup Status": st.column_config.TextColumn("Setup Status", width="small"),
    }


def _select_from_pre_breakout_table(df: pd.DataFrame, key: str):
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_pre_breakout_columns(),
        column_order=_visible(df, _PRE_BREAKOUT_COLUMN_ORDER), on_select="rerun", selection_mode="single-row",
        key=key,
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.selected_ticker = df.iloc[rows[0]]["Ticker"]


_WATCHLIST_COLUMN_ORDER = [
    "Ticker", "Company Name", "Sector", "Setup Status", "Current Price", "Resistance Level",
    "% Below Resistance", "RSI", "Volume Ratio (5D/20D)", "20D Relative Strength", "Reason",
]


def _watchlist_columns():
    return {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Company Name": st.column_config.TextColumn("Company Name"),
        "Sector": st.column_config.TextColumn("Sector", width="small"),
        "Setup Status": st.column_config.TextColumn("Status", width="small"),
        "Current Price": st.column_config.NumberColumn("Price", format="₹%.2f"),
        "Resistance Level": st.column_config.NumberColumn("Resistance", format="₹%.2f"),
        "% Below Resistance": st.column_config.NumberColumn("% Below Resistance", format="%.2f%%"),
        "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
        "Volume Ratio (5D/20D)": st.column_config.NumberColumn("Volume Ratio", format="%.2fx"),
        "20D Relative Strength": st.column_config.NumberColumn("20D Rel. Strength", format="%+.2f%%"),
        "Reason": st.column_config.TextColumn("Reason", width="large"),
    }


def _select_from_watchlist_table(df: pd.DataFrame, key: str):
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_watchlist_columns(),
        column_order=_visible(df, _WATCHLIST_COLUMN_ORDER), on_select="rerun", selection_mode="single-row",
        key=key,
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.selected_ticker = df.iloc[rows[0]]["Ticker"]


# ---------------------------------------------------------------------------
# Reusable blocks
# ---------------------------------------------------------------------------

def render_home_header():
    st.markdown(
        '<div class="home-hero">'
        '<div class="home-hero-title">👋 Welcome, Mallikarjun</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if result["scanned"] == 0:
        st.error(
            "This scan couldn't retrieve any price data for the Nifty 500 universe — likely a temporary "
            "Yahoo Finance or network issue. Try ▶️ Run Scan again shortly."
        )
    elif next_refresh_in <= 0:
        st.caption("⏳ This data may be stale — an auto-refresh is due. Use ▶️ Run Scan for the latest.")


def render_market_overview_card():
    with st.container(border=True):
        st.markdown(
            '<div class="card-header"><span class="badge-dot" style="background:#4F7CFF;"></span>'
            "📈 Market Overview</div>",
            unsafe_allow_html=True,
        )
        indices = market_overview.get_indices_snapshot()
        if not indices:
            st.caption("Index data unavailable right now.")
        else:
            cols = st.columns(len(indices))
            for col, idx in zip(cols, indices):
                col.metric(idx["name"], f"{idx['level']:,.2f}", f"{idx['change_pct']:+.2f}%")
        st.caption("Nifty 50, Bank Nifty, and Sensex levels from Yahoo Finance (delayed/EOD).")


def render_watchlist_overview_card():
    with st.container(border=True):
        head_col, link_col = st.columns([2.6, 1.1])
        with head_col:
            st.markdown(
                '<div class="card-header"><span class="badge-dot" style="background:#FFB020;"></span>'
                f"⭐ My Watchlist ({len(watchlist_tickers)})</div>",
                unsafe_allow_html=True,
            )
        with link_col:
            if st.button("View all →", key="wl_view_all", width="stretch"):
                st.session_state.nav_page = "Watchlist"
                st.rerun()
        st.caption("Track your favorite stocks at a glance.")

        if not watchlist_tickers:
            st.info("Your watchlist is empty. Add stocks from any stock's detail panel below.")
            return
        order = ["Ticker", "Company Name", "Setup Status", "Current Price", "% Below Resistance", "RSI"]
        col_config = {
            "Ticker": st.column_config.TextColumn("Ticker", width="small"),
            "Company Name": st.column_config.TextColumn("Name"),
            "Setup Status": st.column_config.TextColumn("Status", width="small"),
            "Current Price": st.column_config.NumberColumn("Price", format="₹%.2f"),
            "% Below Resistance": st.column_config.NumberColumn("% Below Res.", format="%.2f%%"),
            "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
        }
        event = st.dataframe(
            watchlist_df, hide_index=True, width="stretch", column_config=col_config,
            column_order=_visible(watchlist_df, order), on_select="rerun", selection_mode="single-row",
            key="home_watchlist_table",
        )
        rows = event["selection"]["rows"]
        if rows:
            st.session_state.selected_ticker = watchlist_df.iloc[rows[0]]["Ticker"]


def render_fifty_two_week_card():
    with st.container(border=True):
        st.markdown(
            '<div class="card-header"><span class="badge-dot" style="background:#3ECF8E;"></span>'
            "📈 52-Week High / Low</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Nifty 500 stocks whose latest session's High/Low actually reached a new 52-week (252-session) "
            "extreme today -- not just stocks trading near one."
        )
        high_df = result.get("week52_high", pd.DataFrame())
        low_df = result.get("week52_low", pd.DataFrame())

        tab_high, tab_low = st.tabs([f"🚀 New 52W High ({len(high_df)})", f"🔻 New 52W Low ({len(low_df)})"])
        with tab_high:
            if high_df.empty:
                st.info("No stocks matched your saved strategy in this scan.")
            else:
                order = ["Ticker", "Company Name", "Current Price", "52W High", "Change %"]
                col_config = {
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Company Name": st.column_config.TextColumn("Name"),
                    "Current Price": st.column_config.NumberColumn("Price", format="₹%.2f"),
                    "52W High": st.column_config.NumberColumn("52W High", format="₹%.2f"),
                    "Change %": st.column_config.NumberColumn("Chg %", format="%+.2f%%"),
                }
                event = st.dataframe(
                    high_df, hide_index=True, width="stretch", column_config=col_config,
                    column_order=_visible(high_df, order), on_select="rerun", selection_mode="single-row",
                    key="home_week52_high_table",
                )
                rows = event["selection"]["rows"]
                if rows:
                    st.session_state.selected_ticker = high_df.iloc[rows[0]]["Ticker"]
        with tab_low:
            if low_df.empty:
                st.info("No stocks matched your saved strategy in this scan.")
            else:
                order = ["Ticker", "Company Name", "Current Price", "52W Low", "Change %"]
                col_config = {
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Company Name": st.column_config.TextColumn("Name"),
                    "Current Price": st.column_config.NumberColumn("Price", format="₹%.2f"),
                    "52W Low": st.column_config.NumberColumn("52W Low", format="₹%.2f"),
                    "Change %": st.column_config.NumberColumn("Chg %", format="%+.2f%%"),
                }
                event = st.dataframe(
                    low_df, hide_index=True, width="stretch", column_config=col_config,
                    column_order=_visible(low_df, order), on_select="rerun", selection_mode="single-row",
                    key="home_week52_low_table",
                )
                rows = event["selection"]["rows"]
                if rows:
                    st.session_state.selected_ticker = low_df.iloc[rows[0]]["Ticker"]


def render_latest_opportunities_card():
    with st.container(border=True):
        st.markdown(
            '<div class="card-header"><span class="badge-dot" style="background:#4FD1E8;"></span>'
            "📊 Latest Opportunities</div>",
            unsafe_allow_html=True,
        )
        st.caption("Top Upside Buy Movement candidates, closest to resistance first.")

        combined = pd.concat([result["near_resistance"], result["consolidating"]], ignore_index=True)
        if combined.empty:
            st.info("No stocks matched your saved strategy in this scan.")
        else:
            combined = combined.sort_values("% Below Resistance", ascending=True).head(10).copy()
            order = ["Ticker", "Company Name", "Current Price", "% Below Resistance", "RSI",
                      "Volume Ratio (5D/20D)", "Setup Status"]
            col_config = {
                "Ticker": st.column_config.TextColumn("Symbol", width="small"),
                "Company Name": st.column_config.TextColumn("Name"),
                "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
                "% Below Resistance": st.column_config.NumberColumn("% Below Res.", format="%.2f%%"),
                "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
                "Volume Ratio (5D/20D)": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
                "Setup Status": st.column_config.TextColumn("Status", width="small"),
            }
            event = st.dataframe(
                combined, hide_index=True, width="stretch", column_config=col_config,
                column_order=order, on_select="rerun", selection_mode="single-row", key="home_opportunities_table",
            )
            rows = event["selection"]["rows"]
            if rows:
                st.session_state.selected_ticker = combined.iloc[rows[0]]["Ticker"]

        if st.button("View all results →", key="opp_view_all"):
            st.session_state.nav_page = "Upside Buy Movement"
            st.rerun()

        st.divider()
        st.caption("📰 **Indian Market News** (Yahoo Finance, Nifty 50 headlines)")
        with st.spinner("Loading market news..."):
            news_items = deep_dive.get_recent_news(config.PRE_BREAKOUT_BENCHMARK_INDEX, limit=5)
        if not news_items:
            st.caption("No recent market news available right now.")
        else:
            for n in news_items:
                pub = n.get("publisher") or "Unknown source"
                date_str = ""
                if n.get("pub_date"):
                    try:
                        date_str = pd.to_datetime(n["pub_date"]).strftime("%d %b %Y")
                    except Exception:
                        date_str = ""
                label = f"[{n['title']}]({n['link']})" if n.get("link") else n["title"]
                st.markdown(f'<div class="news-item">{label} — <i>{pub} · {date_str}</i></div>', unsafe_allow_html=True)


def render_recent_scan_history_card():
    with st.container(border=True):
        head_col, link_col = st.columns([2.6, 1.1])
        with head_col:
            st.markdown(
                '<div class="card-header"><span class="badge-dot" style="background:#B26BFF;"></span>'
                "🕐 Recent Scans</div>",
                unsafe_allow_html=True,
            )
        with link_col:
            if st.button("View all scans →", key="hist_view_all", width="stretch"):
                st.session_state.nav_page = "Scan History"
                st.rerun()
        st.caption("Your latest scan activity.")

        entries = scan_history.load()
        if not entries:
            st.caption("No scans recorded yet.")
            return
        recent = list(reversed(entries))[:5]
        hist_df = pd.DataFrame(recent)
        hist_df["Status"] = "✅ Completed"
        for col in ("near_resistance", "consolidating"):
            if col not in hist_df.columns:
                hist_df[col] = 0
        hist_df["Matches"] = hist_df["near_resistance"].fillna(0) + hist_df["consolidating"].fillna(0)
        hist_df = hist_df[["date", "market", "Status", "Matches"]]
        hist_df.columns = ["Scan Time", "Universe", "Status", "Matches"]
        st.dataframe(hist_df, hide_index=True, width="stretch")


def render_detail_panel():
    ticker = st.session_state.selected_ticker
    if not ticker:
        st.caption("👆 Click a row in any table above, or search for a ticker, to see detailed stock information here.")
        return

    all_cache = st.session_state.pre_breakout_all_cache
    all_tables = (
        [all_cache[1]["near_resistance"], all_cache[1]["consolidating"], all_cache[1]["already_broken_out"]]
        if all_cache is not None else []
    )
    combined = pd.concat(
        [result["near_resistance"], result["consolidating"], result["already_broken_out"],
         result.get("week52_high", pd.DataFrame()), result.get("week52_low", pd.DataFrame()),
         watchlist_df, *all_tables],
        ignore_index=True,
    )
    match = combined[combined["Ticker"] == ticker]
    if not match.empty:
        tech = match.iloc[0].to_dict()
    elif st.session_state.search_result and st.session_state.search_result.get("Ticker") == ticker:
        tech = st.session_state.search_result
    else:
        tech = None

    with st.spinner(f"Loading details for {ticker}..."):
        info = detail.get_company_info(ticker)
        hist_recent = detail.get_price_history(ticker, period="5d")

    change_pct = None
    price_now = tech["Current Price"] if tech is not None and pd.notna(tech.get("Current Price")) else None
    if len(hist_recent) >= 2:
        prev_close = float(hist_recent["Close"].iloc[-2])
        last_close = price_now if price_now is not None else float(hist_recent["Close"].iloc[-1])
        if prev_close:
            change_pct = (last_close - prev_close) / prev_close * 100

    col_chart, col_info, col_why = st.columns([2.2, 1, 1])

    with col_chart:
        st.markdown(f"### {ticker.replace('.NS', '')} — {info['name']}")
        tab_chart, tab_levels, tab_reason = st.tabs(["📊 Price Chart", "🎯 Key Levels", "✅ Reason for Match"])

        period_options = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y"}

        with tab_chart:
            chosen_label = st.radio(
                "Period", list(period_options.keys()), index=3, horizontal=True,
                key=f"chart_period_{ticker}", label_visibility="collapsed",
            )
            hist = detail.get_price_history(ticker, period=period_options[chosen_label])
            if hist.empty or "Open" not in hist.columns:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                rsi_series = indicators.compute_rsi(hist["Close"])
                fig = make_subplots(
                    rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.05,
                    subplot_titles=(f"{ticker.replace('.NS', '')} — Price", "RSI(14)"),
                )
                fig.add_trace(go.Candlestick(
                    x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
                    increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
                ), row=1, col=1)
                if tech is not None and pd.notna(tech.get("Resistance Level")):
                    fig.add_hline(
                        y=tech["Resistance Level"], line_dash="dash", line_color="#FF6B6B",
                        annotation_text=f"Resistance: {CURRENCY}{tech['Resistance Level']:.2f}",
                        annotation_position="top left", annotation_font_color="#FF6B6B", row=1, col=1,
                    )
                fig.add_trace(go.Scatter(
                    x=rsi_series.index, y=rsi_series, mode="lines", name="RSI(14)",
                    line=dict(color="#4FD1E8", width=1.5),
                ), row=2, col=1)
                fig.add_hline(y=65, line_dash="dot", line_color="#FF6B6B", row=2, col=1)
                fig.add_hline(y=50, line_dash="dot", line_color="#3ECF8E", row=2, col=1)
                fig.update_layout(
                    template="plotly_dark", height=520, margin=dict(l=10, r=10, t=30, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
                    showlegend=False,
                )
                st.plotly_chart(fig, width="stretch")

        with tab_levels:
            if tech is None:
                st.caption("No computed levels available for this ticker.")
            else:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Resistance", f"{CURRENCY}{tech['Resistance Level']:.2f}" if pd.notna(tech.get("Resistance Level")) else "N/A")
                k2.metric("EMA20", f"{CURRENCY}{tech['EMA20']:.2f}" if pd.notna(tech.get("EMA20")) else "N/A")
                k3.metric("EMA50", f"{CURRENCY}{tech['EMA50']:.2f}" if pd.notna(tech.get("EMA50")) else "N/A")
                k4.metric("RSI14", f"{tech['RSI']:.1f}" if pd.notna(tech.get("RSI")) else "N/A")
                k5, k6, k7, k8 = st.columns(4)
                k5.metric("ATR%", f"{tech['ATR %']:.2f}%" if pd.notna(tech.get("ATR %")) else "N/A")
                k6.metric(
                    "Volume Ratio", f"{tech['Volume Ratio (5D/20D)']:.2f}x"
                    if pd.notna(tech.get("Volume Ratio (5D/20D)")) else "N/A",
                )
                k7.metric(
                    "Consolidation Days", f"{int(tech['Consolidation Days'])}"
                    if pd.notna(tech.get("Consolidation Days")) else "N/A",
                )
                k8.metric(
                    "20D Rel. Strength", f"{tech['20D Relative Strength']:+.2f}%"
                    if pd.notna(tech.get("20D Relative Strength")) else "N/A",
                )

        with tab_reason:
            why_text = tech.get("Why Qualified") if tech else None
            reason_text = tech.get("Reason") if tech else None
            if isinstance(why_text, str) and why_text:
                st.success("This stock qualified under the Upside Buy Movement strategy:")
                for line in why_text.split("\n"):
                    st.markdown(line)
            elif isinstance(reason_text, str) and reason_text:
                st.warning(f"❌ Not currently matching — {reason_text}")
            else:
                st.caption("No qualification data available for this ticker.")

    with col_info:
        chg_html = ""
        if change_pct is not None:
            chg_cls = "stock-change-pos" if change_pct >= 0 else "stock-change-neg"
            chg_html = f'<span class="{chg_cls}">{change_pct:+.2f}%</span>'
        price_html = f"{CURRENCY}{price_now:.2f}" if price_now is not None else "N/A"

        info_html = (
            '<div class="stock-info-card">'
            f'<div style="font-weight:800; font-size:1.1rem; color:#EAF2FA;">{ticker.replace(".NS", "")}</div>'
            f'<div style="font-size:0.78rem; color:#8FA3C0; margin-bottom:0.6rem;">{info["name"]}</div>'
            f'<div class="stock-price-big">{price_html}</div>{chg_html}'
            '<div style="margin-top:0.8rem;">'
        )
        if tech is not None and pd.notna(tech.get("Resistance Level")):
            info_html += f'<div class="kv-row"><span>Resistance</span><b>{CURRENCY}{tech["Resistance Level"]:.2f}</b></div>'
        if tech is not None and tech.get("Setup Status"):
            info_html += f'<div class="kv-row"><span>Setup Status</span><b>{tech["Setup Status"]}</b></div>'
        if info.get("52w_low"):
            info_html += f'<div class="kv-row"><span>52W Low</span><b>{CURRENCY}{info["52w_low"]:.2f}</b></div>'
        info_html += "</div></div>"
        st.markdown(info_html, unsafe_allow_html=True)
        st.write("")
        if ticker in watchlist_tickers:
            if st.button("🗑️ Remove Watchlist", key=f"rm_{ticker}", width="stretch"):
                watchlist.remove(ticker)
                st.rerun()
        else:
            if st.button("⭐ Add to Watchlist", key=f"add_{ticker}", width="stretch", type="primary"):
                watchlist.add(ticker)
                st.rerun()

    with col_why:
        why_text = tech.get("Why Qualified") if tech else None
        reason_text = tech.get("Reason") if tech else None
        why_html = '<div class="why-card"><b style="color:#EAF2FA;">Why it qualified?</b><div style="margin-top:0.5rem;">'
        if isinstance(why_text, str) and why_text:
            for line in why_text.split("\n"):
                clean = line.replace("✓ ", "")
                why_html += f'<div class="why-item">✅ {clean}</div>'
        elif isinstance(reason_text, str) and reason_text:
            why_html += f'<div class="why-item bad">❌ {reason_text}</div>'
        else:
            why_html += '<div class="why-item">No qualification data available.</div>'
        why_html += "</div></div>"
        st.markdown(why_html, unsafe_allow_html=True)

    fcols = st.columns(4)

    def _fmt_market_cap(value):
        if not value:
            return "N/A"
        return f"{CURRENCY}{value/1e7:,.0f} Cr"

    fcols[0].metric("Market Cap", _fmt_market_cap(info.get("market_cap")))
    fcols[1].metric("EPS (TTM)", f"{CURRENCY}{info['eps']:.2f}" if info.get("eps") is not None else "N/A")
    fcols[2].metric("Dividend Yield", f"{info['dividend_yield']:.2f}%" if info.get("dividend_yield") else "N/A")
    fcols[3].metric("Beta", f"{info['beta']:.2f}" if info.get("beta") is not None else "N/A")

    if info.get("target_mean_price"):
        st.caption(
            f"Analyst mean target: {CURRENCY}{info['target_mean_price']:.2f} · "
            f"Recommendation: {info.get('recommendation') or 'N/A'}"
        )
    if info.get("summary"):
        with st.expander("Business summary"):
            st.write(info["summary"])
    if info.get("website"):
        st.caption(info["website"])

    with st.expander("📚 Deep Dive — Financials, Ratios, Shareholding, News & Peers"):
        st.caption(
            "Sourced from Yahoo Finance via yfinance. Coverage for Indian stocks is often less complete "
            "than India-specific sources (e.g. Screener.in) — missing fields show as N/A, never guessed."
        )
        dd_tabs = st.tabs(["📊 Ratios", "📑 Financials", "🧾 Shareholding", "📰 News", "🏢 Peers"])

        with dd_tabs[0]:
            with st.spinner("Loading ratios..."):
                ratios = deep_dive.get_extended_ratios(ticker)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("ROE", _fmt_pct(ratios["roe"]))
            r2.metric("ROA", _fmt_pct(ratios["roa"]))
            r3.metric("Debt/Equity", _fmt_ratio(ratios["debt_to_equity"]))
            r4.metric("Book Value", f"{CURRENCY}{ratios['book_value']:.2f}" if ratios["book_value"] else "N/A")
            r5, r6, r7, r8 = st.columns(4)
            r5.metric("Price/Book", _fmt_ratio(ratios["price_to_book"]))
            r6.metric("Gross Margin", _fmt_pct(ratios["gross_margin"]))
            r7.metric("Operating Margin", _fmt_pct(ratios["operating_margin"]))
            r8.metric("Net Margin", _fmt_pct(ratios["profit_margin"]))
            r9, r10, r11, r12 = st.columns(4)
            r9.metric("Revenue Growth (YoY)", _fmt_pct(ratios["revenue_growth"]))
            r10.metric("Earnings Growth (YoY)", _fmt_pct(ratios["earnings_growth"]))
            r11.metric("PEG Ratio", _fmt_ratio(ratios["peg_ratio"]))
            r12.metric("Payout Ratio", _fmt_pct(ratios["payout_ratio"]))

        with dd_tabs[1]:
            period_toggle = st.radio(
                "Period", ["Annual", "Quarterly"], horizontal=True, key=f"fin_period_{ticker}",
            )
            quarterly = period_toggle == "Quarterly"
            with st.spinner("Loading financial statements..."):
                inc = deep_dive.get_income_statement(ticker, quarterly=quarterly)
                bs = deep_dive.get_balance_sheet(ticker, quarterly=quarterly)
                cf = deep_dive.get_cash_flow(ticker, quarterly=quarterly)
            st.markdown("**Income Statement**")
            if not inc.empty:
                st.dataframe(_format_financial_df(inc, True), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            st.markdown("**Balance Sheet**")
            if not bs.empty:
                st.dataframe(_format_financial_df(bs, True), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            st.markdown("**Cash Flow**")
            if not cf.empty:
                st.dataframe(_format_financial_df(cf, True), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            if not (inc.empty and bs.empty and cf.empty):
                st.caption("Figures in Crores (₹ Cr), except EPS.")

        with dd_tabs[2]:
            with st.spinner("Loading shareholding data..."):
                mh = deep_dive.get_major_holders(ticker)
            if mh.empty:
                st.caption("Shareholding data not available for this ticker via Yahoo Finance.")
            else:
                st.dataframe(mh, width="stretch")
                st.caption(
                    "Snapshot only (not a historical trend), and not broken into "
                    "Promoter/FII/DII/Public the way Screener.in shows it."
                )

        with dd_tabs[3]:
            with st.spinner("Loading news..."):
                news_items = deep_dive.get_recent_news(ticker)
            if not news_items:
                st.caption("No recent news available for this ticker.")
            else:
                for n in news_items:
                    pub = n.get("publisher") or "Unknown source"
                    date_str = ""
                    if n.get("pub_date"):
                        try:
                            date_str = pd.to_datetime(n["pub_date"]).strftime("%d %b %Y")
                        except Exception:
                            date_str = ""
                    label = f"[{n['title']}]({n['link']})" if n.get("link") else f"**{n['title']}**"
                    st.markdown(f"{label}  \n<small>{pub} · {date_str}</small>", unsafe_allow_html=True)
                    st.write("")

        with dd_tabs[4]:
            sector_name = info.get("sector")
            if not sector_name:
                st.caption("Sector unknown for this ticker; can't find peers.")
            else:
                combined_all = pd.concat(
                    [result["near_resistance"], result["consolidating"], result["already_broken_out"]],
                    ignore_index=True,
                )
                peers = combined_all[
                    (combined_all.get("Sector") == sector_name) & (combined_all["Ticker"] != ticker)
                ]
                if peers.empty:
                    st.caption(f"No other {sector_name} stocks in today's Nifty 500 scan results.")
                else:
                    peer_cols = [c for c in
                                 ["Ticker", "Company Name", "Current Price", "RSI", "Setup Status"]
                                 if c in peers.columns]
                    st.dataframe(peers[peer_cols], hide_index=True, width="stretch")
                st.caption(
                    f"Peers = other {sector_name} stocks that also appeared in today's scan — "
                    "not a comprehensive industry peer list."
                )


_CHART_INTERVALS = ["5m", "1d", "1wk", "1mo", "1y"]


def _build_chart_figure(df: pd.DataFrame, symbol_label: str, chart_type: str, show_rsi: bool) -> go.Figure:
    """Light/white-themed candlestick (or line) chart with an optional
    synced RSI(14) panel underneath -- deliberately a different palette
    from the rest of the (dark) app, matching the terminal screenshot's
    clean white chart background. No EMA/volume/buy-sell overlays here by
    design (see CLAUDE.md's chart-pane spec) -- those belong to the
    strategy detail-panel chart, not this general-purpose instrument chart.
    """
    if show_rsi:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
    else:
        fig = make_subplots(rows=1, cols=1)

    if chart_type == "Line":
        fig.add_trace(go.Scatter(
            x=df.index, y=df["Close"], mode="lines", name=symbol_label, line=dict(color="#1E88E5", width=1.6),
        ), row=1, col=1)
    else:
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color="#26A69A", decreasing_line_color="#EF5350",
            increasing_fillcolor="#26A69A", decreasing_fillcolor="#EF5350", name=symbol_label,
        ), row=1, col=1)

    if show_rsi:
        rsi_series = indicators.compute_rsi(df["Close"])
        fig.add_hrect(y0=30, y1=70, fillcolor="#8E24AA", opacity=0.07, line_width=0, row=2, col=1)
        fig.add_trace(go.Scatter(
            x=rsi_series.index, y=rsi_series, mode="lines", name="RSI(14)", line=dict(color="#8E24AA", width=1.4),
        ), row=2, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="#B0B0B0", row=2, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="#B0B0B0", row=2, col=1)
        fig.update_yaxes(title_text="RSI(14)", range=[0, 100], row=2, col=1)

    fig.update_layout(
        template="plotly_white", height=560 if show_rsi else 460,
        margin=dict(l=10, r=55, t=10, b=10), paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        xaxis_rangeslider_visible=False, showlegend=False, font=dict(color="#1A1A1A"),
        dragmode="pan",
    )
    fig.update_xaxes(gridcolor="#EDEDED", showgrid=True)
    fig.update_yaxes(gridcolor="#EDEDED", showgrid=True, side="right", row=1, col=1)
    return fig


@st.dialog("Full Screen Chart", width="large")
def _fullscreen_chart_dialog(fig: go.Figure):
    fig_full = go.Figure(fig)
    fig_full.update_layout(height=760)
    st.plotly_chart(fig_full, width="stretch", config={"displayModeBar": True}, key="chart_fullscreen_plot")
    if st.button("Close", key="chart_fullscreen_close", type="primary"):
        st.rerun()


def render_chart_pane():
    """Right-hand chart pane: right-aligned toolbar (Symbol -> Timeframe ->
    Chart type -> Indicators -> Full Screen), an OHLC/change header line,
    then the candlestick+RSI figure. Drawing tools (trend line, rectangle,
    freehand, erase) come from Plotly's own built-in modebar -- this app's
    "practical version" scope deliberately excludes Fibonacci/parallel-
    channel/lock-hide/undo-redo/per-instrument-persisted drawings and a
    true drag-to-resize divider (a slider stands in for that on Home).
    """
    st.markdown(
        '<div class="card-header"><span class="badge-dot" style="background:#1E88E5;"></span>'
        "📈 Chart</div>", unsafe_allow_html=True,
    )

    tb1, tb2, tb3, tb4, tb5 = st.columns([2.4, 1.2, 1.0, 1.1, 1.1])
    with tb1:
        # Deliberately NOT pre-filled from st.session_state.chart_symbol: a
        # text_input's `value=` argument is only honored the first time a
        # keyed widget is created -- on every later rerun Streamlit keeps
        # whatever the box last held instead, so binding `value=` to
        # chart_symbol here would silently re-diff against that stale text
        # and stomp any programmatic change (e.g. a ticker-tape button
        # click) right back to whatever this box last showed. This search
        # box is intentionally a one-way "type something new to jump"
        # control instead; the currently loaded symbol is shown in the
        # price header below the toolbar, not echoed back into this field.
        symbol_input = st.text_input(
            "Symbol", placeholder="🔍 Search symbol (e.g. RELIANCE, NIFTY 50)...",
            label_visibility="collapsed", key="chart_symbol_search_input",
        )
        typed = symbol_input.strip()
        if typed and typed.upper() != st.session_state.get("_last_chart_symbol_search", ""):
            st.session_state["_last_chart_symbol_search"] = typed.upper()
            resolved = _resolve_chart_symbol(typed)
            if resolved != st.session_state.chart_symbol:
                st.session_state.chart_symbol = resolved
                st.rerun()
    with tb2:
        interval = st.selectbox(
            "Timeframe", _CHART_INTERVALS, index=1, label_visibility="collapsed",
            format_func=lambda i: detail.INTERVAL_LABELS[i], key="chart_interval",
        )
    with tb3:
        range_options = detail.RANGE_OPTIONS_BY_INTERVAL[interval]
        default_range = "1Y" if "1Y" in range_options else range_options[-1]
        range_key = st.selectbox(
            "Range", range_options, index=range_options.index(default_range),
            label_visibility="collapsed", key=f"chart_range_{interval}",
        )
    with tb4:
        chart_type = st.selectbox(
            "Chart type", ["Candlestick", "Line"], label_visibility="collapsed", key="chart_type",
        )
    with tb5:
        with st.popover("📊 Indicators", width="stretch"):
            show_rsi = st.checkbox("RSI (14)", value=True, key="chart_show_rsi")
            st.caption("Only RSI(14) is available on this chart -- no EMA/volume overlays by design.")

    df, note = detail.get_chart_data(st.session_state.chart_symbol, interval, range_key)
    symbol_label = _chart_symbol_label(st.session_state.chart_symbol)

    if df.empty:
        st.warning(note or "No price data available for this instrument/interval/range.")
        return

    last = df.iloc[-1]
    prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
    chg = (float(last["Close"]) - prev_close) if prev_close else None
    chg_pct = (chg / prev_close * 100) if prev_close else None
    if chg is not None:
        chg_class = "stock-change-pos" if chg >= 0 else "stock-change-neg"
        chg_html = f'<span class="{chg_class}">{chg:+.2f} ({chg_pct:+.2f}%)</span>'
    else:
        chg_html = '<span class="meta-text">N/A</span>'
    st.markdown(
        f'<div style="display:flex; align-items:baseline; gap:0.6rem; flex-wrap:wrap;">'
        f'<span style="font-size:1.05rem; font-weight:700; color:#EAF2FA;">{symbol_label}</span>'
        f'<span class="stock-price-big" style="font-size:1.25rem;">{CURRENCY}{float(last["Close"]):,.2f}</span>'
        f"{chg_html}"
        f'<span class="meta-text">O {float(last["Open"]):.2f} · H {float(last["High"]):.2f} · '
        f'L {float(last["Low"]):.2f} · {detail.INTERVAL_LABELS[interval]}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if note:
        st.caption(f"ℹ️ {note}")

    fig = _build_chart_figure(df, symbol_label, chart_type, show_rsi)
    st.plotly_chart(
        fig, width="stretch",
        config={
            "displayModeBar": True, "scrollZoom": True,
            "modeBarButtonsToAdd": ["drawline", "drawrect", "drawopenpath", "eraseshape"],
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
        key=f"chart_plot_{st.session_state.chart_symbol}_{interval}_{range_key}_{chart_type}_{show_rsi}",
    )
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        f"Prices as of {df.index[-1].strftime('%d %b %Y')}"
        f'{" " + df.index[-1].strftime("%I:%M %p") + " IST" if interval == "5m" else ""}.'
    )
    if st.button("⛶ Full Screen", key="chart_fullscreen_btn", width="stretch"):
        _fullscreen_chart_dialog(fig)


def render_footer(scan_ts: float | None = None):
    scan_label_full = datetime.datetime.fromtimestamp(scan_ts or scan_time, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
    st.markdown(
        f'<div class="app-footer">'
        f'<span>Data source: Yahoo Finance (cached, EOD/delayed) · For educational purposes only. '
        f"Not investment advice.</span>"
        f'<span>Last updated: {scan_label_full}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page: Home
# ---------------------------------------------------------------------------

if nav_page == "Home":
    render_home_header()
    st.write("")

    # Default to the #1 candidate so the analysis section below (and the
    # detail panel) is never empty -- the user can still pick any other row.
    if not st.session_state.selected_ticker:
        if not result["near_resistance"].empty:
            st.session_state.selected_ticker = result["near_resistance"].iloc[0]["Ticker"]
        elif not result["consolidating"].empty:
            st.session_state.selected_ticker = result["consolidating"].iloc[0]["Ticker"]

    # Terminal-style layout: dashboard on the left, live candlestick+RSI
    # chart on the right. True pixel drag-to-resize isn't available in
    # pure Streamlit, so this slider is the practical substitute -- moving
    # it re-splits the two st.columns on the next rerun. On narrow screens
    # Streamlit stacks columns automatically, so nothing is hidden there.
    split_pct = st.slider(
        "↔️ Dashboard / Chart width", min_value=30, max_value=70, value=50, step=5,
        key="home_split_pct", help="Resize the dashboard vs. chart panels below.",
    )
    dash_col, chart_col = st.columns([split_pct, 100 - split_pct])

    with dash_col:
        render_market_overview_card()
        render_watchlist_overview_card()
        render_fifty_two_week_card()
        render_latest_opportunities_card()
        render_recent_scan_history_card()

    with chart_col:
        with st.container(border=True):
            render_chart_pane()

    st.divider()
    st.markdown("### 🔎 Selected Stock Analysis")
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Upside Buy Movement (full scan results)
# ---------------------------------------------------------------------------

elif nav_page == "Upside Buy Movement":
    st.markdown("### 🎯 Upside Buy Movement — Pre-Breakout Watchlist")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Scans the Nifty 500 universe (closing price > ₹100) for stocks that have NOT yet broken out but "
        "are consolidating tightly just under a resistance level, on contracting volatility and volume -- "
        "a possible pre-breakout setup, not a prediction that any stock will break out on the next session."
    )

    if st.button("▶️ Run Upside Buy Movement Scan", type="primary", key="run_pre_breakout"):
        st.session_state["_trigger_scan"] = True
        st.rerun()

    scan_label = datetime.datetime.fromtimestamp(scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    st.caption(
        f"Last scanned: {scan_label} · Universe: Nifty 500 ({result['universe_size']} instruments) · "
        f"Scanned OK: {result['scanned']}"
    )
    if result.get("benchmark_missing"):
        st.warning(
            "Nifty 50 index data was unavailable during this scan -- the relative-strength condition "
            "couldn't be checked, so results may be incomplete. Try running the scan again."
        )
    if next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Upside Buy Movement Scan for the latest.")

    near_df = result["near_resistance"]
    consolidating_df = result["consolidating"]
    broken_df = result["already_broken_out"]

    tab_near, tab_consol, tab_broken = st.tabs([
        f"🎯 Near Resistance ({len(near_df)})",
        f"📦 Consolidating ({len(consolidating_df)})",
        f"🚀 Already Broken Out ({len(broken_df)})",
    ])
    with tab_near:
        st.caption("Within 0-2% of resistance -- the most imminent-looking setups.")
        if near_df.empty:
            st.info("No stocks matched your saved strategy in this scan.")
        else:
            _export_buttons(near_df, "upside_near_resistance", "ubm_near")
            _select_from_pre_breakout_table(near_df, key="ubm_near_table")
    with tab_consol:
        st.caption("Within 2-5% of resistance, still building the base.")
        if consolidating_df.empty:
            st.info("No stocks matched your saved strategy in this scan.")
        else:
            _export_buttons(consolidating_df, "upside_consolidating", "ubm_consolidating")
            _select_from_pre_breakout_table(consolidating_df, key="ubm_consolidating_table")
    with tab_broken:
        st.caption(
            "Same quality conditions (EMA structure, RSI, trend), but price is already above resistance -- "
            "too late for a pre-breakout entry on these; shown for reference only."
        )
        if broken_df.empty:
            st.info("No stocks with a similar setup have already broken out in this scan.")
        else:
            _export_buttons(broken_df, "upside_already_broken_out", "ubm_broken")
            _select_from_pre_breakout_table(broken_df, key="ubm_broken_table")

    st.caption(
        "Every condition (EMA structure, trend, RSI, MACD, volume contraction, ATR contraction, range "
        "contraction, consolidation length, relative strength) is a fixed rule from this strategy's own "
        "definition, not a claim about what will happen next. Not investment advice."
    )
    st.divider()
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Upside Buy Movement above 100 (same rules, all-NSE universe)
# ---------------------------------------------------------------------------

elif nav_page == "Upside Buy Movement above 100":
    st.markdown("### 💹 Upside Buy Movement above 100 — All-NSE Pre-Breakout Watchlist")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Same Upside Buy Movement rules as the Nifty 500 page, but scanned across all NSE stocks "
        "(closing price > ₹100) rather than just the Nifty 500 -- a possible pre-breakout setup, not a "
        "prediction that any stock will break out on the next session."
    )

    all_cache = st.session_state.pre_breakout_all_cache
    all_cache_fresh = all_cache is not None and (time.time() - all_cache[0]) < config.SCAN_CACHE_TTL_SECONDS
    run_all_clicked = st.button("▶️ Run Upside Buy Movement above 100 Scan", type="primary", key="run_pre_breakout_all")

    if run_all_clicked or all_cache is None:
        all_progress = st.progress(0, text="Starting scan...")

        def _on_all_progress(frac, text_):
            all_progress.progress(min(frac, 1.0), text=text_)

        with st.spinner("Scanning all NSE stocks for Upside Buy Movement setups..."):
            result_all = pre_breakout.scan_pre_breakout(
                min_price=MIN_PRICE, progress_callback=_on_all_progress, universe_name="all_nse",
            )
        all_progress.empty()
        st.session_state.pre_breakout_all_cache = (time.time(), result_all)
        scan_history.record(result_all, universe_label=result_all["universe_label"])
    else:
        result_all = all_cache[1]

    scan_time_all = st.session_state.pre_breakout_all_cache[0]
    next_refresh_in_all = max(0, int(config.SCAN_CACHE_TTL_SECONDS - (time.time() - scan_time_all)))
    scan_label_all = datetime.datetime.fromtimestamp(scan_time_all, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    st.caption(
        f"Last scanned: {scan_label_all} · Universe: All NSE Stocks ({result_all['universe_size']} instruments) · "
        f"Scanned OK: {result_all['scanned']}"
    )
    if result_all.get("benchmark_missing"):
        st.warning(
            "Nifty 50 index data was unavailable during this scan -- the relative-strength condition "
            "couldn't be checked, so results may be incomplete. Try running the scan again."
        )
    if next_refresh_in_all <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Upside Buy Movement above 100 Scan for the latest.")

    near_df_all = result_all["near_resistance"]
    consolidating_df_all = result_all["consolidating"]
    broken_df_all = result_all["already_broken_out"]

    tab_near_a, tab_consol_a, tab_broken_a = st.tabs([
        f"🎯 Near Resistance ({len(near_df_all)})",
        f"📦 Consolidating ({len(consolidating_df_all)})",
        f"🚀 Already Broken Out ({len(broken_df_all)})",
    ])
    with tab_near_a:
        st.caption("Within 0-2% of resistance -- the most imminent-looking setups.")
        if near_df_all.empty:
            st.info("No stocks matched your saved strategy in this scan.")
        else:
            _export_buttons(near_df_all, "upside100_near_resistance", "ubm100_near")
            _select_from_pre_breakout_table(near_df_all, key="ubm100_near_table")
    with tab_consol_a:
        st.caption("Within 2-5% of resistance, still building the base.")
        if consolidating_df_all.empty:
            st.info("No stocks matched your saved strategy in this scan.")
        else:
            _export_buttons(consolidating_df_all, "upside100_consolidating", "ubm100_consolidating")
            _select_from_pre_breakout_table(consolidating_df_all, key="ubm100_consolidating_table")
    with tab_broken_a:
        st.caption(
            "Same quality conditions (EMA structure, RSI, trend), but price is already above resistance -- "
            "too late for a pre-breakout entry on these; shown for reference only."
        )
        if broken_df_all.empty:
            st.info("No stocks with a similar setup have already broken out in this scan.")
        else:
            _export_buttons(broken_df_all, "upside100_already_broken_out", "ubm100_broken")
            _select_from_pre_breakout_table(broken_df_all, key="ubm100_broken_table")

    st.caption(
        "Every condition (EMA structure, trend, RSI, MACD, volume contraction, ATR contraction, range "
        "contraction, consolidation length, relative strength) is a fixed rule from this strategy's own "
        "definition, not a claim about what will happen next. Not investment advice."
    )
    st.divider()
    render_detail_panel()
    render_footer(scan_ts=scan_time_all)


# ---------------------------------------------------------------------------
# Page: Daily Rising Channel (Pre-Breakout & Breakout Scanner)
# ---------------------------------------------------------------------------

_RC_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Support Level", "% Below Resistance", "RSI", "Volume Ratio",
    "Channel Age", "Resistance Touches", "Support Touches",
]
_RC_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Setup Status": st.column_config.TextColumn("Status", width="small"),
    "Signal Date": st.column_config.TextColumn("Signal Date", width="small"),
    "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "Resistance Level": st.column_config.NumberColumn("Resistance", format="₹%.2f"),
    "Support Level": st.column_config.NumberColumn("Support", format="₹%.2f"),
    "% Below Resistance": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
    "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
    "Volume Ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
    "Channel Age": st.column_config.NumberColumn("Channel Age (sessions)"),
    "Resistance Touches": st.column_config.NumberColumn("R Touches"),
    "Support Touches": st.column_config.NumberColumn("S Touches"),
}


def _render_rc_tab(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("No stocks matched your saved strategy in this scan.")
        return
    _export_buttons(df, key_prefix, key_prefix)
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_RC_COLUMN_CONFIG,
        column_order=[c for c in _RC_COLUMN_ORDER if c in df.columns],
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_table",
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.rc_selected_ticker = df.iloc[rows[0]]["Ticker"]


if nav_page == "Daily Rising Channel":
    st.markdown("### 📐 Daily Rising Channel — Pre-Breakout & Breakout Scanner")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Uses split/dividend-adjusted daily closes (yfinance's standard back-adjustment: prices before a "
        "split/dividend are scaled so the ex-date shows no artificial gap) and only completed daily "
        "candles -- a breakout is never confirmed on an unfinished session. Fits an upward-sloping price "
        "channel and classifies each candidate as approaching its resistance (Pre-Breakout Watchlist) or "
        "already closing above it (Confirmed Breakout / Breakout — Volume Unconfirmed, depending on "
        "whether volume backed the move). Rule-based scanner matches, not guaranteed profitable "
        "recommendations -- proximity to resistance does not guarantee a breakout."
    )

    rc_universe_options = ["Nifty 500", "All NSE Stocks (₹50+)"]
    rc_universe_label_choice = st.radio(
        "Universe", rc_universe_options, horizontal=True, key="rc_universe",
    )
    rc_universe_name = "nifty500" if rc_universe_label_choice == "Nifty 500" else "all_nse"
    st.caption(f"✅ Mandatory: closing price > ₹{config.RISING_CHANNEL_MIN_PRICE_INR:.0f}.")

    with st.expander("⚙️ Adjust Thresholds"):
        c1, c2, c3 = st.columns(3)
        rc_touch_tol = c1.slider(
            "Touch Tolerance (× ATR14)", 0.1, 2.0, config.RISING_CHANNEL_TOUCH_TOLERANCE_ATR_MULT, 0.1,
            key="rc_touch_tol", help="How close a swing point must be to the fitted line to count as a touch.",
        )
        rc_parallel_tol = c2.slider(
            "Parallelism Tolerance (%)", 5.0, 60.0, config.RISING_CHANNEL_PARALLEL_TOLERANCE_PCT, 5.0,
            key="rc_parallel_tol", help="Max allowed relative difference between the resistance and support slopes.",
        )
        rc_containment = c3.slider(
            "Min. Containment (%)", 50.0, 100.0, config.RISING_CHANNEL_MIN_CONTAINMENT_PCT, 5.0,
            key="rc_containment", help="% of closes across the fitted window that must sit within the channel band.",
        )
        c4, c5, c6 = st.columns(3)
        rc_lookback = c4.slider(
            "Lookback Window (sessions)", 20, 150,
            (config.RISING_CHANNEL_LOOKBACK_MIN, config.RISING_CHANNEL_LOOKBACK_MAX), 10, key="rc_lookback",
        )
        rc_rsi = c5.slider(
            "Pre-Breakout RSI Range", 0, 100,
            (int(config.RISING_CHANNEL_PREBREAKOUT_RSI_MIN), int(config.RISING_CHANNEL_PREBREAKOUT_RSI_MAX)),
            key="rc_rsi",
        )
        rc_distance = c6.slider(
            "Distance to Resistance (%)", 0.0, 15.0,
            (config.RISING_CHANNEL_PREBREAKOUT_DISTANCE_MIN_PCT, config.RISING_CHANNEL_PREBREAKOUT_DISTANCE_MAX_PCT),
            0.5, key="rc_distance",
        )
        c7, c8 = st.columns(2)
        rc_vol_mult = c7.slider(
            "Breakout Volume Multiplier", 1.0, 4.0, config.RISING_CHANNEL_BREAKOUT_VOLUME_MULT, 0.1,
            key="rc_vol_mult",
        )
        rc_min_touches = c8.slider(
            "Min. Touches per Boundary", 2, 6, config.RISING_CHANNEL_MIN_TOUCHES, 1, key="rc_min_touches",
        )
        c9, c10 = st.columns(2)
        rc_use_range_filter = c9.checkbox(
            "Optional: require range contraction (10D range < preceding 30D range)",
            value=False, key="rc_range_filter",
        )
        rc_use_volume_filter = c10.checkbox(
            "Optional: require volume contraction (5D avg volume < preceding 60D avg)",
            value=False, key="rc_volume_filter",
        )

    rc_params = rising_channel.default_params()
    rc_params.update({
        "touch_tolerance_atr_mult": rc_touch_tol,
        "parallel_tolerance_pct": rc_parallel_tol,
        "min_containment_pct": rc_containment,
        "lookback_min": rc_lookback[0],
        "lookback_max": rc_lookback[1],
        "prebreakout_rsi_min": float(rc_rsi[0]),
        "prebreakout_rsi_max": float(rc_rsi[1]),
        "prebreakout_distance_min_pct": rc_distance[0],
        "prebreakout_distance_max_pct": rc_distance[1],
        "breakout_volume_mult": rc_vol_mult,
        "min_touches": rc_min_touches,
        "use_range_contraction_filter": rc_use_range_filter,
        "use_volume_contraction_filter": rc_use_volume_filter,
    })
    rc_cache_key = (rc_universe_name, tuple(sorted(rc_params.items())))

    rc_cache = st.session_state.rising_channel_cache.get(rc_cache_key)
    run_rc_clicked = st.button("▶️ Run Rising Channel Scan", type="primary", key="run_rising_channel")

    if run_rc_clicked or rc_cache is None:
        rc_progress = st.progress(0, text="Starting scan...")

        def _on_rc_progress(frac, text_):
            rc_progress.progress(min(frac, 1.0), text=text_)

        with st.spinner(f"Scanning {rc_universe_label_choice} for Daily Rising Channel setups..."):
            result_rc = rising_channel.scan_rising_channel(
                universe_name=rc_universe_name, min_price=config.RISING_CHANNEL_MIN_PRICE_INR,
                params=rc_params, progress_callback=_on_rc_progress,
            )
        rc_progress.empty()
        st.session_state.rising_channel_cache[rc_cache_key] = (time.time(), result_rc)
    else:
        result_rc = rc_cache[1]

    rc_scan_time = st.session_state.rising_channel_cache[rc_cache_key][0]
    rc_scan_label = datetime.datetime.fromtimestamp(rc_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    rc_asof = result_rc.get("data_asof_date")
    rc_asof_label = rc_asof.strftime("%d %b %Y") if rc_asof else "N/A"
    st.caption(
        f"Last scanned: {rc_scan_label} · Latest completed candle: {rc_asof_label} · "
        f"Universe: {result_rc['universe_label']} ({result_rc['universe_size']} instruments) · "
        f"Scanned OK: {result_rc['scanned']}"
    )
    rc_next_refresh_in = config.SCAN_CACHE_TTL_SECONDS - (time.time() - rc_scan_time)
    if rc_next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Rising Channel Scan for the latest.")

    rc_flags = []
    if result_rc["insufficient_history_count"]:
        rc_flags.append(
            f"{result_rc['insufficient_history_count']} candidate(s) have under "
            f"{config.RISING_CHANNEL_MIN_HISTORY_SESSIONS} sessions of history"
        )
    if result_rc["missing_volume_count"]:
        rc_flags.append(f"{result_rc['missing_volume_count']} candidate(s) have missing/zero recent volume")
    if result_rc["stale_data_count"]:
        rc_flags.append(f"{result_rc['stale_data_count']} candidate(s) have stale (>5 day old) data")
    if rc_flags:
        st.warning("⚠️ " + "; ".join(rc_flags) + ".")

    rc_sort_options = {
        "Distance to Resistance": ("% Below Resistance", True),
        "Volume Ratio": ("Volume Ratio", False),
        "Channel Quality (Resistance Touches)": ("Resistance Touches", False),
    }
    rc_sort_label = st.selectbox("Sort results by", list(rc_sort_options.keys()), key="rc_sort")
    rc_sort_col, rc_sort_default_asc = rc_sort_options[rc_sort_label]
    rc_sort_asc = st.checkbox("Ascending", value=rc_sort_default_asc, key="rc_sort_asc")

    def _rc_sorted(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or rc_sort_col not in df.columns:
            return df
        return df.sort_values(rc_sort_col, ascending=rc_sort_asc, na_position="last")

    pre_df_rc = _rc_sorted(result_rc["pre_breakout"])
    confirmed_df_rc = _rc_sorted(result_rc["confirmed_breakout"])
    unconfirmed_df_rc = _rc_sorted(result_rc["volume_unconfirmed"])

    tab_pre_rc, tab_confirmed_rc, tab_unconfirmed_rc = st.tabs([
        f"👀 Pre-Breakout Watchlist ({len(pre_df_rc)})",
        f"🚀 Confirmed Breakouts ({len(confirmed_df_rc)})",
        f"⚠️ Breakout — Volume Unconfirmed ({len(unconfirmed_df_rc)})",
    ])
    with tab_pre_rc:
        st.caption("Inside a valid rising channel, within the configured distance below resistance. Not a guaranteed breakout.")
        _render_rc_tab(pre_df_rc, "rc_pre")
    with tab_confirmed_rc:
        st.caption("Closed above the channel's projected resistance, on the required volume.")
        _render_rc_tab(confirmed_df_rc, "rc_confirmed")
    with tab_unconfirmed_rc:
        st.caption("Same price breakout, but volume didn't confirm it -- treat with extra caution.")
        _render_rc_tab(unconfirmed_df_rc, "rc_unconfirmed")

    st.divider()
    st.markdown("#### 📊 Selected Candidate Chart")
    rc_ticker = st.session_state.rc_selected_ticker
    if not rc_ticker:
        st.caption("👆 Click a row in any table above to see its channel chart here.")
    else:
        rc_channel = result_rc["channels"].get(rc_ticker)
        rc_signal_idx = result_rc["signal_idx"].get(rc_ticker)
        if rc_channel is None or rc_signal_idx is None:
            st.caption(f"No channel data for {rc_ticker} under the current scan -- select a row above again.")
        else:
            with st.spinner(f"Loading chart for {rc_ticker}..."):
                rc_chart_history = scanner.download_history([rc_ticker], auto_adjust=True)
            rc_chart_df = rc_chart_history.get(rc_ticker)
            if rc_chart_df is None:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                rc_fig = rising_channel.build_channel_chart(rc_chart_df, rc_channel, rc_signal_idx, rc_ticker)
                st.plotly_chart(rc_fig, width="stretch")
                st.caption(
                    "Blue solid = resistance, blue dotted = support (both sloped, fit only from data "
                    "available at signal time -- historical signals don't change when later data arrives). "
                    "Red ▽ = swing high, green △ = swing low used to fit the lines. Orange marker = the "
                    "pre-breakout/breakout signal candle."
                )

    st.caption(
        "Rule-based scanner matches, not guaranteed profitable recommendations. A wick above resistance "
        "with a close below it is never treated as a breakout. Not investment advice."
    )
    render_footer(scan_ts=rc_scan_time)


# ---------------------------------------------------------------------------
# Page: Daily Trend + Consolidation Breakout
# ---------------------------------------------------------------------------

_TC_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance", "Support", "% Below Resistance", "Consolidation Width %", "Volume Ratio",
    "SMA50", "SMA200", "Stock 63D Return %", "Benchmark 63D Return %",
]
_TC_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Setup Status": st.column_config.TextColumn("Status", width="small"),
    "Signal Date": st.column_config.TextColumn("Signal Date", width="small"),
    "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "Resistance": st.column_config.NumberColumn("Resistance", format="₹%.2f"),
    "Support": st.column_config.NumberColumn("Support", format="₹%.2f"),
    "% Below Resistance": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
    "Consolidation Width %": st.column_config.NumberColumn("Width %", format="%.2f%%"),
    "Volume Ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
    "SMA50": st.column_config.NumberColumn("SMA50", format="₹%.2f"),
    "SMA200": st.column_config.NumberColumn("SMA200", format="₹%.2f"),
    "Stock 63D Return %": st.column_config.NumberColumn("Stock 63D Ret.", format="%+.2f%%"),
    "Benchmark 63D Return %": st.column_config.NumberColumn("Nifty 63D Ret.", format="%+.2f%%"),
}


def _render_tc_tab(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("No stocks matched your saved strategy in this scan.")
        return
    _export_buttons(df, key_prefix, key_prefix)
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_TC_COLUMN_CONFIG,
        column_order=[c for c in _TC_COLUMN_ORDER if c in df.columns],
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_table",
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.tc_selected_ticker = df.iloc[rows[0]]["Ticker"]


if nav_page == "Daily Trend + Consolidation":
    st.markdown("### 🧭 Daily Trend + Consolidation Breakout")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Uses split/dividend-adjusted daily closes and only completed daily candles. Requires the stock "
        "AND Nifty 50 to both be in an uptrend, a tight (≤8% by default) 15-session consolidation, and "
        "either an approach to (Pre-Breakout Watchlist) or a volume-backed close above (Confirmed "
        "Breakout) that consolidation's resistance. A wick above resistance without a qualifying close is "
        "never treated as a breakout. Rule-based scanner matches, not guaranteed profitable trades."
    )

    tc_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All NSE Stocks (₹100+)"], horizontal=True, key="tc_universe",
    )
    tc_universe_name = "nifty500" if tc_universe_choice == "Nifty 500" else "all_nse"

    with st.expander("⚙️ Adjust Thresholds"):
        c1, c2, c3 = st.columns(3)
        tc_min_price = c1.number_input(
            "Minimum Close Price (₹)", min_value=0.0, value=config.TREND_CONSOL_MIN_PRICE_INR, step=10.0,
            key="tc_min_price", help="Stocks closing at or below this price are excluded entirely.",
        )
        tc_min_traded_value_cr = c2.number_input(
            "Min. Avg. Traded Value (₹ Crore, 20-session avg.)", min_value=0.0,
            value=config.TREND_CONSOL_MIN_TRADED_VALUE_INR / 1_00_00_000, step=1.0, key="tc_min_traded_value",
            help="Liquidity filter: average of (Close × Volume) over the preceding 20 sessions must exceed this.",
        )
        tc_max_width = c3.slider(
            "Max. Consolidation Width (%)", 1.0, 20.0, config.TREND_CONSOL_MAX_WIDTH_PCT, 0.5,
            key="tc_max_width", help="(Resistance − Support) / Support over the consolidation window, as a %.",
        )
        c4, c5, c6 = st.columns(3)
        tc_consolidation_period = c4.slider(
            "Consolidation Period (sessions)", 5, 40, config.TREND_CONSOL_CONSOLIDATION_PERIOD, 1,
            key="tc_consolidation_period", help="How many prior sessions (excluding today) set the resistance/support.",
        )
        tc_distance = c5.slider(
            "Pre-Breakout Distance to Resistance (%)", 0.0, 15.0,
            (config.TREND_CONSOL_PREBREAKOUT_DISTANCE_MIN_PCT, config.TREND_CONSOL_PREBREAKOUT_DISTANCE_MAX_PCT),
            0.5, key="tc_distance", help="How close (below resistance) counts as a Pre-Breakout Watchlist candidate.",
        )
        tc_volume_mult = c6.slider(
            "Breakout Volume Multiplier", 1.0, 4.0, config.TREND_CONSOL_VOLUME_MULTIPLIER, 0.1,
            key="tc_volume_mult", help="Signal-day volume must be at least this many times the prior 20-session average.",
        )
        c7, c8, c9 = st.columns(3)
        tc_breakout_buffer = c7.slider(
            "Breakout Buffer (%)", 0.0, 5.0, config.TREND_CONSOL_BREAKOUT_BUFFER_PCT, 0.1,
            key="tc_breakout_buffer", help="Close must clear resistance by at least this % to count as a breakout.",
        )
        tc_sma_fast = c8.number_input(
            "Fast Moving Average (sessions)", min_value=5, max_value=100, value=config.TREND_CONSOL_SMA_FAST,
            key="tc_sma_fast",
        )
        tc_sma_slow = c9.number_input(
            "Slow Moving Average (sessions)", min_value=50, max_value=300, value=config.TREND_CONSOL_SMA_SLOW,
            key="tc_sma_slow",
        )
        tc_rs_days = st.slider(
            "Relative-Strength Lookback (sessions)", 20, 120, config.TREND_CONSOL_RELATIVE_STRENGTH_DAYS, 1,
            key="tc_rs_days", help="The stock's return over this many sessions must beat Nifty 50's return over the same period.",
        )

    tc_params = trend_consolidation.default_params()
    tc_params.update({
        "min_price": tc_min_price,
        "min_traded_value": tc_min_traded_value_cr * 1_00_00_000,
        "max_width_pct": tc_max_width,
        "consolidation_period": tc_consolidation_period,
        "prebreakout_distance_min_pct": tc_distance[0],
        "prebreakout_distance_max_pct": tc_distance[1],
        "volume_multiplier": tc_volume_mult,
        "breakout_buffer_pct": tc_breakout_buffer,
        "sma_fast": tc_sma_fast,
        "sma_slow": tc_sma_slow,
        "relative_strength_days": tc_rs_days,
    })
    tc_cache_key = (tc_universe_name, tuple(sorted(tc_params.items())))

    tc_cache = st.session_state.trend_consol_cache.get(tc_cache_key)
    tc_run_clicked = st.button("▶️ Run Trend + Consolidation Scan", type="primary", key="run_trend_consol")

    if tc_run_clicked or tc_cache is None:
        tc_progress = st.progress(0, text="Starting scan...")

        def _on_tc_progress(frac, text_):
            tc_progress.progress(min(frac, 1.0), text=text_)

        with st.spinner(f"Scanning {tc_universe_choice} for Trend + Consolidation setups..."):
            result_tc = trend_consolidation.scan_trend_consolidation(
                universe_name=tc_universe_name, params=tc_params, progress_callback=_on_tc_progress,
            )
        tc_progress.empty()
        st.session_state.trend_consol_cache[tc_cache_key] = (time.time(), result_tc)
    else:
        result_tc = tc_cache[1]

    tc_scan_time = st.session_state.trend_consol_cache[tc_cache_key][0]
    tc_scan_label = datetime.datetime.fromtimestamp(tc_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    tc_asof = result_tc.get("data_asof_date")
    tc_asof_label = tc_asof.strftime("%d %b %Y") if tc_asof else "N/A"
    st.caption(
        f"Last scanned: {tc_scan_label} · Latest completed candle: {tc_asof_label} · "
        f"Universe: {result_tc['universe_label']} ({result_tc['universe_size']} instruments) · "
        f"Scanned OK: {result_tc['scanned']}"
    )
    if not result_tc.get("benchmark_available"):
        st.warning(
            "⚠️ Nifty 50 index data was unavailable during this scan -- the market-trend and "
            "relative-strength conditions couldn't be verified, so they were NOT silently passed. "
            "No candidates are shown this run. Try running the scan again."
        )
    tc_next_refresh_in = config.SCAN_CACHE_TTL_SECONDS - (time.time() - tc_scan_time)
    if tc_next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Trend + Consolidation Scan for the latest.")

    tc_flags = []
    if result_tc["insufficient_history_count"]:
        tc_flags.append(
            f"{result_tc['insufficient_history_count']} candidate(s) have under "
            f"{config.TREND_CONSOL_MIN_HISTORY_SESSIONS} sessions of history"
        )
    if result_tc["missing_volume_count"]:
        tc_flags.append(f"{result_tc['missing_volume_count']} candidate(s) have missing/zero recent volume")
    if result_tc["stale_data_count"]:
        tc_flags.append(f"{result_tc['stale_data_count']} candidate(s) have stale (>5 day old) data")
    if tc_flags:
        st.warning("⚠️ " + "; ".join(tc_flags) + ".")

    tc_sort_options = {
        "Distance to Resistance": ("% Below Resistance", True),
        "Volume Ratio": ("Volume Ratio", False),
        "Consolidation Width": ("Consolidation Width %", True),
    }
    tc_sort_label = st.selectbox("Sort results by", list(tc_sort_options.keys()), key="tc_sort")
    tc_sort_col, tc_sort_default_asc = tc_sort_options[tc_sort_label]
    tc_sort_asc = st.checkbox("Ascending", value=tc_sort_default_asc, key="tc_sort_asc")

    def _tc_sorted(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or tc_sort_col not in df.columns:
            return df
        return df.sort_values(tc_sort_col, ascending=tc_sort_asc, na_position="last")

    pre_df_tc = _tc_sorted(result_tc["pre_breakout"])
    confirmed_df_tc = _tc_sorted(result_tc["confirmed_breakout"])
    unconfirmed_df_tc = _tc_sorted(result_tc["volume_unconfirmed"])

    tab_pre_tc, tab_confirmed_tc, tab_unconfirmed_tc = st.tabs([
        f"👀 Pre-Breakout Watchlist ({len(pre_df_tc)})",
        f"🚀 Confirmed Breakouts ({len(confirmed_df_tc)})",
        f"⚠️ Volume Unconfirmed ({len(unconfirmed_df_tc)})",
    ])
    with tab_pre_tc:
        st.caption("Approaching resistance -- this means approaching, not a guaranteed future breakout.")
        _render_tc_tab(pre_df_tc, "tc_pre")
    with tab_confirmed_tc:
        st.caption("Closed above resistance with the consolidation, trend, and volume conditions all confirmed.")
        _render_tc_tab(confirmed_df_tc, "tc_confirmed")
    with tab_unconfirmed_tc:
        st.caption("Same qualifying price breakout, but volume didn't confirm it -- treat with extra caution.")
        _render_tc_tab(unconfirmed_df_tc, "tc_unconfirmed")

    st.divider()
    st.markdown("#### 📊 Selected Candidate Chart")
    tc_ticker = st.session_state.tc_selected_ticker
    if not tc_ticker:
        st.caption("👆 Click a row in any table above to see its chart here.")
    else:
        tc_signal_idx = result_tc["signal_idx"].get(tc_ticker)
        tc_resistance = result_tc["resistance_by_ticker"].get(tc_ticker)
        tc_support = result_tc["support_by_ticker"].get(tc_ticker)
        if tc_signal_idx is None:
            st.caption(f"No chart data for {tc_ticker} under the current scan -- select a row above again.")
        else:
            with st.spinner(f"Loading chart for {tc_ticker}..."):
                tc_chart_history = scanner.download_history([tc_ticker], auto_adjust=True)
            tc_chart_df = tc_chart_history.get(tc_ticker)
            if tc_chart_df is None:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                tc_fig = trend_consolidation.build_trend_chart(
                    tc_chart_df, tc_ticker, tc_signal_idx, tc_resistance, tc_support, tc_params,
                )
                st.plotly_chart(tc_fig, width="stretch")
                st.caption(
                    "Red = resistance, green dotted = support, both frozen exactly as computed for this "
                    "signal (shaded band = the consolidation window) -- these never change when later "
                    "data arrives, even if today's own setup for this stock looks different."
                )

    st.caption(
        "These settings are a starting hypothesis, not a proven profitable strategy. Not investment "
        "advice. See 🧪 Trend + Consolidation Backtest to paper-test this strategy's historical rules."
    )
    render_footer(scan_ts=tc_scan_time)


# ---------------------------------------------------------------------------
# Page: Trend + Consolidation Backtest (paper trading)
# ---------------------------------------------------------------------------

elif nav_page == "Trend + Consolidation Backtest":
    st.markdown("### 🧪 Daily Trend + Consolidation — Paper-Trading Backtest")
    st.warning(
        "⚠️ These settings are a starting hypothesis, not a proven profitable strategy. This does NOT "
        "claim any success rate or guaranteed returns, and no number on this page is a probability of "
        "profit. Uses today's Nifty 500 / All-NSE membership applied to past dates (no free historical "
        "point-in-time membership data exists), which introduces survivorship bias -- stocks removed "
        "from the index during the backtest window are still included for dates before their removal, "
        "and vice versa. Not investment advice; for research only."
    )
    st.caption(
        "Walks each stock's full history day by day: a Confirmed Breakout signal enters at the *next* "
        "session's open, is sized to risk a fixed % of equity, and exits at a stop, a target, or after a "
        "maximum holding period -- whichever comes first. Brokerage, transaction charges, and slippage "
        "are all modeled. The development and out-of-sample periods run as two independent simulations "
        "so neither can leak state into the other."
    )

    tcb_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All NSE Stocks (₹100+)"], horizontal=True, key="tcb_universe",
    )
    tcb_universe_name = "nifty500" if tcb_universe_choice == "Nifty 500" else "all_nse"

    today = datetime.date.today()
    default_oos_start = today - datetime.timedelta(days=180)
    default_dev_end = default_oos_start - datetime.timedelta(days=1)
    default_dev_start = default_dev_end - datetime.timedelta(days=365 * 3)

    st.markdown("##### Development Period")
    dcol1, dcol2 = st.columns(2)
    tcb_dev_start = dcol1.date_input("Start", value=default_dev_start, key="tcb_dev_start")
    tcb_dev_end = dcol2.date_input("End", value=default_dev_end, key="tcb_dev_end")
    st.markdown("##### Out-of-Sample Period (untouched during development)")
    ocol1, ocol2 = st.columns(2)
    tcb_oos_start = ocol1.date_input("Start", value=default_oos_start, key="tcb_oos_start")
    tcb_oos_end = ocol2.date_input("End", value=today, key="tcb_oos_end")

    with st.expander("⚙️ Strategy & Trading Settings"):
        st.caption("Strategy thresholds (same meaning as the live scanner page):")
        c1, c2, c3 = st.columns(3)
        tcb_max_width = c1.slider("Max. Consolidation Width (%)", 1.0, 20.0, config.TREND_CONSOL_MAX_WIDTH_PCT, 0.5, key="tcb_max_width")
        tcb_consolidation_period = c2.slider("Consolidation Period (sessions)", 5, 40, config.TREND_CONSOL_CONSOLIDATION_PERIOD, 1, key="tcb_consolidation_period")
        tcb_volume_mult = c3.slider("Breakout Volume Multiplier", 1.0, 4.0, config.TREND_CONSOL_VOLUME_MULTIPLIER, 0.1, key="tcb_volume_mult")
        c4, c5 = st.columns(2)
        tcb_breakout_buffer = c4.slider("Breakout Buffer (%)", 0.0, 5.0, config.TREND_CONSOL_BREAKOUT_BUFFER_PCT, 0.1, key="tcb_breakout_buffer")
        tcb_min_price = c5.number_input("Minimum Close Price (₹)", min_value=0.0, value=config.TREND_CONSOL_MIN_PRICE_INR, step=10.0, key="tcb_min_price")

        st.caption("Paper-trading mechanics:")
        c6, c7, c8 = st.columns(3)
        tcb_initial_equity = c6.number_input(
            "Initial Paper Capital (₹)", min_value=10000.0, value=config.TREND_CONSOL_BACKTEST_INITIAL_EQUITY_INR,
            step=100000.0, key="tcb_initial_equity",
        )
        tcb_risk_pct = c7.slider(
            "Risk per Trade (% of equity)", 0.1, 5.0, config.TREND_CONSOL_BACKTEST_RISK_PCT, 0.1,
            key="tcb_risk_pct", help="Position size is chosen so a full stop-out loses about this % of current equity.",
        )
        tcb_stop_atr_mult = c8.slider(
            "Stop Distance (× ATR14)", 0.5, 5.0, config.TREND_CONSOL_BACKTEST_STOP_ATR_MULT, 0.25, key="tcb_stop_atr_mult",
        )
        c9, c10, c11 = st.columns(3)
        tcb_target_rr = c9.slider(
            "Profit Target (× initial risk)", 0.5, 5.0, config.TREND_CONSOL_BACKTEST_TARGET_RR_MULT, 0.25, key="tcb_target_rr",
        )
        tcb_max_holding = c10.slider(
            "Max Holding (sessions)", 1, 60, config.TREND_CONSOL_BACKTEST_MAX_HOLDING_SESSIONS, 1, key="tcb_max_holding",
        )
        tcb_entry_gap_max = c11.slider(
            "Skip Entry if Gap Above Signal Close (%)", 0.5, 10.0, config.TREND_CONSOL_BACKTEST_ENTRY_GAP_MAX_PCT, 0.5,
            key="tcb_entry_gap_max",
        )
        c12, c13, c14 = st.columns(3)
        tcb_brokerage = c12.number_input("Brokerage (% per side)", min_value=0.0, value=config.TREND_CONSOL_BACKTEST_BROKERAGE_PCT, step=0.01, key="tcb_brokerage")
        tcb_transaction = c13.number_input("Transaction Charges (% per side)", min_value=0.0, value=config.TREND_CONSOL_BACKTEST_TRANSACTION_CHARGES_PCT, step=0.01, key="tcb_transaction")
        tcb_slippage = c14.number_input("Slippage (% per side)", min_value=0.0, value=config.TREND_CONSOL_BACKTEST_SLIPPAGE_PCT, step=0.01, key="tcb_slippage")

    tcb_params = trend_consolidation.default_params()
    tcb_params.update({
        "min_price": tcb_min_price,
        "max_width_pct": tcb_max_width,
        "consolidation_period": tcb_consolidation_period,
        "volume_multiplier": tcb_volume_mult,
        "breakout_buffer_pct": tcb_breakout_buffer,
        "initial_equity": tcb_initial_equity,
        "risk_pct": tcb_risk_pct,
        "stop_atr_mult": tcb_stop_atr_mult,
        "target_rr_mult": tcb_target_rr,
        "max_holding_sessions": tcb_max_holding,
        "entry_gap_max_pct": tcb_entry_gap_max,
        "brokerage_pct": tcb_brokerage,
        "transaction_charges_pct": tcb_transaction,
        "slippage_pct": tcb_slippage,
    })

    if st.button("▶️ Run Backtest", type="primary", key="run_tc_backtest"):
        if tcb_dev_start >= tcb_dev_end or tcb_oos_start >= tcb_oos_end:
            st.error("Each period's start date must be before its end date.")
        else:
            tcb_progress = st.progress(0, text="Starting backtest...")

            def _on_tcb_progress(frac, text_):
                tcb_progress.progress(min(frac, 1.0), text=text_)

            with st.spinner(f"Backtesting {tcb_universe_choice}... this walks the full history of every stock and can take a couple of minutes."):
                backtest_result = trend_consolidation.run_full_backtest(
                    universe_name=tcb_universe_name, params=tcb_params,
                    dev_start=pd.Timestamp(tcb_dev_start), dev_end=pd.Timestamp(tcb_dev_end),
                    oos_start=pd.Timestamp(tcb_oos_start), oos_end=pd.Timestamp(tcb_oos_end),
                    progress_callback=_on_tcb_progress,
                )
            tcb_progress.empty()
            st.session_state.trend_consol_backtest_result = backtest_result

    tcb_result = st.session_state.trend_consol_backtest_result
    if tcb_result is None:
        st.caption("👆 Set your dates and settings, then click ▶️ Run Backtest.")
    elif tcb_result.get("error"):
        st.error(tcb_result["error"])
    else:
        st.caption(
            f"Universe: {tcb_result['universe_label']} ({tcb_result['universe_size']} instruments, "
            f"{tcb_result['scanned']} scanned OK) · {tcb_result['signal_count']} historical Confirmed "
            f"Breakout signals found across the full available history."
        )

        def _render_period_results(label: str, metrics: dict, trades: list, curve: list, key_prefix: str):
            st.markdown(f"##### {label}")
            if metrics["trade_count"] == 0:
                st.info("No trades in this period with these settings.")
                return
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Trades", metrics["trade_count"])
            m2.metric("Win Rate", f"{metrics['win_rate']:.1f}%" if metrics["win_rate"] is not None else "N/A")
            m3.metric("Expectancy / Trade", f"₹{metrics['expectancy']:,.0f}" if metrics["expectancy"] is not None else "N/A")
            m4.metric(
                "Profit Factor",
                f"{metrics['profit_factor']:.2f}" if metrics["profit_factor"] is not None else "N/A (no losses)",
            )
            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Avg Win", f"₹{metrics['avg_win']:,.0f}" if metrics["avg_win"] is not None else "N/A")
            m6.metric("Avg Loss", f"₹{metrics['avg_loss']:,.0f}" if metrics["avg_loss"] is not None else "N/A")
            m7.metric("Portfolio Return", f"{metrics['total_return_pct']:+.2f}%" if metrics["total_return_pct"] is not None else "N/A")
            m8.metric("Max Drawdown", f"{metrics['max_drawdown_pct']:.2f}%" if metrics["max_drawdown_pct"] is not None else "N/A")
            if metrics["ambiguous_count"]:
                st.caption(
                    f"⚠️ {metrics['ambiguous_count']} trade(s) had both stop and target touched on the same "
                    "day with no way to know the true order -- resolved conservatively as a stop-out."
                )

            if curve:
                curve_df = pd.DataFrame(curve, columns=["Date", "Equity"])
                fig = go.Figure(data=[go.Scatter(x=curve_df["Date"], y=curve_df["Equity"], mode="lines",
                                                  line=dict(color="#4FD1E8", width=2))])
                fig.update_layout(
                    template="plotly_dark", height=280, margin=dict(l=10, r=10, t=20, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    yaxis_title="Equity (₹)",
                )
                st.plotly_chart(fig, width="stretch")

            trades_df = pd.DataFrame(trades)
            if not trades_df.empty:
                trades_df = trades_df[["ticker", "entry_date", "exit_date", "entry_price", "exit_price",
                                        "shares", "pnl", "pnl_pct", "exit_reason", "ambiguous_stop_target",
                                        "holding_sessions"]]
                _export_buttons(trades_df, f"{key_prefix}_trades", key_prefix)
                st.dataframe(trades_df, hide_index=True, width="stretch")

        _render_period_results(
            "📈 Development Period", tcb_result["dev_metrics"], tcb_result["dev_trades"], tcb_result["dev_curve"], "tcb_dev",
        )
        st.divider()
        _render_period_results(
            "🔒 Out-of-Sample Period (untouched)", tcb_result["oos_metrics"], tcb_result["oos_trades"],
            tcb_result["oos_curve"], "tcb_oos",
        )

    st.divider()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Bullish Recovery Above EMAs
# ---------------------------------------------------------------------------

_BR_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Latest Close", "Change %", "EMA10", "EMA20",
    "Previous Open", "Previous Close", "RSI", "Market Cap (Cr)", "Candle Date",
]
_BR_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Latest Close": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "Change %": st.column_config.NumberColumn("Chg %", format="%+.2f%%"),
    "EMA10": st.column_config.NumberColumn("EMA10", format="₹%.2f"),
    "EMA20": st.column_config.NumberColumn("EMA20", format="₹%.2f"),
    "Previous Open": st.column_config.NumberColumn("Prev Open", format="₹%.2f"),
    "Previous Close": st.column_config.NumberColumn("Prev Close", format="₹%.2f"),
    "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
    "Market Cap (Cr)": st.column_config.NumberColumn("Mkt Cap (₹ Cr)", format="%.0f"),
    "Candle Date": st.column_config.TextColumn("Candle Date", width="small"),
}


if nav_page == "Bullish Recovery Above EMAs":
    st.markdown("### 🌅 Bullish Recovery Above EMAs")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Every completed daily candle where the close has recovered back above both EMA(10) and EMA(20) "
        "after a down/flat previous session, on strong (RSI ≥ 65) momentum, with a market cap of at least "
        "₹2,000 crore -- a fixed rule set, not a prediction. This is NOT a confirmed bullish engulfing "
        "pattern: today's open is not required to sit below yesterday's close."
    )

    br_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All Stocks"], horizontal=True, key="br_universe_choice",
    )
    br_universe_name = "nifty500" if br_universe_choice == "Nifty 500" else "all_nse"

    br_cache_entry = st.session_state.bullish_recovery_cache.get(br_universe_name)
    br_cache_fresh = (
        br_cache_entry is not None and (time.time() - br_cache_entry[0]) < config.SCAN_CACHE_TTL_SECONDS
    )
    br_run_clicked = st.button("▶️ Run Bullish Recovery Scan", type="primary", key="run_bullish_recovery")

    if br_run_clicked or not br_cache_fresh:
        br_progress = st.progress(0, text="Starting scan...")

        def _on_br_progress(frac, text_):
            br_progress.progress(min(frac, 1.0), text=text_)

        try:
            with st.spinner(f"Scanning {br_universe_choice} for Bullish Recovery Above EMAs setups..."):
                br_result = bullish_recovery.scan_bullish_recovery(
                    progress_callback=_on_br_progress, universe_name=br_universe_name,
                )
            br_progress.empty()
            st.session_state.bullish_recovery_cache[br_universe_name] = (time.time(), br_result)
        except Exception as exc:
            br_progress.empty()
            st.error(f"Scan failed: {exc}. This is usually a temporary Yahoo Finance or network issue -- try again.")
            br_result = None
    else:
        br_result = br_cache_entry[1]

    if br_result is None:
        st.stop()

    br_scan_time = st.session_state.bullish_recovery_cache[br_universe_name][0]
    br_scan_label = datetime.datetime.fromtimestamp(br_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    br_asof = br_result.get("data_asof_date")
    br_asof_label = br_asof.strftime("%d %b %Y") if br_asof else "N/A"
    st.caption(
        f"Last scanned: {br_scan_label} · Prices as of {br_asof_label} close · "
        f"Universe: {br_universe_choice} ({br_result['universe_size']} instruments) · "
        f"Scanned OK: {br_result['scanned']} · Skipped (missing data): {br_result['skipped_no_data']} · "
        f"Skipped (missing market cap): {br_result['skipped_missing_market_cap']} · "
        f"Price floor applied: {CURRENCY}{br_result['min_price']:.0f}"
    )
    if br_result["scanned"] == 0:
        st.error(
            "This scan couldn't retrieve any price data for this universe -- likely a temporary Yahoo "
            "Finance or network issue. Try ▶️ Run Bullish Recovery Scan again shortly."
        )

    br_matches = br_result["matches"]
    st.markdown(f"#### Matches ({len(br_matches)})")
    if br_matches.empty:
        st.info("No stocks matched the Bullish Recovery Above EMAs rules in this scan.")
    else:
        _export_buttons(br_matches, "bullish_recovery", "br_matches")
        br_event = st.dataframe(
            br_matches, hide_index=True, width="stretch", column_config=_BR_COLUMN_CONFIG,
            column_order=[c for c in _BR_COLUMN_ORDER if c in br_matches.columns],
            on_select="rerun", selection_mode="single-row", key="br_matches_table",
        )
        br_rows = br_event["selection"]["rows"]
        if br_rows:
            st.session_state.br_selected_ticker = br_matches.iloc[br_rows[0]]["Ticker"]

    st.divider()
    st.markdown("#### 📊 Selected Candidate: Chart + Condition Checklist")
    br_ticker = st.session_state.br_selected_ticker
    if not br_ticker or br_matches.empty or br_ticker not in set(br_matches["Ticker"]):
        st.caption("👆 Click a row in the table above to see its chart and condition checklist here.")
    else:
        br_row = br_matches[br_matches["Ticker"] == br_ticker].iloc[0]
        br_chart_col, br_checklist_col = st.columns([1.6, 1])
        with br_chart_col:
            with st.spinner(f"Loading chart for {br_ticker}..."):
                br_hist = detail.get_price_history(br_ticker, period="1y")
            if br_hist.empty:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                br_rsi_series = indicators.compute_rsi(br_hist["Close"])
                br_fig = make_subplots(
                    rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.05,
                    subplot_titles=(f"{br_ticker.replace('.NS', '')} — Price", "RSI(14)"),
                )
                br_fig.add_trace(go.Candlestick(
                    x=br_hist.index, open=br_hist["Open"], high=br_hist["High"], low=br_hist["Low"],
                    close=br_hist["Close"], increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B",
                    name=br_ticker,
                ), row=1, col=1)
                br_fig.add_hline(
                    y=float(br_row["EMA10"]), line_dash="dot", line_color="#4FD1E8",
                    annotation_text="EMA10", annotation_position="top left",
                    annotation_font_color="#4FD1E8", row=1, col=1,
                )
                br_fig.add_hline(
                    y=float(br_row["EMA20"]), line_dash="dot", line_color="#B26BFF",
                    annotation_text="EMA20", annotation_position="top left",
                    annotation_font_color="#B26BFF", row=1, col=1,
                )
                br_fig.add_trace(go.Scatter(
                    x=br_rsi_series.index, y=br_rsi_series, mode="lines", name="RSI(14)",
                    line=dict(color="#4FD1E8", width=1.5),
                ), row=2, col=1)
                br_fig.add_hline(y=65, line_dash="dot", line_color="#3ECF8E", row=2, col=1)
                br_fig.add_hline(y=30, line_dash="dot", line_color="#FF6B6B", row=2, col=1)
                br_fig.update_layout(
                    template="plotly_dark", height=480, margin=dict(l=10, r=10, t=30, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_rangeslider_visible=False, showlegend=False,
                )
                st.plotly_chart(br_fig, width="stretch")
        with br_checklist_col:
            st.markdown("**Condition checklist**")
            br_checklist_rows = [
                ("Close ≥ EMA(10)", f'{br_row["Latest Close"]:.2f} ≥ {br_row["EMA10"]:.2f}',
                 br_row["Latest Close"] >= br_row["EMA10"]),
                ("Close ≥ EMA(20)", f'{br_row["Latest Close"]:.2f} ≥ {br_row["EMA20"]:.2f}',
                 br_row["Latest Close"] >= br_row["EMA20"]),
                ("Previous close ≤ previous open", f'{br_row["Previous Close"]:.2f} ≤ {br_row["Previous Open"]:.2f}',
                 br_row["Previous Close"] <= br_row["Previous Open"]),
                ("Close ≥ previous open", f'{br_row["Latest Close"]:.2f} ≥ {br_row["Previous Open"]:.2f}',
                 br_row["Latest Close"] >= br_row["Previous Open"]),
                ("Market cap ≥ ₹2,000 Cr", f'{CURRENCY}{br_row["Market Cap (Cr)"]:,.0f} Cr',
                 br_row["Market Cap (Cr)"] >= config.BULLISH_RECOVERY_MIN_MARKET_CAP_CR),
                ("RSI(14) ≥ 65", f'{br_row["RSI"]:.1f}', br_row["RSI"] >= config.BULLISH_RECOVERY_RSI_MIN),
                (f'Close > {CURRENCY}{br_result["min_price"]:.0f}', f'{br_row["Latest Close"]:.2f}',
                 br_row["Latest Close"] > br_result["min_price"]),
            ]
            for br_label, br_value, br_passed in br_checklist_rows:
                br_icon = "✅" if br_passed else "❌"
                st.markdown(
                    f'<div class="why-item{"" if br_passed else " bad"}">{br_icon} <b>{br_label}</b> — {br_value}</div>',
                    unsafe_allow_html=True,
                )

    st.caption(
        "Every condition (EMA10/EMA20 recovery, prior-day structure, RSI, market cap, price floor) is a "
        "fixed rule from this strategy's own definition. Not investment advice, and not a guarantee that "
        "any of these stocks will keep moving in this direction."
    )
    render_footer(scan_ts=br_scan_time)


# ---------------------------------------------------------------------------
# Page: Resistance Breakout ("Previous Swing High Breakout with Volume
# Confirmation")
# ---------------------------------------------------------------------------

_RBO_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Buy Level", "EMA10", "EMA20", "RSI", "Resistance Date", "Pullback Low", "Pullback %",
    "Momentum %", "% Below Resistance", "Volume Ratio",
]
_RBO_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Setup Status": st.column_config.TextColumn("Status", width="small"),
    "Signal Date": st.column_config.TextColumn("Signal Date", width="small"),
    "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "Resistance Level": st.column_config.NumberColumn("Previous High", format="₹%.2f"),
    "Buy Level": st.column_config.NumberColumn("Buy Level", format="₹%.2f"),
    "EMA10": st.column_config.NumberColumn("EMA10", format="₹%.2f"),
    "EMA20": st.column_config.NumberColumn("EMA20", format="₹%.2f"),
    "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
    "Resistance Date": st.column_config.TextColumn("High Date", width="small"),
    "Pullback Low": st.column_config.NumberColumn("Pullback Low", format="₹%.2f"),
    "Pullback %": st.column_config.NumberColumn("Pullback %", format="%.1f%%"),
    "Momentum %": st.column_config.NumberColumn("Momentum %", format="%+.1f%%"),
    "% Below Resistance": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
    "Volume Ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
}


def _render_rbo_tab(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("No stocks matched your saved strategy in this scan.")
        return
    _export_buttons(df, key_prefix, key_prefix)
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_RBO_COLUMN_CONFIG,
        column_order=[c for c in _RBO_COLUMN_ORDER if c in df.columns],
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_table",
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.rbo_selected_ticker = df.iloc[rows[0]]["Ticker"]


if nav_page == "Resistance Breakout":
    st.markdown("### ⛰️ Resistance Breakout — Previous Swing High Breakout with Volume Confirmation")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "A stock previously reached a price it could not move beyond (a confirmed swing high -- its "
        "resistance), fell into a genuine pullback and base, recovered back toward that old high, and now "
        "either approaches it (Pre-Breakout Watchlist) or has closed above it on visibly higher volume "
        "(Confirmed Breakout / Breakout — Volume Unconfirmed). This is NOT a confirmed cup-and-handle "
        "pattern -- that requires a distinct handle before the breakout, which isn't checked for here; the "
        "recovery leg may simply look cup-shaped. A minimum-momentum gate excludes stocks just sitting "
        "flat/consolidating near the previous high with no real forward movement behind them. Each match "
        "shows a **Buy Level** (resistance + the breakout buffer -- the exact price a close needs to clear "
        "to confirm the breakout), also drawn as a green line on the chart, plus an RSI momentum note "
        "comparing today's RSI to RSI when the original high was made -- a caution flag if weaker, never a "
        "reason a stock is excluded (rising price on weakening RSI doesn't mean it must fall or can't break "
        "resistance). Rule-based scanner matches, not guaranteed profitable recommendations."
    )

    rbo_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All Stocks"], horizontal=True, key="rbo_universe",
    )
    rbo_universe_name = "nifty500" if rbo_universe_choice == "Nifty 500" else "all_nse"
    st.caption(f"✅ Mandatory: closing price > ₹{config.RESISTANCE_BREAKOUT_MIN_PRICE_INR:.0f}.")

    with st.expander("⚙️ Adjust Thresholds"):
        rc1, rc2, rc3 = st.columns(3)
        rbo_lookback = rc1.slider(
            "Previous-High Lookback (sessions)", 60, 250, config.RESISTANCE_BREAKOUT_LOOKBACK_DAYS, 10,
            key="rbo_lookback", help="How far back to search for the previous swing high (resistance).",
        )
        rbo_high_age = rc2.slider(
            "Min. Previous-High Age (sessions)", 5, 60, config.RESISTANCE_BREAKOUT_MIN_HIGH_AGE_DAYS, 5,
            key="rbo_high_age", help="The previous high must be at least this many sessions old.",
        )
        rbo_pullback = rc3.slider(
            "Min. Pullback (%)", 3.0, 25.0, config.RESISTANCE_BREAKOUT_MIN_PULLBACK_PCT, 1.0,
            key="rbo_pullback", help="Minimum decline off the previous high to count as a real base.",
        )
        rc4, rc5, rc6 = st.columns(3)
        rbo_distance = rc4.slider(
            "Distance to Resistance (%)", 0.0, 15.0,
            (config.RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MIN_PCT, config.RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MAX_PCT),
            0.5, key="rbo_distance", help="Pre-Breakout Watchlist band: how close to (but below) resistance.",
        )
        rbo_breakout_min = rc5.slider(
            "Breakout Min. Clearance (%)", 0.1, 3.0, config.RESISTANCE_BREAKOUT_BREAKOUT_MIN_PCT, 0.1,
            key="rbo_breakout_min", help="Close must clear resistance by at least this %.",
        )
        rbo_vol_mult = rc6.slider(
            "Breakout Volume Multiplier", 1.0, 4.0, config.RESISTANCE_BREAKOUT_VOLUME_MULT, 0.1,
            key="rbo_vol_mult",
        )
        rc7, rc8 = st.columns(2)
        rbo_pivot_n = rc7.slider(
            "Swing Confirmation Bars (each side)", 2, 6, config.RESISTANCE_BREAKOUT_PIVOT_N, 1,
            key="rbo_pivot_n", help="Bars required on each side of a candle for it to count as a swing high/low.",
        )
        rbo_momentum_lookback = rc8.slider(
            "Momentum Lookback (sessions)", 3, 30, config.RESISTANCE_BREAKOUT_MOMENTUM_LOOKBACK_DAYS, 1,
            key="rbo_momentum_lookback", help="How many sessions back to measure recent price momentum over.",
        )
        rbo_min_momentum = st.slider(
            "Min. Momentum (%)", 0.0, 15.0, config.RESISTANCE_BREAKOUT_MIN_MOMENTUM_PCT, 0.5,
            key="rbo_min_momentum",
            help="Close must be up at least this % over the momentum lookback -- excludes stocks just "
                 "sitting flat/consolidating with no real forward movement.",
        )

    rbo_params = resistance_breakout.default_params()
    rbo_params.update({
        "resistance_lookback_days": rbo_lookback,
        "min_high_age_days": rbo_high_age,
        "min_pullback_pct": rbo_pullback,
        "prebreakout_distance_min_pct": rbo_distance[0],
        "prebreakout_distance_max_pct": rbo_distance[1],
        "breakout_min_pct": rbo_breakout_min,
        "breakout_volume_mult": rbo_vol_mult,
        "pivot_n": rbo_pivot_n,
        "momentum_lookback_days": rbo_momentum_lookback,
        "min_momentum_pct": rbo_min_momentum,
    })
    rbo_cache_key = (rbo_universe_name, tuple(sorted(rbo_params.items())))

    rbo_cache = st.session_state.resistance_breakout_cache.get(rbo_cache_key)
    run_rbo_clicked = st.button("▶️ Run Resistance Breakout Scan", type="primary", key="run_resistance_breakout")

    if run_rbo_clicked or rbo_cache is None:
        rbo_progress = st.progress(0, text="Starting scan...")

        def _on_rbo_progress(frac, text_):
            rbo_progress.progress(min(frac, 1.0), text=text_)

        try:
            with st.spinner(f"Scanning {rbo_universe_choice} for Resistance Breakout setups..."):
                result_rbo = resistance_breakout.scan_resistance_breakout(
                    universe_name=rbo_universe_name, min_price=config.RESISTANCE_BREAKOUT_MIN_PRICE_INR,
                    params=rbo_params, progress_callback=_on_rbo_progress,
                )
            rbo_progress.empty()
            st.session_state.resistance_breakout_cache[rbo_cache_key] = (time.time(), result_rbo)
        except Exception as exc:
            rbo_progress.empty()
            st.error(f"Scan failed: {exc}. This is usually a temporary Yahoo Finance or network issue -- try again.")
            result_rbo = None
    else:
        result_rbo = rbo_cache[1]

    if result_rbo is None:
        st.stop()

    rbo_scan_time = st.session_state.resistance_breakout_cache[rbo_cache_key][0]
    rbo_scan_label = datetime.datetime.fromtimestamp(rbo_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    rbo_asof = result_rbo.get("data_asof_date")
    rbo_asof_label = rbo_asof.strftime("%d %b %Y") if rbo_asof else "N/A"
    st.caption(
        f"Last scanned: {rbo_scan_label} · Prices as of {rbo_asof_label} close · "
        f"Universe: {result_rbo['universe_label']} ({result_rbo['universe_size']} instruments) · "
        f"Scanned OK: {result_rbo['scanned']}"
    )
    rbo_next_refresh_in = config.SCAN_CACHE_TTL_SECONDS - (time.time() - rbo_scan_time)
    if rbo_next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Resistance Breakout Scan for the latest.")

    pre_df_rbo = result_rbo["pre_breakout"]
    confirmed_df_rbo = result_rbo["confirmed_breakout"]
    unconfirmed_df_rbo = result_rbo["volume_unconfirmed"]

    tab_pre_rbo, tab_confirmed_rbo, tab_unconfirmed_rbo = st.tabs([
        f"👀 Pre-Breakout Watchlist ({len(pre_df_rbo)})",
        f"🚀 Confirmed Breakouts ({len(confirmed_df_rbo)})",
        f"⚠️ Breakout — Volume Unconfirmed ({len(unconfirmed_df_rbo)})",
    ])
    with tab_pre_rbo:
        st.caption("Recovering toward the previous high, within the configured distance band. Not a guaranteed breakout.")
        _render_rbo_tab(pre_df_rbo, "rbo_pre")
    with tab_confirmed_rbo:
        st.caption("Closed above the previous high, on the required volume.")
        _render_rbo_tab(confirmed_df_rbo, "rbo_confirmed")
    with tab_unconfirmed_rbo:
        st.caption("Same price breakout, but volume didn't confirm it -- treat with extra caution.")
        _render_rbo_tab(unconfirmed_df_rbo, "rbo_unconfirmed")

    st.divider()
    st.markdown("#### 📊 Selected Candidate Chart")
    rbo_ticker = st.session_state.rbo_selected_ticker
    if not rbo_ticker:
        st.caption("👆 Click a row in any table above to see its breakout chart here.")
    else:
        rbo_res_idx = result_rbo["resistance_idx"].get(rbo_ticker)
        rbo_pb_idx = result_rbo["pullback_idx"].get(rbo_ticker)
        rbo_sig_idx = result_rbo["signal_idx"].get(rbo_ticker)
        rbo_combined = pd.concat([pre_df_rbo, confirmed_df_rbo, unconfirmed_df_rbo], ignore_index=True)
        rbo_match = rbo_combined[rbo_combined["Ticker"] == rbo_ticker]
        if rbo_res_idx is None or rbo_pb_idx is None or rbo_sig_idx is None or rbo_match.empty:
            st.caption(f"No breakout data for {rbo_ticker} under the current scan -- select a row above again.")
        else:
            rbo_row = rbo_match.iloc[0]
            with st.spinner(f"Loading chart for {rbo_ticker}..."):
                rbo_chart_history = scanner.download_history([rbo_ticker])
            rbo_chart_df = rbo_chart_history.get(rbo_ticker)
            if rbo_chart_df is None:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                rbo_fig = resistance_breakout.build_breakout_chart(
                    rbo_chart_df, rbo_res_idx, float(rbo_row["Resistance Level"]),
                    rbo_pb_idx, float(rbo_row["Pullback Low"]), rbo_sig_idx, rbo_ticker,
                    buy_level=float(rbo_row["Buy Level"]),
                )
                st.plotly_chart(rbo_fig, width="stretch")
                st.caption(
                    "Cyan/purple dotted lines = EMA10/EMA20 (reference only). Red ▽ = previous high "
                    "(resistance). Green dashed line = Buy Level (resistance + breakout buffer). Green △ = "
                    "pullback low. Orange marker = the signal candle; its Volume bar is also highlighted "
                    "orange, so a volume spike is visible at a glance."
                )
                st.markdown(f"**Why qualified:**\n\n{rbo_row['Why Qualified']}")

    st.caption(
        "The previous-high/pullback/recovery/breakout structure and volume confirmation are rule-based "
        "checks against this strategy's own adjustable thresholds -- not guaranteed profitable "
        "recommendations, and not a claim that any stock will keep moving in this direction."
    )
    render_footer(scan_ts=rbo_scan_time)


# ---------------------------------------------------------------------------
# Page: Triple EMA Golden Cross
# ---------------------------------------------------------------------------

_TEG_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "EMA Fast", "EMA Mid", "EMA Slow", "EMA Spread %", "Trend SMA", "Channel Resistance", "Channel Distance %",
    "Golden Cross Date", "Days Since Cross", "Momentum %", "RSI", "Volume Ratio",
]
_TEG_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Setup Status": st.column_config.TextColumn("Status", width="small"),
    "Signal Date": st.column_config.TextColumn("Signal Date", width="small"),
    "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "EMA Fast": st.column_config.NumberColumn("EMA Fast", format="₹%.2f"),
    "EMA Mid": st.column_config.NumberColumn("EMA Mid", format="₹%.2f"),
    "EMA Slow": st.column_config.NumberColumn("EMA Slow", format="₹%.2f"),
    "EMA Spread %": st.column_config.NumberColumn("EMA Spread %", format="%.2f%%"),
    "Trend SMA": st.column_config.NumberColumn("Trend SMA", format="₹%.2f"),
    "Channel Resistance": st.column_config.NumberColumn("Channel Resistance", format="₹%.2f"),
    "Channel Distance %": st.column_config.NumberColumn("Channel Distance %", format="%.2f%%"),
    "Golden Cross Date": st.column_config.TextColumn("Cross Date", width="small"),
    "Days Since Cross": st.column_config.NumberColumn("Days Since Cross"),
    "Momentum %": st.column_config.NumberColumn("Momentum %", format="%+.1f%%"),
    "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
    "Volume Ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
}


def _render_teg_tab(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("No stocks matched your saved strategy in this scan.")
        return
    _export_buttons(df, key_prefix, key_prefix)
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_TEG_COLUMN_CONFIG,
        column_order=[c for c in _TEG_COLUMN_ORDER if c in df.columns],
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_table",
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.teg_selected_ticker = df.iloc[rows[0]]["Ticker"]


if nav_page == "Triple EMA Golden Cross":
    st.markdown("### 🥇 Triple EMA Golden Cross")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "Three EMAs (fast/mid/slow) that start tangled together through a consolidation, then the fast EMA "
        "crosses above the slow EMA (a golden cross) and the three fan out into full bullish order -- "
        "Close > EMA-fast > EMA-mid > EMA-slow, all rising -- with RSI in a healthy range. A volume spike "
        "on top of that full alignment is a Confirmed Breakout; the same alignment without one is still "
        "building. Momentum, EMA-spread, and long-term-trend gates together exclude weak, just-formed "
        "crosses -- flat/tangled stocks, and stocks merely bouncing inside a longer-term downtrend. A "
        "channel gate additionally requires a valid, contained rising channel (reusing Daily Rising "
        "Channel's own detection) with today's close near its top edge -- ready to break out, not just a "
        "loose uptrend. Rule-based scanner matches, not guaranteed profitable recommendations."
    )

    teg_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All Stocks"], horizontal=True, key="teg_universe",
    )
    teg_universe_name = "nifty500" if teg_universe_choice == "Nifty 500" else "all_nse"
    st.caption(f"✅ Mandatory: closing price > ₹{config.TRIPLE_EMA_MIN_PRICE_INR:.0f}.")

    with st.expander("⚙️ Adjust Thresholds"):
        te1, te2, te3 = st.columns(3)
        teg_ema_fast = te1.slider("EMA Fast Period", 3, 20, config.TRIPLE_EMA_FAST, 1, key="teg_ema_fast")
        teg_ema_mid = te2.slider("EMA Mid Period", 10, 40, config.TRIPLE_EMA_MID, 1, key="teg_ema_mid")
        teg_ema_slow = te3.slider("EMA Slow Period", 30, 100, config.TRIPLE_EMA_SLOW, 5, key="teg_ema_slow")
        te4, te5, te6 = st.columns(3)
        teg_cross_lookback = te4.slider(
            "Golden Cross Lookback (sessions)", 20, 200, config.TRIPLE_EMA_GOLDEN_CROSS_LOOKBACK_DAYS, 10,
            key="teg_cross_lookback", help="How far back to search for the fast-EMA-crosses-above-slow-EMA event.",
        )
        teg_rsi = te5.slider(
            "RSI Healthy Range", 0, 100,
            (int(config.TRIPLE_EMA_RSI_MIN), int(config.TRIPLE_EMA_RSI_MAX)), key="teg_rsi",
        )
        teg_vol_mult = te6.slider(
            "Breakout Volume Multiplier", 1.0, 4.0, config.TRIPLE_EMA_BREAKOUT_VOLUME_MULT, 0.1, key="teg_vol_mult",
        )
        te7, te8 = st.columns(2)
        teg_momentum_lookback = te7.slider(
            "Momentum Lookback (sessions)", 3, 30, config.TRIPLE_EMA_MOMENTUM_LOOKBACK_DAYS, 1,
            key="teg_momentum_lookback", help="How many sessions back to measure recent price momentum over.",
        )
        teg_min_momentum = te8.slider(
            "Min. Momentum (%)", 0.0, 15.0, config.TRIPLE_EMA_MIN_MOMENTUM_PCT, 0.5,
            key="teg_min_momentum",
            help="Close must be up at least this % over the momentum lookback -- excludes a weak, "
                 "just-formed cross where the EMAs are bunched together on a flat/rolling-over stock.",
        )
        teg_min_spread = st.slider(
            "Min. EMA Spread (%)", 0.0, 3.0, config.TRIPLE_EMA_MIN_SPREAD_PCT, 0.1,
            key="teg_min_spread",
            help="EMA-fast must sit at least this % above EMA-slow -- a more robust check than momentum "
                 "alone that the EMAs have actually fanned out, not just barely crossed while still tangled.",
        )
        teg_trend_sma = st.slider(
            "Trend SMA Period (sessions)", 100, 250, config.TRIPLE_EMA_TREND_SMA_PERIOD, 10,
            key="teg_trend_sma",
            help="Close must be above this long-term SMA -- excludes stocks whose recent bounce is happening "
                 "inside a longer-term downtrend (e.g. Tata Chemicals, well below its own SMA200).",
        )
        teg_channel_distance = st.slider(
            "Channel Distance to Resistance (%)", 0.0, 15.0,
            (config.TRIPLE_EMA_CHANNEL_DISTANCE_MIN_PCT, config.TRIPLE_EMA_CHANNEL_DISTANCE_MAX_PCT), 0.5,
            key="teg_channel_distance",
            help="Requires a valid rising channel (reusing Daily Rising Channel's own detection) with "
                 "today's close within this band below its projected resistance -- ready to break out, "
                 "not just anywhere inside the channel.",
        )
        if not (teg_ema_fast < teg_ema_mid < teg_ema_slow):
            st.warning("⚠️ EMA periods should be Fast < Mid < Slow for this pattern to make sense.")

    teg_params = triple_ema_golden_cross.default_params()
    teg_params.update({
        "ema_fast": teg_ema_fast,
        "ema_mid": teg_ema_mid,
        "ema_slow": teg_ema_slow,
        "golden_cross_lookback_days": teg_cross_lookback,
        "rsi_min": float(teg_rsi[0]),
        "rsi_max": float(teg_rsi[1]),
        "breakout_volume_mult": teg_vol_mult,
        "momentum_lookback_days": teg_momentum_lookback,
        "min_momentum_pct": teg_min_momentum,
        "min_spread_pct": teg_min_spread,
        "trend_sma_period": teg_trend_sma,
        "channel_distance_min_pct": teg_channel_distance[0],
        "channel_distance_max_pct": teg_channel_distance[1],
    })
    teg_cache_key = (teg_universe_name, tuple(sorted(teg_params.items())))

    teg_cache = st.session_state.triple_ema_cache.get(teg_cache_key)
    run_teg_clicked = st.button("▶️ Run Triple EMA Golden Cross Scan", type="primary", key="run_triple_ema")

    if run_teg_clicked or teg_cache is None:
        teg_progress = st.progress(0, text="Starting scan...")

        def _on_teg_progress(frac, text_):
            teg_progress.progress(min(frac, 1.0), text=text_)

        try:
            with st.spinner(f"Scanning {teg_universe_choice} for Triple EMA Golden Cross setups..."):
                result_teg = triple_ema_golden_cross.scan_triple_ema_golden_cross(
                    universe_name=teg_universe_name, min_price=config.TRIPLE_EMA_MIN_PRICE_INR,
                    params=teg_params, progress_callback=_on_teg_progress,
                )
            teg_progress.empty()
            st.session_state.triple_ema_cache[teg_cache_key] = (time.time(), result_teg)
        except Exception as exc:
            teg_progress.empty()
            st.error(f"Scan failed: {exc}. This is usually a temporary Yahoo Finance or network issue -- try again.")
            result_teg = None
    else:
        result_teg = teg_cache[1]

    if result_teg is None:
        st.stop()

    teg_scan_time = st.session_state.triple_ema_cache[teg_cache_key][0]
    teg_scan_label = datetime.datetime.fromtimestamp(teg_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    teg_asof = result_teg.get("data_asof_date")
    teg_asof_label = teg_asof.strftime("%d %b %Y") if teg_asof else "N/A"
    st.caption(
        f"Last scanned: {teg_scan_label} · Prices as of {teg_asof_label} close · "
        f"Universe: {result_teg['universe_label']} ({result_teg['universe_size']} instruments) · "
        f"Scanned OK: {result_teg['scanned']}"
    )
    teg_next_refresh_in = config.SCAN_CACHE_TTL_SECONDS - (time.time() - teg_scan_time)
    if teg_next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Triple EMA Golden Cross Scan for the latest.")

    cross_df_teg = result_teg["golden_cross"]
    aligned_df_teg = result_teg["bullish_alignment"]
    confirmed_df_teg = result_teg["confirmed_breakout"]

    tab_cross_teg, tab_aligned_teg, tab_confirmed_teg = st.tabs([
        f"➕ Golden Cross Formed ({len(cross_df_teg)})",
        f"📶 Bullish Alignment ({len(aligned_df_teg)})",
        f"🚀 Confirmed Breakout ({len(confirmed_df_teg)})",
    ])
    with tab_cross_teg:
        st.caption("The fast EMA has crossed above the slow EMA and is still holding, but not fully stacked yet.")
        _render_teg_tab(cross_df_teg, "teg_cross")
    with tab_aligned_teg:
        st.caption("Fully stacked (Close > EMA-fast > EMA-mid > EMA-slow, all rising) and RSI is healthy -- still building volume.")
        _render_teg_tab(aligned_df_teg, "teg_aligned")
    with tab_confirmed_teg:
        st.caption("Fully stacked, RSI healthy, and today's volume confirms the move.")
        _render_teg_tab(confirmed_df_teg, "teg_confirmed")

    st.divider()
    st.markdown("#### 📊 Selected Candidate Chart")
    teg_ticker = st.session_state.teg_selected_ticker
    if not teg_ticker:
        st.caption("👆 Click a row in any table above to see its chart here.")
    else:
        teg_cross_idx = result_teg["cross_idx"].get(teg_ticker)
        teg_sig_idx = result_teg["signal_idx"].get(teg_ticker)
        teg_combined = pd.concat([cross_df_teg, aligned_df_teg, confirmed_df_teg], ignore_index=True)
        teg_match = teg_combined[teg_combined["Ticker"] == teg_ticker]
        if teg_cross_idx is None or teg_sig_idx is None or teg_match.empty:
            st.caption(f"No data for {teg_ticker} under the current scan -- select a row above again.")
        else:
            teg_row = teg_match.iloc[0]
            with st.spinner(f"Loading chart for {teg_ticker}..."):
                teg_chart_history = scanner.download_history([teg_ticker])
            teg_chart_df = teg_chart_history.get(teg_ticker)
            if teg_chart_df is None:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                teg_channel = result_teg["channels"].get(teg_ticker)
                teg_fig = triple_ema_golden_cross.build_golden_cross_chart(
                    teg_chart_df, teg_cross_idx, teg_sig_idx, teg_params, teg_ticker, channel=teg_channel,
                )
                st.plotly_chart(teg_fig, width="stretch")
                st.caption(
                    "Green = EMA Fast, Red = EMA Mid, Blue = EMA Slow. Cyan ✚ = the golden-cross candle. "
                    "Solid blue = channel resistance, dotted blue = channel support (▽/△ = the swing points "
                    "used to fit them). The signal day's Volume bar is highlighted orange."
                )
                st.markdown(f"**Why qualified:**\n\n{teg_row['Why Qualified']}")

    st.caption(
        "The EMA structure, golden cross, RSI range, and volume confirmation are rule-based checks against "
        "this strategy's own adjustable thresholds -- not guaranteed profitable recommendations, and not a "
        "claim that any stock will keep moving in this direction."
    )
    render_footer(scan_ts=teg_scan_time)


# ---------------------------------------------------------------------------
# Page: Breakout Flag Continuation
# ---------------------------------------------------------------------------

_BF_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Setup Status", "Signal Date", "Current Price",
    "Resistance Level", "Breakout Date", "Flagpole High", "Flag Low", "Pullback %",
    "% Below Flagpole High", "RSI", "Volume Ratio",
]
_BF_COLUMN_CONFIG = {
    "Rank": st.column_config.NumberColumn("Rank", width="small"),
    "Ticker": st.column_config.TextColumn("Symbol", width="small"),
    "Company Name": st.column_config.TextColumn("Company Name"),
    "Setup Status": st.column_config.TextColumn("Status", width="small"),
    "Signal Date": st.column_config.TextColumn("Signal Date", width="small"),
    "Current Price": st.column_config.NumberColumn("Close", format="₹%.2f"),
    "Resistance Level": st.column_config.NumberColumn("Resistance", format="₹%.2f"),
    "Breakout Date": st.column_config.TextColumn("Breakout Date", width="small"),
    "Flagpole High": st.column_config.NumberColumn("Flagpole High", format="₹%.2f"),
    "Flag Low": st.column_config.NumberColumn("Flag Low", format="₹%.2f"),
    "Pullback %": st.column_config.NumberColumn("Pullback %", format="%.1f%%"),
    "% Below Flagpole High": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
    "RSI": st.column_config.NumberColumn("RSI14", format="%.1f"),
    "Volume Ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
}


def _render_bf_tab(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("No stocks matched your saved strategy in this scan.")
        return
    _export_buttons(df, key_prefix, key_prefix)
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_BF_COLUMN_CONFIG,
        column_order=[c for c in _BF_COLUMN_ORDER if c in df.columns],
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_table",
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.bf_selected_ticker = df.iloc[rows[0]]["Ticker"]


if nav_page == "Breakout Flag Continuation":
    st.markdown("### 🚩 Breakout Flag Continuation")
    st.caption(
        "📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed. "
        "A stock already broke out above a meaningful resistance on volume, pulled back only shallowly (a "
        "'flag,' not a deep base), held above that resistance through the pullback, and is now resuming "
        "(Flag Watchlist) or has broken back above the post-breakout high (Confirmed Continuation / "
        "Continuation — Volume Unconfirmed). Distinct from Resistance Breakout's deep 8%+ base over a long "
        "swing-high lookback -- this uses a simple rolling-max resistance so a single sharp gap-up breakout "
        "candle doesn't break the fit. Rule-based scanner matches, not guaranteed profitable recommendations."
    )

    bf_universe_choice = st.radio(
        "Universe", ["Nifty 500", "All Stocks"], horizontal=True, key="bf_universe",
    )
    bf_universe_name = "nifty500" if bf_universe_choice == "Nifty 500" else "all_nse"
    st.caption(f"✅ Mandatory: closing price > ₹{config.BREAKOUT_FLAG_MIN_PRICE_INR:.0f}.")

    with st.expander("⚙️ Adjust Thresholds"):
        bf1, bf2, bf3 = st.columns(3)
        bf_res_lookback = bf1.slider(
            "Resistance Lookback (sessions)", 20, 150, config.BREAKOUT_FLAG_RESISTANCE_LOOKBACK_DAYS, 5,
            key="bf_res_lookback", help="Trailing window used to define resistance before a breakout day.",
        )
        bf_breakout_lookback = bf2.slider(
            "Breakout Lookback (sessions)", 5, 40, config.BREAKOUT_FLAG_BREAKOUT_LOOKBACK_DAYS, 1,
            key="bf_breakout_lookback", help="How far back to search for the most recent qualifying breakout.",
        )
        bf_breakout_vol_mult = bf3.slider(
            "Breakout Volume Multiplier", 1.0, 4.0, config.BREAKOUT_FLAG_BREAKOUT_VOLUME_MULT, 0.1,
            key="bf_breakout_vol_mult",
        )
        bf4, bf5, bf6 = st.columns(3)
        bf_pullback = bf4.slider(
            "Flag Pullback (%)", 0.5, 20.0,
            (config.BREAKOUT_FLAG_PULLBACK_MIN_PCT, config.BREAKOUT_FLAG_PULLBACK_MAX_PCT), 0.5,
            key="bf_pullback", help="Shallow pullback since the breakout -- not Resistance Breakout's deep base.",
        )
        bf_retest_tolerance = bf5.slider(
            "Retest Tolerance (%)", 0.0, 10.0, config.BREAKOUT_FLAG_RETEST_TOLERANCE_PCT, 0.5,
            key="bf_retest_tolerance",
            help="How far the flag low may dip below the breakout resistance and still count as holding.",
        )
        bf_continuation_min = bf6.slider(
            "Continuation Min. Clearance (%)", 0.1, 3.0, config.BREAKOUT_FLAG_CONTINUATION_MIN_PCT, 0.1,
            key="bf_continuation_min", help="Close must clear the flagpole high by at least this %.",
        )
        bf7, bf8 = st.columns(2)
        bf_continuation_vol_mult = bf7.slider(
            "Continuation Volume Multiplier", 1.0, 4.0, config.BREAKOUT_FLAG_CONTINUATION_VOLUME_MULT, 0.1,
            key="bf_continuation_vol_mult",
        )
        bf_rsi = bf8.slider(
            "RSI Healthy Range", 0, 100,
            (int(config.BREAKOUT_FLAG_RSI_MIN), int(config.BREAKOUT_FLAG_RSI_MAX)), key="bf_rsi",
        )

    bf_params = breakout_flag.default_params()
    bf_params.update({
        "resistance_lookback_days": bf_res_lookback,
        "breakout_lookback_days": bf_breakout_lookback,
        "breakout_volume_mult": bf_breakout_vol_mult,
        "flag_pullback_min_pct": bf_pullback[0],
        "flag_pullback_max_pct": bf_pullback[1],
        "retest_tolerance_pct": bf_retest_tolerance,
        "continuation_min_pct": bf_continuation_min,
        "continuation_volume_mult": bf_continuation_vol_mult,
        "rsi_min": float(bf_rsi[0]),
        "rsi_max": float(bf_rsi[1]),
    })
    bf_cache_key = (bf_universe_name, tuple(sorted(bf_params.items())))

    bf_cache = st.session_state.breakout_flag_cache.get(bf_cache_key)
    run_bf_clicked = st.button("▶️ Run Breakout Flag Scan", type="primary", key="run_breakout_flag")

    if run_bf_clicked or bf_cache is None:
        bf_progress = st.progress(0, text="Starting scan...")

        def _on_bf_progress(frac, text_):
            bf_progress.progress(min(frac, 1.0), text=text_)

        try:
            with st.spinner(f"Scanning {bf_universe_choice} for Breakout Flag Continuation setups..."):
                result_bf = breakout_flag.scan_breakout_flag(
                    universe_name=bf_universe_name, min_price=config.BREAKOUT_FLAG_MIN_PRICE_INR,
                    params=bf_params, progress_callback=_on_bf_progress,
                )
            bf_progress.empty()
            st.session_state.breakout_flag_cache[bf_cache_key] = (time.time(), result_bf)
        except Exception as exc:
            bf_progress.empty()
            st.error(f"Scan failed: {exc}. This is usually a temporary Yahoo Finance or network issue -- try again.")
            result_bf = None
    else:
        result_bf = bf_cache[1]

    if result_bf is None:
        st.stop()

    bf_scan_time = st.session_state.breakout_flag_cache[bf_cache_key][0]
    bf_scan_label = datetime.datetime.fromtimestamp(bf_scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    bf_asof = result_bf.get("data_asof_date")
    bf_asof_label = bf_asof.strftime("%d %b %Y") if bf_asof else "N/A"
    st.caption(
        f"Last scanned: {bf_scan_label} · Prices as of {bf_asof_label} close · "
        f"Universe: {result_bf['universe_label']} ({result_bf['universe_size']} instruments) · "
        f"Scanned OK: {result_bf['scanned']}"
    )
    bf_next_refresh_in = config.SCAN_CACHE_TTL_SECONDS - (time.time() - bf_scan_time)
    if bf_next_refresh_in <= 0:
        st.caption("⏳ This data may be stale -- use ▶️ Run Breakout Flag Scan for the latest.")

    watchlist_df_bf = result_bf["flag_watchlist"]
    confirmed_df_bf = result_bf["confirmed_continuation"]
    unconfirmed_df_bf = result_bf["volume_unconfirmed"]

    tab_watchlist_bf, tab_confirmed_bf, tab_unconfirmed_bf = st.tabs([
        f"🚩 Flag Watchlist ({len(watchlist_df_bf)})",
        f"🚀 Confirmed Continuation ({len(confirmed_df_bf)})",
        f"⚠️ Continuation — Volume Unconfirmed ({len(unconfirmed_df_bf)})",
    ])
    with tab_watchlist_bf:
        st.caption("A fresh volume breakout, a shallow pullback still holding above resistance -- watching for a resumption.")
        _render_bf_tab(watchlist_df_bf, "bf_watchlist")
    with tab_confirmed_bf:
        st.caption("Closed back above the post-breakout high, on the required volume.")
        _render_bf_tab(confirmed_df_bf, "bf_confirmed")
    with tab_unconfirmed_bf:
        st.caption("Same price continuation, but volume didn't confirm it -- treat with extra caution.")
        _render_bf_tab(unconfirmed_df_bf, "bf_unconfirmed")

    st.divider()
    st.markdown("#### 📊 Selected Candidate Chart")
    bf_ticker = st.session_state.bf_selected_ticker
    if not bf_ticker:
        st.caption("👆 Click a row in any table above to see its chart here.")
    else:
        bf_breakout_idx = result_bf["breakout_idx"].get(bf_ticker)
        bf_flag_low_idx = result_bf["flag_low_idx"].get(bf_ticker)
        bf_sig_idx = result_bf["signal_idx"].get(bf_ticker)
        bf_combined = pd.concat([watchlist_df_bf, confirmed_df_bf, unconfirmed_df_bf], ignore_index=True)
        bf_match = bf_combined[bf_combined["Ticker"] == bf_ticker]
        if bf_breakout_idx is None or bf_flag_low_idx is None or bf_sig_idx is None or bf_match.empty:
            st.caption(f"No data for {bf_ticker} under the current scan -- select a row above again.")
        else:
            bf_row = bf_match.iloc[0]
            with st.spinner(f"Loading chart for {bf_ticker}..."):
                bf_chart_history = scanner.download_history([bf_ticker])
            bf_chart_df = bf_chart_history.get(bf_ticker)
            if bf_chart_df is None:
                st.caption("Price history unavailable for this ticker right now.")
            else:
                bf_fig = breakout_flag.build_flag_chart(
                    bf_chart_df, bf_breakout_idx, float(bf_row["Resistance Level"]),
                    bf_flag_low_idx, float(bf_row["Flag Low"]), bf_sig_idx, bf_ticker,
                )
                st.plotly_chart(bf_fig, width="stretch")
                st.caption(
                    "Red dashed line = breakout resistance. Cyan star = the breakout day (its Volume bar is "
                    "also cyan). Green △ = flag low. The signal day's Volume bar is highlighted orange."
                )
                st.markdown(f"**Why qualified:**\n\n{bf_row['Why Qualified']}")

    st.caption(
        "The breakout/flag/continuation structure and volume confirmation are rule-based checks against "
        "this strategy's own adjustable thresholds -- not guaranteed profitable recommendations, and not a "
        "claim that any stock will keep moving in this direction."
    )
    render_footer(scan_ts=bf_scan_time)


# ---------------------------------------------------------------------------
# Page: Watchlist
# ---------------------------------------------------------------------------

elif nav_page == "Watchlist":
    st.markdown(f"### ⭐ Watchlist ({len(watchlist_tickers)})")
    if not watchlist_tickers:
        st.info(
            "Your watchlist is empty. Search a stock or select one from the Upside Buy Movement page, "
            "then use \"⭐ Add to Watchlist\" in the detail panel."
        )
    else:
        _export_buttons(watchlist_df, "watchlist", "watchlist")
        _select_from_watchlist_table(watchlist_df, key="watchlist_page_table")
    st.divider()
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Stock Analysis (search-driven)
# ---------------------------------------------------------------------------

elif nav_page == "Stock Analysis":
    st.markdown("### 📈 Stock Analysis")
    st.caption("Use the search box in the header to look up any ticker, or pick one from Upside Buy Movement/Watchlist.")
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Scan History
# ---------------------------------------------------------------------------

elif nav_page == "Scan History":
    st.markdown("### 🕐 Scan History")
    entries = scan_history.load()
    if not entries:
        st.caption("No scans recorded yet.")
    else:
        hist_df = pd.DataFrame(list(reversed(entries)))
        for col in ("universe_size", "scanned", "failures", "near_resistance", "consolidating", "already_broken_out"):
            if col not in hist_df.columns:
                hist_df[col] = None  # entries recorded before this strategy existed
        hist_df = hist_df[[
            "date", "market", "universe_size", "scanned", "failures",
            "near_resistance", "consolidating", "already_broken_out",
        ]]
        hist_df.columns = [
            "Date", "Universe", "Total", "Scanned OK", "Failures",
            "Near Resistance", "Consolidating", "Already Broken Out",
        ]
        st.dataframe(hist_df, hide_index=True, width="stretch")
        _export_buttons(hist_df, "scan_history", "scanhist")
    render_footer()


# ---------------------------------------------------------------------------
# Page: Help & Support
# ---------------------------------------------------------------------------

elif nav_page == "Help & Support":
    st.markdown("### ❓ Help & Support")
    st.markdown(
        """
        **Data source**: This app uses `yfinance` (Yahoo Finance) only — there is no official NSE
        real-time feed. Quotes may be delayed or cached (EOD).

        **Strategy**: "Upside Buy Movement" looks for stocks that have NOT yet broken out but show the
        technical fingerprint of a stock immediately before a strong breakout: Close > EMA20 > EMA50 (both
        rising), a higher-high/higher-low trend over ~3 months, RSI(14) 50-65, a flat/improving or
        recently-positive MACD histogram, contracting volume and ATR%, a narrowing 10-day trading range, a
        7-20 session consolidation within 0-5% of a 20/40/60-day resistance level, and positive 20-day
        relative strength vs. the Nifty 50. Every condition must pass together. Results split into
        **Near Resistance**, **Consolidating**, and **Already Broken Out** (reference only, excluded from
        being a candidate). None of this predicts that any stock will break out on the next session — it
        only reports today's measured technical state.

        Two pages run the exact same rule set over different universes: **"🎯 Upside Buy Movement"** scans
        the Nifty 500 only; **"💹 Upside Buy Movement above 100"** scans all NSE stocks priced above ₹100
        (NSE's broadest official list, "Nifty Total Market", as a practical stand-in for "all NSE stocks").

        **"📐 Daily Rising Channel"** is a separate, adjustable strategy: it fits an upward-sloping
        support/resistance channel to confirmed swing highs/lows on the daily chart and looks for stocks
        either approaching that channel's resistance (**Pre-Breakout Watchlist**) or already closing above
        it (**Confirmed Breakout**, or **Breakout — Volume Unconfirmed** if volume didn't back the move).
        Uses split/dividend-adjusted prices and only completed daily candles. Unlike the two Upside Buy
        Movement pages, every threshold here (touch tolerance, parallelism, containment, RSI range,
        distance to resistance, volume multiplier, lookback window) is adjustable from the page itself.

        **"🧭 Daily Trend + Consolidation"** requires the stock AND Nifty 50 to both be trending up, a
        tight (≤8% by default) consolidation over the preceding 15 sessions, and a liquidity floor (20-
        session average traded value > ₹10 crore by default). Same Pre-Breakout / Confirmed Breakout /
        Volume Unconfirmed split as the other breakout strategies, every threshold adjustable. Its
        companion page, **"🧪 Trend + Consolidation Backtest"**, paper-trades those exact rules
        historically (next-session-open entries, ATR-based stops, a profit target, a time-based exit,
        modeled costs) and reports development vs. untouched out-of-sample results separately. These
        are a starting hypothesis, not a proven profitable strategy, and the backtest's universe list is
        today's index membership applied to past dates -- it carries survivorship bias, disclosed on
        that page rather than corrected (no free historical point-in-time membership data exists).

        **Watchlist**: persists across restarts (`data/watchlist.json`) — add/remove from any stock's
        detail panel.

        **Known limitations**: no exchange-holiday calendar for the Market Open/Closed badge; relative
        strength is measured against the Nifty 50, not a true Nifty 500 index (Yahoo has no reliable free
        feed for one); sector-index relative strength is not implemented.
        """
    )
    render_footer()
