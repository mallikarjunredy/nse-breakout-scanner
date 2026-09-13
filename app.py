"""Streamlit dashboard: near-real-time NSE / NYSE breakout scanner.

Run with:  streamlit run app.py --server.port 8501
"""

import datetime
import io
import time
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from src import config, deep_dive, detail, market_overview, scan_history, scanner, watchlist

st.set_page_config(page_title="NSE Scanner", page_icon="📈", layout="wide")


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


def _market_status(market: str) -> tuple[str, bool]:
    """OPEN/CLOSED based on exchange trading hours only -- does NOT account
    for market holidays (no holiday calendar is wired up).
    """
    if market == "NYSE":
        tz, exch = ZoneInfo("America/New_York"), "NYSE/Nasdaq"
        open_h, open_m = config.NYSE_OPEN_HOUR, config.NYSE_OPEN_MINUTE
        close_h, close_m = config.NYSE_CLOSE_HOUR, config.NYSE_CLOSE_MINUTE
    else:
        tz, exch = ZoneInfo("Asia/Kolkata"), "NSE"
        open_h, open_m = config.NSE_OPEN_HOUR, config.NSE_OPEN_MINUTE
        close_h, close_m = config.NSE_CLOSE_HOUR, config.NSE_CLOSE_MINUTE
    now = datetime.datetime.now(tz)
    open_t = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    close_t = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    is_open = now.weekday() < 5 and open_t <= now <= close_t
    return exch, is_open


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "scan_cache" not in st.session_state:
    st.session_state.scan_cache = {}
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

_PARAM_DEFAULTS = {
    "param_market_label": list(config.MARKETS.keys())[0],
    "param_rsi_range": (int(config.RSI_THRESHOLD), 80),
    "param_volume_multiplier": float(config.VOLUME_SPIKE_MULTIPLIER),
    "param_near_pct": float(config.NEAR_BREAKOUT_PCT * 100),
    "param_min_price": float(config.MIN_PRICE_INR),
    "param_breakout_periods": list(config.RESISTANCE_LOOKBACKS.keys()),
}
for _k, _v in _PARAM_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

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
    .app-header {
        display: flex; align-items: center; justify-content: space-between;
        gap: 1rem; flex-wrap: wrap; margin-bottom: 0.8rem;
        padding: 0.8rem 1.2rem;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(79,209,232,0.18);
        border-radius: 14px;
    }
    .app-logo-title { font-size: 1.25rem; font-weight: 700; color: #EAF2FA; margin: 0; }
    .app-logo-sub { font-size: 0.72rem; color: #6FE3D6; margin: 0; letter-spacing: 0.02em; }
    .status-badge {
        display: inline-flex; align-items: center; gap: 0.4rem;
        padding: 0.25rem 0.75rem; border-radius: 999px;
        font-size: 0.75rem; font-weight: 700; letter-spacing: 0.03em; border: 1px solid;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .meta-text { color: #9FB3D1; font-size: 0.75rem; text-align: right; line-height: 1.3; }
    .nav-tagline {
        margin-top: 1rem; padding: 0.9rem; border-radius: 12px;
        background: linear-gradient(135deg, rgba(79,209,232,0.14), rgba(28,37,65,0.4));
        border: 1px solid rgba(79,209,232,0.25); color: #CFE3F5; font-size: 0.82rem; line-height: 1.4;
    }
    .signal-pill {
        display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
        font-size: 0.72rem; font-weight: 700;
    }
    .pill-breakout { background: rgba(62,207,142,0.15); color: #3ECF8E; border: 1px solid rgba(62,207,142,0.4); }
    .pill-near { background: rgba(255,176,32,0.15); color: #FFB020; border: 1px solid rgba(255,176,32,0.4); }
    .sector-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.35rem 0; }
    .sector-label { width: 110px; font-size: 0.78rem; color: #CFE3F5; flex-shrink: 0; }
    .sector-bar-track { flex: 1; background: rgba(255,255,255,0.06); border-radius: 6px; height: 10px; overflow: hidden; }
    .sector-bar-fill { background: linear-gradient(90deg, #4FD1E8, #3ECF8E); height: 100%; border-radius: 6px; }
    .sector-count { width: 24px; text-align: right; font-size: 0.78rem; color: #EAF2FA; font-weight: 600; }
    .quality-score-big { font-size: 2.2rem; font-weight: 800; color: #EAF2FA; }
    .quality-bar-track { background: rgba(255,255,255,0.08); border-radius: 8px; height: 12px; overflow: hidden; margin-top: 0.4rem; }
    .app-footer {
        display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem;
        color: #7C90AC; font-size: 0.75rem; padding: 1rem 0.2rem; border-top: 1px solid rgba(255,255,255,0.06);
        margin-top: 1.5rem;
    }
    .summary-row { display: flex; gap: 0.9rem; flex-wrap: wrap; margin-bottom: 1rem; }
    .summary-card {
        flex: 1; min-width: 150px; background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; padding: 1rem 1.1rem;
        box-shadow: 0 4px 16px rgba(0,0,0,0.25);
    }
    .summary-card.greet-card { flex: 1.6; display: flex; align-items: center; gap: 0.8rem; }
    .greet-icon { font-size: 1.8rem; }
    .greet-title { font-size: 1.05rem; color: #EAF2FA; line-height: 1.3; }
    .greet-sub { font-size: 0.75rem; color: #8FA3C0; margin-top: 0.15rem; }
    .metric-icon {
        width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center;
        justify-content: center; font-size: 1rem; margin-bottom: 0.5rem;
    }
    .metric-value { font-size: 1.7rem; font-weight: 800; color: #EAF2FA; line-height: 1; }
    .metric-label { font-size: 0.75rem; color: #8FA3C0; margin-top: 0.25rem; }
    .metric-sub { font-size: 0.7rem; color: #6FE3D6; margin-top: 0.3rem; }
    .html-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; table-layout: fixed; }
    .html-table th {
        text-align: left; color: #8FA3C0; font-weight: 600; font-size: 0.72rem;
        text-transform: uppercase; letter-spacing: 0.03em; padding: 0.4rem 0.4rem;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .html-table td {
        padding: 0.55rem 0.4rem; border-bottom: 1px solid rgba(255,255,255,0.05); color: #DCE6F2;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .html-table tr:hover td { background: rgba(79,209,232,0.05); }
    .html-table .ellipsis-cell { max-width: 0; }
    .preview-table-wrap { width: 100%; overflow-x: auto; }
    @media (max-width: 900px) {
        .html-table th:nth-child(2), .html-table td:nth-child(2) { display: none; }
    }
    .ticker-link { color: #4FD1E8; font-weight: 700; }
    .distance-pos { color: #3ECF8E; font-weight: 600; }
    .distance-neg { color: #FF6B6B; font-weight: 600; }
    .section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem; }
    .view-all-link {
        font-size: 0.78rem; color: #4FD1E8; text-decoration: none; font-weight: 600;
    }
    .stock-info-card, .why-card, .score-card {
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
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar: icon navigation
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("Home", "🏠"), ("Scanner", "🔍"), ("Watchlist", "⭐"), ("Stock Analysis", "📈"),
    ("Scan History", "🕐"), ("Strategy Settings", "⚙️"), ("Help & Support", "❓"),
]

with st.sidebar:
    st.markdown("### 📊 NSE Scanner")
    st.caption("Breakout. Trade Smarter.")
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
        'title="Data via yfinance (Yahoo Finance). Quotes may be delayed ~15-20 minutes, '
        'especially for NSE tickers. P/E ratios are not available for all stocks.">'
        "ℹ️ Data source</span>",
        unsafe_allow_html=True,
    )

nav_page = st.session_state.nav_page
market = config.MARKETS[st.session_state.param_market_label]
rsi_min_val, rsi_max_val = st.session_state.param_rsi_range
volume_multiplier = st.session_state.param_volume_multiplier
near_breakout_pct = st.session_state.param_near_pct
breakout_periods = st.session_state.param_breakout_periods or list(config.RESISTANCE_LOOKBACKS.keys())
min_price_input = st.session_state.param_min_price if market in ("NSE", "NSE_ALL") else None
currency = "₹" if market in ("NSE", "NSE_ALL") else "$"
exch_name, market_is_open = _market_status(market)


# ---------------------------------------------------------------------------
# Header bar
# ---------------------------------------------------------------------------

status_color = "#3ECF8E" if market_is_open else "#FF6B6B"
now_str = time.strftime("%d %b %Y")

h_left, h_mid, h_right = st.columns([2.2, 3, 2.6])
with h_left:
    st.markdown(
        '<p class="app-logo-title">📊 NSE Scanner</p>'
        '<p class="app-logo-sub">Breakout. Trade Smarter.</p>',
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
    st.markdown(
        f'<div style="text-align:right;">'
        f'<span class="status-badge" style="color:{status_color}; border-color:{status_color}66; '
        f'background:{status_color}1F;"><span class="dot" style="background:{status_color};"></span>'
        f'Market: {"OPEN" if market_is_open else "CLOSED"} — {exch_name}</span><br>'
        f'<span class="meta-text">{st.session_state.param_market_label}<br>Data Date: {now_str}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown(
    f'<div class="meta-text" style="text-align:left; margin: -0.5rem 0 0.8rem 0;">'
    f"📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE real-time feed "
    f"&nbsp;·&nbsp; Market status reflects trading hours only, not exchange holidays.</div>",
    unsafe_allow_html=True,
)

if search_submitted and search_input.strip():
    with st.spinner(f"Looking up {search_input.strip()}..."):
        found = scanner.search_ticker(
            search_input, market, rsi_threshold=rsi_min_val, rsi_max=rsi_max_val,
            volume_multiplier=volume_multiplier, near_pct=near_breakout_pct / 100, min_price=min_price_input,
        )
    if found is None:
        st.session_state.search_result = None
        st.session_state.search_error = f"Couldn't find price data for '{search_input.strip()}'. Check the symbol and try again."
    else:
        st.session_state.search_result = found
        st.session_state.search_error = None
        st.session_state.selected_ticker = found["Ticker"]
        st.session_state.nav_page = "Stock Analysis"
        # Force a clean rerun: `nav_page` was already captured into a local
        # variable below before this point, so without a rerun the page
        # routing would still act on the stale value and Home's preview-
        # table dropdowns would immediately overwrite selected_ticker.
        st.rerun()

if st.session_state.search_error:
    st.error(st.session_state.search_error)


# ---------------------------------------------------------------------------
# Run (or reuse cached) scan + watchlist snapshot
# ---------------------------------------------------------------------------

st_autorefresh(interval=config.AUTOREFRESH_INTERVAL_MS, key="autorefresh_tick")

cache_key = (
    market, rsi_min_val, rsi_max_val, volume_multiplier, near_breakout_pct, min_price_input,
    tuple(sorted(breakout_periods)),
)
cache_entry = st.session_state.scan_cache.get(cache_key)
cache_fresh = cache_entry is not None and (time.time() - cache_entry[0]) < config.SCAN_CACHE_TTL_SECONDS
manual_refresh = st.session_state.pop("_trigger_scan", False)
need_scan = manual_refresh or not cache_fresh

if need_scan:
    progress_bar = st.progress(0, text="Starting scan...")

    def _on_progress(frac, text):
        progress_bar.progress(min(frac, 1.0), text=text)

    with st.spinner(f"Scanning {st.session_state.param_market_label}..."):
        result = scanner.scan_market(
            market, progress_callback=_on_progress, min_price=min_price_input,
            rsi_threshold=rsi_min_val, rsi_max=rsi_max_val, volume_multiplier=volume_multiplier,
            near_pct=near_breakout_pct / 100, periods=breakout_periods,
        )
    progress_bar.empty()
    st.session_state.scan_cache[cache_key] = (time.time(), result)
    scan_history.record(config.MARKET_LABELS.get(market, market), result)
else:
    result = cache_entry[1]

scan_time = st.session_state.scan_cache[cache_key][0]
last_updated = time.strftime("%I:%M %p", time.localtime(scan_time))
last_updated_full = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(scan_time))
next_refresh_in = max(0, int(config.SCAN_CACHE_TTL_SECONDS - (time.time() - scan_time)))
next_refresh_label = f"{next_refresh_in // 3600}h {(next_refresh_in % 3600) // 60}m"

watchlist_tickers = watchlist.load()
watchlist_key = (tuple(watchlist_tickers), rsi_min_val, rsi_max_val, volume_multiplier, near_breakout_pct)
wl_cache_entry = st.session_state.watchlist_cache.get(watchlist_key)
wl_cache_fresh = wl_cache_entry is not None and (time.time() - wl_cache_entry[0]) < config.SCAN_CACHE_TTL_SECONDS
if need_scan or not wl_cache_fresh:
    with st.spinner("Refreshing watchlist..."):
        watchlist_df = scanner.get_watchlist_data(
            watchlist_tickers, rsi_threshold=rsi_min_val, rsi_max=rsi_max_val,
            volume_multiplier=volume_multiplier, near_pct=near_breakout_pct / 100,
        )
    st.session_state.watchlist_cache[watchlist_key] = (time.time(), watchlist_df)
else:
    watchlist_df = wl_cache_entry[1]

st.sidebar.download_button(
    "📥 Export All (Excel)",
    _to_excel_bytes({
        "Breakout (Daily)": result["breakout"],
        "Near Breakout (Daily)": result["near_breakout"],
        "Breakout (Weekly)": result["weekly_breakout"],
        "Near Breakout (Weekly)": result["weekly_near_breakout"],
        "Watchlist": watchlist_df,
    }),
    file_name=f"scanner_export_{market}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)


# ---------------------------------------------------------------------------
# Shared column configs
# ---------------------------------------------------------------------------

def _display_columns():
    return {
        "Rank": st.column_config.NumberColumn("Rank", width="small"),
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Company Name": st.column_config.TextColumn("Company Name"),
        "Sector": st.column_config.TextColumn("Sector", width="small"),
        "Signal": st.column_config.TextColumn("Signal", width="small"),
        "Current Price": st.column_config.NumberColumn("LTP", format=f"{currency}%.2f"),
        "P/E Ratio": st.column_config.NumberColumn("P/E Ratio", format="%.2f"),
        "Volume Ratio": st.column_config.NumberColumn("Volume Ratio", format="%.2fx"),
        "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
        "Quality Score": st.column_config.ProgressColumn(
            "Score", format="%d", min_value=0, max_value=100,
            help="Deterministic 0-100 score (resistance strength, volume, RSI, trend, close strength). "
                 "Not a buy/sell recommendation.",
        ),
        "Buy Level": st.column_config.NumberColumn("Buy Level", format=f"{currency}%.2f"),
        "Stop Loss": st.column_config.NumberColumn("Stop Loss", format=f"{currency}%.2f"),
        "Resistance Period": st.column_config.TextColumn("Period", width="small"),
        "Resistance Level": st.column_config.NumberColumn("Resistance", format=f"{currency}%.2f"),
        "52W High": st.column_config.NumberColumn("52W High", format=f"{currency}%.2f"),
        "% From Resistance": st.column_config.NumberColumn("Distance", format="%.2f%%"),
        "Support Level": st.column_config.NumberColumn("Support", format=f"{currency}%.2f"),
    }


_FULL_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Sector", "Signal", "Current Price", "P/E Ratio",
    "Resistance Level", "% From Resistance", "Volume Ratio", "RSI", "Quality Score",
    "Buy Level", "Stop Loss", "Resistance Period", "52W High", "Support Level",
]
_PREVIEW_COLUMN_ORDER = [
    "Rank", "Ticker", "Company Name", "Current Price", "Resistance Level",
    "% From Resistance", "Volume Ratio", "RSI", "Signal", "Quality Score",
]


def _visible(df: pd.DataFrame, order: list) -> list:
    return [c for c in order if c in df.columns]


def _watchlist_columns():
    return {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Company Name": st.column_config.TextColumn("Company Name"),
        "Sector": st.column_config.TextColumn("Sector", width="small"),
        "Status": st.column_config.TextColumn("Status", width="small"),
        "Current Price": st.column_config.NumberColumn("Current Price", format="%.2f"),
        "P/E Ratio": st.column_config.NumberColumn("P/E Ratio", format="%.2f"),
        "Volume Ratio": st.column_config.NumberColumn("Volume Ratio", format="%.2fx"),
        "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
        "Buy Level": st.column_config.NumberColumn("Buy Level", format="%.2f"),
        "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="%.2f"),
        "Resistance Level": st.column_config.NumberColumn("Resistance Level", format="%.2f"),
        "52W High": st.column_config.NumberColumn("52W High", format="%.2f"),
        "Support Level": st.column_config.NumberColumn("Support Level", format="%.2f"),
        "Reason": st.column_config.TextColumn("Reason", width="large"),
    }


def _select_from_table(df: pd.DataFrame, columns_order: list, key: str, height: int | None = None):
    kwargs = {"height": height} if height is not None else {}
    event = st.dataframe(
        df, hide_index=True, width="stretch", column_config=_display_columns(),
        column_order=_visible(df, columns_order), on_select="rerun", selection_mode="single-row",
        key=key, **kwargs,
    )
    rows = event["selection"]["rows"]
    if rows:
        st.session_state.selected_ticker = df.iloc[rows[0]]["Ticker"]


# ---------------------------------------------------------------------------
# Reusable blocks
# ---------------------------------------------------------------------------

def render_scan_parameters(button_key: str):
    with st.container(border=True):
        st.markdown("##### ⚙️ Scan Parameters (Your Strategy)")
        c1, c2, c3, c4, c5, c6 = st.columns([1.4, 1.3, 1, 1.2, 1.3, 1])
        c1.selectbox("Market", list(config.MARKETS.keys()), key="param_market_label")
        c2.multiselect(
            "Breakout Period", options=list(config.RESISTANCE_LOOKBACKS.keys()), key="param_breakout_periods",
            help="Which resistance lookback windows to check (5D-3Y).",
        )
        c3.number_input("Min Volume Ratio", min_value=1.0, max_value=10.0, step=0.1, key="param_volume_multiplier")
        c4.slider("RSI Range", min_value=0, max_value=100, key="param_rsi_range")
        c5.slider(
            "Min Distance to Resistance (%)", min_value=0.5, max_value=10.0, step=0.5, key="param_near_pct",
        )
        cur_market = config.MARKETS[st.session_state.param_market_label]
        if cur_market in ("NSE", "NSE_ALL"):
            c6.number_input(
                "Min Price (₹)", min_value=float(config.MIN_PRICE_INR), step=10.0, key="param_min_price",
                help=f"Mandatory: only price strictly above ₹{config.MIN_PRICE_INR:.0f} qualifies.",
            )
        else:
            c6.caption("No mandatory\nprice floor for\nthis market.")
        if st.button("▶️ Run Scan", type="primary", width="stretch", key=button_key):
            st.session_state["_trigger_scan"] = True
            st.rerun()
        st.caption(
            f"Active condition: Price > {currency}{min_price_input:.2f}" if min_price_input else
            "No mandatory price floor for this market."
        )


def render_summary_cards():
    hour = datetime.datetime.now().hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
    status_col = "#3ECF8E" if market_is_open else "#FF6B6B"

    def _card(icon, icon_bg, icon_color, value, label):
        return (
            f'<div class="summary-card">'
            f'<div class="metric-icon" style="background:{icon_bg}; color:{icon_color};">{icon}</div>'
            f'<div class="metric-value">{value}</div>'
            f'<div class="metric-label">{label}</div></div>'
        )

    html = '<div class="summary-row">'
    html += (
        '<div class="summary-card greet-card"><div class="greet-icon">👋</div><div>'
        f'<div class="greet-title">{greeting},<br><b>Mallikarjun</b></div>'
        f'<div class="greet-sub">Here are today\'s scan results from {config.MARKET_LABELS.get(market, market)}</div>'
        "</div></div>"
    )
    html += _card("🗄️", "#2E5BFF22", "#4F7CFF", result["universe_size"], "Universe")
    html += _card("✅", "#3ECF8E22", "#3ECF8E", result["scanned"], "Scanned OK")
    html += _card("🚀", "#FF6B6B22", "#FF6B6B", len(result["breakout"]), "Breakouts (Daily)")
    html += _card("🔭", "#B26BFF22", "#B26BFF", len(result["near_breakout"]), "Near Breakouts (Daily)")
    html += (
        '<div class="summary-card"><div class="metric-label">Market Status</div>'
        f'<div class="metric-value" style="color:{status_col}; font-size:1.3rem;">'
        f'{"OPEN" if market_is_open else "CLOSED"}</div>'
        f'<div class="metric-sub">Auto-refreshes every {refresh_label}</div></div>'
    )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)
    st.caption(
        f"Eligible: {result['eligible']} (passed the mandatory price filter) · "
        f"Last scan: {last_updated} · Next scan: ~{next_refresh_label}"
    )
    st.caption(
        f"📅 Weekly timeframe (same strategy, weekly-aggregated bars): "
        f"{len(result['weekly_breakout'])} breakouts, {len(result['weekly_near_breakout'])} near breakouts — "
        "see the Timeframe toggle below."
    )


def render_market_overview():
    st.markdown(
        '<div class="card-header"><span class="badge-dot" style="background:#4F7CFF;"></span>'
        "📈 Market Overview</div>",
        unsafe_allow_html=True,
    )
    idx = market_overview.get_index_snapshot(market)
    if idx:
        st.metric(idx["name"], f"{idx['level']:,.2f}", f"{idx['change_pct']:+.2f}%")
    else:
        st.caption("Index data unavailable right now.")
    a, d, u = st.columns(3)
    a.metric("Advances", result["advances"])
    d.metric("Declines", result["declines"])
    u.metric("Unchanged", result["unchanged"])
    st.caption(f"Computed from today's {result['scanned']} actually-scanned tickers, not the full exchange.")


def render_sector_summary():
    st.markdown(
        '<div class="card-header"><span class="badge-dot" style="background:#B26BFF;"></span>'
        "🏭 Sector Breakout Count</div>",
        unsafe_allow_html=True,
    )
    sector_counts = scanner.summarize_sectors(result["breakout"], result["near_breakout"])
    if not sector_counts:
        st.caption("No qualifying stocks yet to break down by sector.")
        return
    ranked = sorted(sector_counts.items(), key=lambda kv: kv[1], reverse=True)
    max_count = ranked[0][1]
    rows_html = ""
    for sector, count in ranked:
        width_pct = max(6, int(count / max_count * 100))
        rows_html += (
            f'<div class="sector-row"><span class="sector-label">{sector}</span>'
            f'<span class="sector-bar-track"><span class="sector-bar-fill" style="width:{width_pct}%;"></span></span>'
            f'<span class="sector-count">{count}</span></div>'
        )
    st.markdown(rows_html, unsafe_allow_html=True)


def _signal_pill(signal_text: str) -> str:
    cls = "pill-near" if "Near" in signal_text else "pill-breakout"
    return f'<span class="signal-pill {cls}">{signal_text}</span>'


def render_preview_table_html(df: pd.DataFrame, title: str, icon: str, nav_target: str):
    head_l, head_r1, head_r2 = st.columns([4.2, 1.2, 0.6])
    head_l.markdown(f"##### {icon} {title} ({len(df)})")
    with head_r1:
        if st.button(f"View All ({len(df)})", key=f"viewall_{title}", width="stretch"):
            st.session_state.nav_page = nav_target
            st.rerun()
    with head_r2:
        if not df.empty:
            with st.popover("⬇️", width="stretch"):
                stamp = time.strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    "CSV", _to_csv_bytes(df), file_name=f"{title}_{stamp}.csv",
                    mime="text/csv", key=f"prev_csv_{title}", width="stretch",
                )
                st.download_button(
                    "Excel", _to_excel_bytes({title[:31]: df}), file_name=f"{title}_{stamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"prev_xlsx_{title}", width="stretch",
                )
    if df.empty:
        st.info("No candidates right now.")
        return

    # Preview tables live in a narrower column (see the [2.1, 1] split on the
    # Home page), so this deliberately shows fewer columns than the full
    # Scanner table -- just enough to act on at a glance, so it fits without
    # horizontal scrolling. Rank/Distance/Vol Ratio/RSI/Quality Score are
    # still shown in the full Scanner-page table (one click away via
    # "View All"); Buy/Support/Resistance/Stop Loss stay on every table per
    # the documented requirement.
    rows_html = ""
    for _, r in df.iterrows():
        star = "⭐" if r["Ticker"] in watchlist_tickers else "☆"
        company = (r.get("Company Name") or r["Ticker"])
        buy_lvl = f"{currency}{r['Buy Level']:.2f}" if pd.notna(r.get("Buy Level")) else "N/A"
        support_lvl = f"{currency}{r['Support Level']:.2f}" if pd.notna(r.get("Support Level")) else "N/A"
        stop_lvl = f"{currency}{r['Stop Loss']:.2f}" if pd.notna(r.get("Stop Loss")) else "N/A"
        rows_html += (
            "<tr>"
            f"<td class='ticker-link'>{r['Ticker'].replace('.NS', '')}</td>"
            f"<td class='ellipsis-cell' title='{company}'>{company}</td>"
            f"<td>{currency}{r['Current Price']:.2f}</td>"
            f"<td>{buy_lvl}</td>"
            f"<td>{currency}{r['Resistance Level']:.2f}</td>"
            f"<td>{support_lvl}</td>"
            f"<td>{stop_lvl}</td>"
            f"<td>{_signal_pill(r['Signal'])}</td>"
            f"<td>{star}</td>"
            "</tr>"
        )
    table_html = (
        '<div class="preview-table-wrap"><table class="html-table"><thead><tr>'
        "<th>Ticker</th><th>Company</th><th>LTP</th><th>Buy</th><th>Resistance</th>"
        "<th>Support</th><th>Stop Loss</th><th>Signal</th><th>Watch</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    # The ⭐/☆ in the "Watch" column is a plain HTML cell (st.markdown has no
    # click handler), so it isn't clickable by itself -- this picker + button
    # pair is the actual way to toggle watchlist status for a row without
    # scrolling down to the detail panel.
    display_options = [t.replace(".NS", "") for t in df["Ticker"].tolist()]
    idx_map = dict(zip(display_options, df["Ticker"].tolist()))
    pick_col, watch_col = st.columns([3, 1])
    with pick_col:
        chosen_display = st.selectbox(
            "🔎 View details for", display_options, key=f"pick_{title}", label_visibility="collapsed",
        )
    if chosen_display:
        st.session_state.selected_ticker = idx_map[chosen_display]
        chosen_ticker = idx_map[chosen_display]
        with watch_col:
            if chosen_ticker in watchlist_tickers:
                if st.button("🗑️ Unwatch", key=f"prev_unwatch_{title}", width="stretch"):
                    watchlist.remove(chosen_ticker)
                    st.rerun()
            else:
                if st.button("⭐ Watch", key=f"prev_watch_{title}", width="stretch"):
                    watchlist.add(chosen_ticker)
                    st.rerun()


def render_detail_panel():
    ticker = st.session_state.selected_ticker
    if not ticker:
        st.caption("👆 Click a row in any table above, or search for a ticker, to see detailed stock information here.")
        return

    is_inr = ticker.endswith(".NS")
    curr = "₹" if is_inr else "$"

    combined = pd.concat(
        [result["breakout"], result["near_breakout"], result["weekly_breakout"],
         result["weekly_near_breakout"], watchlist_df],
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
                fig = go.Figure(data=[go.Candlestick(
                    x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
                    increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
                )])
                if tech is not None:
                    if pd.notna(tech.get("Resistance Level")):
                        fig.add_hline(
                            y=tech["Resistance Level"], line_dash="dash", line_color="#FF6B6B",
                            annotation_text=f"Resistance: {curr}{tech['Resistance Level']:.2f}",
                            annotation_position="top left", annotation_font_color="#FF6B6B",
                        )
                    if pd.notna(tech.get("Support Level")):
                        fig.add_hline(
                            y=tech["Support Level"], line_dash="dash", line_color="#3ECF8E",
                            annotation_text=f"Support: {curr}{tech['Support Level']:.2f}",
                            annotation_position="bottom left", annotation_font_color="#3ECF8E",
                        )
                fig.update_layout(
                    template="plotly_dark", height=420, margin=dict(l=10, r=10, t=30, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_rangeslider_visible=False,
                )
                st.plotly_chart(fig, width="stretch")

        with tab_levels:
            if tech is None:
                st.caption("No computed levels available for this ticker.")
            else:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Resistance", f"{curr}{tech['Resistance Level']:.2f}" if pd.notna(tech.get("Resistance Level")) else "N/A")
                k2.metric("Support", f"{curr}{tech['Support Level']:.2f}" if pd.notna(tech.get("Support Level")) else "N/A")
                k3.metric("52W High", f"{curr}{tech['52W High']:.2f}" if pd.notna(tech.get("52W High")) else "N/A")
                k4.metric("Buy Level", f"{curr}{tech['Buy Level']:.2f}" if pd.notna(tech.get("Buy Level")) else "N/A")
                k5, k6, k7, k8 = st.columns(4)
                k5.metric("Stop Loss", f"{curr}{tech['Stop Loss']:.2f}" if pd.notna(tech.get("Stop Loss")) else "N/A")
                k6.metric("RSI", f"{tech['RSI']:.1f}" if pd.notna(tech.get("RSI")) else "N/A")
                k7.metric("Volume Ratio", f"{tech['Volume Ratio']:.2f}x" if pd.notna(tech.get("Volume Ratio")) else "N/A")
                k8.metric(
                    "Quality Score", f"{tech['Quality Score']:.0f}/100" if pd.notna(tech.get("Quality Score")) else "N/A",
                )

        with tab_reason:
            why_text = tech.get("Why Qualified") if tech else None
            reason_text = tech.get("Reason") if tech else None
            if isinstance(why_text, str) and why_text:
                st.success("This stock qualified under the current strategy:")
                for line in why_text.split("\n"):
                    st.markdown(line)
            elif isinstance(reason_text, str) and reason_text:
                st.warning(f"❌ Not qualified — {reason_text}")
            else:
                st.caption("No qualification data available for this ticker.")

    with col_info:
        chg_html = ""
        if change_pct is not None:
            chg_cls = "stock-change-pos" if change_pct >= 0 else "stock-change-neg"
            chg_html = f'<span class="{chg_cls}">{change_pct:+.2f}%</span>'
        price_html = f"{curr}{price_now:.2f}" if price_now is not None else "N/A"

        info_html = (
            '<div class="stock-info-card">'
            f'<div style="font-weight:800; font-size:1.1rem; color:#EAF2FA;">{ticker.replace(".NS", "")}</div>'
            f'<div style="font-size:0.78rem; color:#8FA3C0; margin-bottom:0.6rem;">{info["name"]}</div>'
            f'<div class="stock-price-big">{price_html}</div>{chg_html}'
            '<div style="margin-top:0.8rem;">'
        )
        if tech is not None and pd.notna(tech.get("Resistance Level")):
            info_html += f'<div class="kv-row"><span>Resistance</span><b>{curr}{tech["Resistance Level"]:.2f}</b></div>'
        if tech is not None and pd.notna(tech.get("Support Level")):
            info_html += f'<div class="kv-row"><span>Support</span><b>{curr}{tech["Support Level"]:.2f}</b></div>'
        if tech is not None and pd.notna(tech.get("52W High")):
            info_html += f'<div class="kv-row"><span>52W High</span><b>{curr}{tech["52W High"]:.2f}</b></div>'
        if info.get("52w_low"):
            info_html += f'<div class="kv-row"><span>52W Low</span><b>{curr}{info["52w_low"]:.2f}</b></div>'
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
        st.write("")
        score = tech.get("Quality Score") if tech else None
        if tech is not None and pd.notna(score):
            score_pct = max(0, min(100, int(score)))
            score_color = "#3ECF8E" if score_pct >= 70 else ("#FFB020" if score_pct >= 40 else "#FF6B6B")
            score_html = (
                '<div class="score-card"><b style="color:#EAF2FA;">Breakout Quality Score</b>'
                f'<div class="quality-score-big" style="margin-top:0.4rem;">{score_pct} / 100</div>'
                '<div class="quality-bar-track">'
                f'<div style="width:{score_pct}%; height:100%; background:{score_color}; border-radius:8px;"></div>'
                "</div></div>"
            )
            st.markdown(score_html, unsafe_allow_html=True)

    fcols = st.columns(4)

    def _fmt_market_cap(value):
        if not value:
            return "N/A"
        return f"{curr}{value/1e7:,.0f} Cr" if is_inr else f"{curr}{value/1e9:,.2f} B"

    fcols[0].metric("Market Cap", _fmt_market_cap(info.get("market_cap")))
    fcols[1].metric("EPS (TTM)", f"{curr}{info['eps']:.2f}" if info.get("eps") is not None else "N/A")
    fcols[2].metric("Dividend Yield", f"{info['dividend_yield']:.2f}%" if info.get("dividend_yield") else "N/A")
    fcols[3].metric("Beta", f"{info['beta']:.2f}" if info.get("beta") is not None else "N/A")

    if info.get("target_mean_price"):
        st.caption(
            f"Analyst mean target: {curr}{info['target_mean_price']:.2f} · "
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
            r4.metric("Book Value", f"{curr}{ratios['book_value']:.2f}" if ratios["book_value"] else "N/A")
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
                st.dataframe(_format_financial_df(inc, is_inr), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            st.markdown("**Balance Sheet**")
            if not bs.empty:
                st.dataframe(_format_financial_df(bs, is_inr), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            st.markdown("**Cash Flow**")
            if not cf.empty:
                st.dataframe(_format_financial_df(cf, is_inr), width="stretch")
            else:
                st.caption("Not available for this ticker.")
            if not (inc.empty and bs.empty and cf.empty):
                st.caption(f"Figures in {'Crores (₹ Cr)' if is_inr else 'Billions ($B)'}, except EPS.")

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
                combined_all = pd.concat([result["breakout"], result["near_breakout"]], ignore_index=True)
                peers = combined_all[
                    (combined_all.get("Sector") == sector_name) & (combined_all["Ticker"] != ticker)
                ]
                if peers.empty:
                    st.caption(
                        f"No other {sector_name} stocks in today's "
                        f"{config.MARKET_LABELS.get(market, market)} scan results."
                    )
                else:
                    peer_cols = [c for c in
                                 ["Ticker", "Company Name", "Current Price", "P/E Ratio", "RSI", "Quality Score"]
                                 if c in peers.columns]
                    st.dataframe(peers[peer_cols], hide_index=True, width="stretch")
                st.caption(
                    f"Peers = other {sector_name} stocks that also qualified in today's scan — "
                    "not a comprehensive industry peer list."
                )


def render_footer():
    st.markdown(
        f'<div class="app-footer">'
        f'<span>Data source: Yahoo Finance (cached, EOD/delayed) · For educational purposes only. '
        f"Not investment advice.</span>"
        f'<span>Last updated: {last_updated_full}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page: Home
# ---------------------------------------------------------------------------

if nav_page == "Home":
    render_summary_cards()
    st.write("")
    render_scan_parameters(button_key="run_scan_home")
    st.write("")

    home_timeframe = st.radio(
        "Timeframe", ["Daily", "Weekly"], horizontal=True, key="home_timeframe",
        help="Weekly re-runs the same strategy on weekly-aggregated bars (10/40-week SMA trend "
             "template instead of daily 50/200) -- a higher-timeframe confirmation view.",
    )
    if home_timeframe == "Weekly":
        active_breakout, active_near = result["weekly_breakout"], result["weekly_near_breakout"]
    else:
        active_breakout, active_near = result["breakout"], result["near_breakout"]

    # Default to the #1 breakout (or #1 near-breakout) candidate so the
    # analysis section below is never empty, matching a dashboard-summary
    # feel -- the user can still pick any other row via the dropdowns.
    if not st.session_state.selected_ticker:
        if not active_breakout.empty:
            st.session_state.selected_ticker = active_breakout.iloc[0]["Ticker"]
        elif not active_near.empty:
            st.session_state.selected_ticker = active_near.iloc[0]["Ticker"]

    left, right = st.columns([2.1, 1])
    with left:
        render_preview_table_html(
            active_breakout.head(5), f"Today's Top Breakout Candidates — {home_timeframe}", "🚀", "Scanner",
        )
        st.write("")
        render_preview_table_html(
            active_near.head(5), f"Near Breakout Candidates — {home_timeframe}", "🔭", "Scanner",
        )

    with right:
        with st.container(border=True):
            render_market_overview()
        st.write("")
        st.write("")
        with st.container(border=True):
            render_sector_summary()

    st.divider()
    st.markdown("### 🔎 Selected Stock Analysis")
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Scanner (full tables)
# ---------------------------------------------------------------------------

elif nav_page == "Scanner":
    render_scan_parameters(button_key="run_scan_scanner")
    st.write("")

    scanner_timeframe = st.radio(
        "Timeframe", ["Daily", "Weekly"], horizontal=True, key="scanner_timeframe",
        help="Weekly re-runs the same strategy on weekly-aggregated bars (10/40-week SMA trend "
             "template instead of daily 50/200) -- a higher-timeframe confirmation view.",
    )
    if scanner_timeframe == "Weekly":
        active_breakout, active_near = result["weekly_breakout"], result["weekly_near_breakout"]
    else:
        active_breakout, active_near = result["breakout"], result["near_breakout"]

    all_sectors = sorted(
        set(active_breakout.get("Sector", pd.Series(dtype=str)))
        | set(active_near.get("Sector", pd.Series(dtype=str)))
    )
    selected_sectors = st.multiselect("Filter tables by sector", options=all_sectors, default=all_sectors) if all_sectors else []

    def _sector_filtered(df):
        if df.empty or not selected_sectors or "Sector" not in df.columns:
            return df
        return df[df["Sector"].isin(selected_sectors)]

    tab_b, tab_n, tab_w = st.tabs([
        f"🚀 Breakout ({len(active_breakout)})",
        f"👀 Near Breakout ({len(active_near)})",
        f"⭐ Watchlist ({len(watchlist_tickers)})",
    ])
    with tab_b:
        df_b = _sector_filtered(active_breakout)
        if df_b.empty:
            st.info(f"No {scanner_timeframe.lower()} breakout signals right now.")
        else:
            _export_buttons(df_b, f"breakout_{scanner_timeframe.lower()}_{market}", "breakout")
            _select_from_table(df_b, _FULL_COLUMN_ORDER, key="scanner_breakout_table")
    with tab_n:
        df_n = _sector_filtered(active_near)
        if df_n.empty:
            st.info(f"No {scanner_timeframe.lower()} near-breakout signals right now.")
        else:
            _export_buttons(df_n, f"near_breakout_{scanner_timeframe.lower()}_{market}", "near")
            _select_from_table(df_n, _FULL_COLUMN_ORDER, key="scanner_near_table")
    with tab_w:
        if not watchlist_tickers:
            st.info("Your watchlist is empty. Add stocks from the detail panel below.")
        else:
            _export_buttons(watchlist_df, "watchlist", "watchlist")
            event = st.dataframe(
                watchlist_df, hide_index=True, width="stretch", column_config=_watchlist_columns(),
                on_select="rerun", selection_mode="single-row", key="scanner_watchlist_table",
            )
            rows = event["selection"]["rows"]
            if rows:
                st.session_state.selected_ticker = watchlist_df.iloc[rows[0]]["Ticker"]

    st.caption(
        "Buy Level, Stop Loss, Resistance Level, Support Level, 52W High, and Breakout Quality Score are "
        "computed technical reference points, not investment advice. Breakout / Near Breakout are strategy "
        "classifications, not guaranteed buy/sell recommendations."
    )
    st.divider()
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Watchlist
# ---------------------------------------------------------------------------

elif nav_page == "Watchlist":
    st.markdown(f"### ⭐ Watchlist ({len(watchlist_tickers)})")
    if not watchlist_tickers:
        st.info(
            "Your watchlist is empty. Search a stock or select one from the Scanner page, "
            "then use \"⭐ Add to Watchlist\" in the detail panel."
        )
    else:
        _export_buttons(watchlist_df, "watchlist", "watchlist")
        event = st.dataframe(
            watchlist_df, hide_index=True, width="stretch", column_config=_watchlist_columns(),
            on_select="rerun", selection_mode="single-row", key="watchlist_page_table",
        )
        rows = event["selection"]["rows"]
        if rows:
            st.session_state.selected_ticker = watchlist_df.iloc[rows[0]]["Ticker"]
    st.divider()
    render_detail_panel()
    render_footer()


# ---------------------------------------------------------------------------
# Page: Stock Analysis (search-driven)
# ---------------------------------------------------------------------------

elif nav_page == "Stock Analysis":
    st.markdown("### 📈 Stock Analysis")
    st.caption("Use the search box in the header to look up any ticker, or pick one from Scanner/Watchlist.")
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
        for col in ["weekly_breakouts", "weekly_near_breakouts"]:
            if col not in hist_df.columns:
                hist_df[col] = None  # older entries recorded before the weekly timeframe was added
        hist_df = hist_df[[
            "date", "market", "universe_size", "eligible", "scanned",
            "breakouts", "near_breakouts", "weekly_breakouts", "weekly_near_breakouts",
        ]]
        hist_df.columns = [
            "Date", "Universe", "Total", "Eligible", "Scanned OK",
            "Breakouts (Daily)", "Near Breakouts (Daily)", "Breakouts (Weekly)", "Near Breakouts (Weekly)",
        ]
        st.dataframe(hist_df, hide_index=True, width="stretch")
        _export_buttons(hist_df, "scan_history", "scanhist")
    render_footer()


# ---------------------------------------------------------------------------
# Page: Strategy Settings
# ---------------------------------------------------------------------------

elif nav_page == "Strategy Settings":
    st.markdown("### ⚙️ Strategy Settings")
    st.caption("These controls drive every scan across the app — the same settings shown on Home/Scanner.")
    render_scan_parameters(button_key="run_scan_settings")
    st.write("")
    st.markdown("##### Fixed rules (not adjustable from the UI)")
    st.markdown(
        f"- Uptrend: price > SMA{config.SMA_FAST} > SMA{config.SMA_SLOW}\n"
        f"- Stop-loss buffer: {config.STOP_LOSS_BUFFER*100:.0f}% below the nearest support level\n"
        f"- Breakout Quality Score weights: {config.QUALITY_SCORE_WEIGHTS}"
    )
    render_footer()


# ---------------------------------------------------------------------------
# Page: Help & Support
# ---------------------------------------------------------------------------

elif nav_page == "Help & Support":
    st.markdown("### ❓ Help & Support")
    st.markdown(
        """
        **Data source**: This app uses `yfinance` (Yahoo Finance) only — there is no official NSE
        real-time feed. Quotes may be delayed or cached (EOD), especially for NSE tickers.

        **Signal definitions**: A stock qualifies as **Breakout** or **Near Breakout** only when it
        passes every configured condition at once (price floor, uptrend, volume spike, RSI range,
        and resistance proximity/breakout). These are strategy classifications, not investment advice.

        **Breakout Quality Score**: a deterministic 0-100 score from five measurable sub-scores
        (resistance strength, volume, RSI, trend, close strength) — documented in
        `indicators.compute_quality_score`. No randomness, no external AI scoring.

        **Watchlist**: persists across restarts (`data/watchlist.json`) — add/remove from any stock's
        detail panel.

        **Known limitations**: no exchange-holiday calendar for the Market Open/Closed badge; no
        reliable Nifty 500 index feed on Yahoo (Market Overview shows Nifty 50 for NSE markets
        instead); the "Breakout Period" filter applies to the main scan only, not to Watchlist/Search
        lookups (those always check the full 5D-3Y range).
        """
    )
    render_footer()
