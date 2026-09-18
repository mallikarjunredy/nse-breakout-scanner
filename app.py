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
from streamlit_autorefresh import st_autorefresh

from src import config, deep_dive, detail, market_overview, pre_breakout, scan_history, watchlist

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


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "pre_breakout_cache" not in st.session_state:
    st.session_state.pre_breakout_cache = None  # (timestamp, result) once a scan has run, else None
if "pre_breakout_all_cache" not in st.session_state:
    st.session_state.pre_breakout_all_cache = None  # same, for the "above 100" all-NSE strategy
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


# ---------------------------------------------------------------------------
# Header bar
# ---------------------------------------------------------------------------

status_color = "#3ECF8E" if market_is_open else "#FF6B6B"

h_left, h_mid, h_right = st.columns([2.2, 3, 2.6])
with h_left:
    st.markdown(
        '<p class="app-logo-title">📊 NSE Scanner</p>'
        '<p class="app-logo-sub">Upside Buy Movement</p>',
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
    scan_label = datetime.datetime.fromtimestamp(scan_time, tz=IST).strftime("%d %b %Y, %I:%M %p IST")
    asof = result.get("data_asof_date")
    partial_note = ""
    if asof:
        asof_label = asof.strftime("%d %b %Y")
        today_ist = datetime.datetime.now(IST).date()
        if asof == today_ist and market_is_open:
            partial_note = (
                ' <span style="color:#FFB020;">⚠️ Today\'s session is still open — the latest daily '
                "candle may still be forming, not a confirmed close.</span>"
            )
    else:
        asof_label = "N/A"

    st.markdown(
        '<div class="home-hero">'
        '<div class="home-hero-title">👋 Welcome, Mallikarjun</div>'
        '<div class="home-hero-sub">Your Upside Buy Movement pre-breakout watchlist for the Nifty 500.</div>'
        '<div class="home-hero-tagline">📈 Discipline today, better trades tomorrow.</div>'
        '<div class="meta-text" style="text-align:left; margin-top:0.4rem;">'
        f"📡 Yahoo Finance · End-of-day data &nbsp;·&nbsp; Prices as of {asof_label} close "
        f"&nbsp;·&nbsp; Scan: {scan_label}{partial_note}</div>"
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


def render_home_top_controls():
    with st.container(border=True):
        pill_col, cta_col, run_col = st.columns([2, 1.4, 1.4])
        with pill_col:
            st.markdown(
                f'<div class="condition-pill">🔽 Closing price &gt; {CURRENCY}{MIN_PRICE:.2f} · Nifty 500</div>',
                unsafe_allow_html=True,
            )
        with cta_col:
            if st.button("🎯 View Full Results", key="home_open_results", width="stretch", type="primary"):
                st.session_state.nav_page = "Upside Buy Movement"
                st.rerun()
        with run_col:
            if st.button("▶️ Run Scan", key="home_run_scan_top", width="stretch"):
                st.session_state["_trigger_scan"] = True
                st.rerun()
        st.caption(
            f"{result['universe_size']} instruments in the Nifty 500 selection — may differ slightly from "
            f"a round number due to index reconstitution or data gaps. Scanned OK: {result['scanned']}."
        )


def render_home_summary_tiles():
    tiles = [
        ("🎯", "#4FD1E822", "#4FD1E8", len(result["near_resistance"]), "Near Resistance", "Upside Buy Movement"),
        ("📦", "#B26BFF22", "#B26BFF", len(result["consolidating"]), "Consolidating", "Upside Buy Movement"),
        ("🚀", "#FF6B6B22", "#FF6B6B", len(result["already_broken_out"]), "Already Broken Out", "Upside Buy Movement"),
        ("✅", "#3ECF8E22", "#3ECF8E", result["scanned"], "Successfully Scanned", None),
    ]
    cols = st.columns(4)
    for col, (icon, bg, color, value, label, nav_target) in zip(cols, tiles):
        with col:
            with st.container(border=True):
                st.markdown(
                    f'<div class="metric-icon" style="background:{bg}; color:{color};">{icon}</div>'
                    f'<div class="metric-value">{value}</div><div class="metric-label">{label}</div>',
                    unsafe_allow_html=True,
                )
                if nav_target:
                    if st.button("View →", key=f"tile_{label}", width="stretch"):
                        st.session_state.nav_page = nav_target
                        st.rerun()


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
                fig = go.Figure(data=[go.Candlestick(
                    x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
                    increasing_line_color="#3ECF8E", decreasing_line_color="#FF6B6B", name=ticker,
                )])
                if tech is not None and pd.notna(tech.get("Resistance Level")):
                    fig.add_hline(
                        y=tech["Resistance Level"], line_dash="dash", line_color="#FF6B6B",
                        annotation_text=f"Resistance: {CURRENCY}{tech['Resistance Level']:.2f}",
                        annotation_position="top left", annotation_font_color="#FF6B6B",
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
    render_home_top_controls()
    st.write("")
    render_home_summary_tiles()
    st.write("")

    ov_col, wl_col = st.columns(2)
    with ov_col:
        render_market_overview_card()
    with wl_col:
        render_watchlist_overview_card()

    # Default to the #1 candidate so the analysis section below is never
    # empty -- the user can still pick any other row via the tables above.
    if not st.session_state.selected_ticker:
        if not result["near_resistance"].empty:
            st.session_state.selected_ticker = result["near_resistance"].iloc[0]["Ticker"]
        elif not result["consolidating"].empty:
            st.session_state.selected_ticker = result["consolidating"].iloc[0]["Ticker"]

    st.write("")
    render_latest_opportunities_card()
    st.write("")
    render_recent_scan_history_card()

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

        **Watchlist**: persists across restarts (`data/watchlist.json`) — add/remove from any stock's
        detail panel.

        **Known limitations**: no exchange-holiday calendar for the Market Open/Closed badge; relative
        strength is measured against the Nifty 50, not a true Nifty 500 index (Yahoo has no reliable free
        feed for one); sector-index relative strength is not implemented.
        """
    )
    render_footer()
