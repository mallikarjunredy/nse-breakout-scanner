# NSE/NYSE Breakout Scanner

A Streamlit dashboard that scans a market universe — NSE Nifty 500, all of
NSE (via the Nifty Total Market list), or the US S&P 500 as a stand-in for
"NYSE-listed" — for upward-trending stocks that are near or breaking
through a resistance level, with confirming volume and momentum. Results
refresh automatically and are split into "Breakout" and "Near Breakout"
ranked tables, plus a persistent Watchlist, a stock-search box, market
overview, sector breakdown, and scan history. The sidebar's "🎯 Scan
Parameters" expander lets the user adjust every threshold live, without
editing code.

**Data source**: `yfinance` (Yahoo Finance) only. There is no official NSE
real-time feed integration. The UI always labels this explicitly ("📡 Data
Source: Yahoo Finance • Delayed / Cached / EOD data • Not official NSE
real-time feed") — never claim real-time/official NSE data anywhere in
the UI or docs.

## Running it

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\streamlit.exe run app.py --server.port 8501
```

Then open http://localhost:8501. Port **8501** is the Streamlit default;
change it with `--server.port <port>` if 8501 is taken.

These commands call the venv's `python.exe`/`streamlit.exe` directly
instead of activating the venv, deliberately. `.venv\Scripts\Activate.ps1`
is blocked on most Windows machines by the default PowerShell execution
policy (`running scripts is disabled on this system`), and changing that
policy is a machine-wide security setting not worth requiring just to run
this app. If you'd still rather activate the venv (e.g. for a longer
interactive session), run PowerShell as yourself and either:
- `powershell -ExecutionPolicy Bypass -File .venv\Scripts\Activate.ps1`, or
- set it once for your user: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

First run for each market fetches and caches the ticker list
(`data/nifty500.csv` / `data/nse_all.csv` / `data/sp500.csv`, refreshed
weekly). If that fetch fails (no internet, NSE/Wikipedia unreachable), a
small built-in fallback list is used instead — see `src/universe.py`.

## Architecture

- `app.py` — single-file Streamlit app with lightweight, in-script "page"
  navigation (a sidebar icon button list sets `st.session_state.nav_page`;
  an `if/elif` chain near the bottom of the file renders whichever page is
  active). This is deliberately NOT Streamlit's file-based multi-page
  routing (`st.navigation`/`st.Page`) -- a single script keeps the
  existing `st.session_state`-based scan/watchlist caching working
  unchanged, with none of the cross-page widget-state subtlety that
  routing would introduce. Pages: Home (dashboard summary), Scanner (full
  Breakout/Near Breakout/Watchlist tables + sector filter), Watchlist,
  Stock Analysis (search-driven detail view), Scan History, Strategy
  Settings, Exports, Help & Support. The header bar (logo, search box,
  market status/date badges), the scan/watchlist fetch, and
  `render_scan_parameters()` / `render_detail_panel()` / `render_footer()`
  all run before/independent of the page routing, so every page can use
  them. Auto-refreshes every `config.AUTOREFRESH_INTERVAL_MS` (currently
  5 hours) via `streamlit-autorefresh`.
- The "Scan Parameters" widgets use a **fixed set of `st.session_state`
  keys** (`param_market_label`, `param_rsi_range`, `param_volume_multiplier`,
  `param_near_pct`, `param_min_price`, `param_breakout_periods`), pre-seeded
  with defaults at the top of the script. `render_scan_parameters()` is
  called from multiple pages (Home, Scanner, Strategy Settings) using
  those same keys -- safe because only one page's branch actually executes
  per script run, so there's never a duplicate-widget-id collision. Do
  NOT pass `value=`/`index=`/`default=` alongside a `key=` that's already
  pre-seeded in `st.session_state` -- Streamlit will ignore/warn about the
  conflicting default.
- Home's "Top Breakout"/"Near Breakout" preview tables and the summary
  cards are rendered as hand-built HTML (`render_preview_table_html`,
  `render_summary_cards`) rather than `st.dataframe`/`st.metric` --
  needed for colored signal pills, a colored ticker style, and star
  watch-indicators, none of which `st.dataframe` can style per-cell. This
  sacrifices native row-click selection on the preview tables specifically
  (HTML has no Streamlit interactivity); a small selectbox below each
  preview table lets the user pick which of those rows to inspect (and
  each preview table has its own "⬇️" export popover, same as the full
  tables), and the *full* Scanner-page tables still use `st.dataframe`
  with real row-selection, unchanged. Every results table -- Home
  previews, Scanner's Breakout/Near Breakout/Watchlist, and the
  Watchlist page -- shows Buy Level, Support Level, Resistance Level, and
  Stop Loss; there's no separate "Exports" nav page (removed -- the
  per-table download icons plus a small "Export All" button in the
  sidebar cover it without a dedicated page). The selected-stock detail
  panel is a 3-column layout (chart+tabs | stock info card | Why-
  Qualified + Quality Score cards) built the same way, matching a
  supplied reference screenshot.
- `st.container(border=True)` (Market Overview / Sector Breakout Count on
  Home) is styled via `div[data-testid="stVerticalBlockBorderWrapper"]`
  in the global CSS -- that's Streamlit's actual test-id for a bordered
  container in this version. Each such card also gets an explicit
  `.card-header` (colored dot + title) so adjacent cards read as clearly
  separate, since the default border alone was too subtle against the
  dark gradient background to look independent.
- **Another bug class to watch for**: never write a bare ternary as a
  statement whose branches are Streamlit calls, e.g.
  `st.dataframe(x) if cond else st.caption(y)` on its own line. Streamlit's
  "magic" auto-display wraps any non-None top-level expression statement
  in `st.write()`; the ternary's *value* (the DeltaGenerator `st.dataframe`
  returns) gets passed to `st.write`, which falls through to `st.help()`
  for unrecognized objects and tries to `ast.parse` the single source
  line to recover a variable name -- and crashes if that line is a
  multi-line statement (e.g. one broken with `\`). Always use a real
  `if/else` block instead when the branches are Streamlit calls.
- **Known bug class to watch for**: any code path that changes
  `st.session_state.nav_page` mid-script (the search box's "jump to Stock
  Analysis" being the example that broke once) MUST call `st.rerun()`
  right after. The page-routing `if/elif` chain reads a **local**
  `nav_page` variable captured near the top of the script; without a
  rerun, that local variable stays stale for the rest of that same run,
  so the *old* page's widgets (e.g. Home's preview-table dropdowns) still
  execute and can silently overwrite state the navigation change just set.
- The "▶️ Run Scan" button doesn't call `scan_market` directly: it sets
  `st.session_state["_trigger_scan"] = True` and calls `st.rerun()`. The
  actual scan-or-reuse-cache decision happens once, unconditionally, near
  the top of the script (before page routing), where that flag is popped
  and treated the same as a cache-miss. This ordering matters because the
  button itself is rendered *after* that decision point in the page-
  specific branches.
- `src/config.py` — all tunable thresholds (resistance lookback windows,
  RSI/volume/near-breakout/min-price thresholds, refresh cadence, quality
  score weights, benchmark index tickers, market hours). Change values
  here rather than hardcoding elsewhere. Most of these are *defaults*
  only — the sidebar's Scan Parameters controls override them per scan
  (see `scan_market`'s keyword args in `src/scanner.py`); the scan cache
  key includes every one of them, so changing any control triggers a
  fresh scan instead of showing stale results.
- `src/universe.py` — loads and caches the ticker universe: Nifty 500 and
  Nifty Total Market (used as "all NSE stocks") from NSE's archives CSVs,
  S&P 500 scraped from Wikipedia. All cache to `data/*.csv` for 7 days.
- `src/indicators.py` — pure functions on a single ticker's OHLCV
  DataFrame: Wilder's RSI, volume-vs-20-day-average ratio, uptrend check
  (price > SMA50 > SMA200), resistance/support-signal detection across
  configurable lookback windows (5D, 1M, 3M, 6M, 1Y, 2Y, 3Y, or a
  user-chosen subset), and the deterministic Breakout Quality Score.
- `src/scanner.py` — orchestrates a full scan: bulk-downloads OHLCV for
  the whole universe in one `yfinance.download()` call (fast), evaluates
  every ticker against the filters, and only then fetches company info
  (name, sector, P/E — a slow per-ticker network call) for the small set
  of stocks that passed, keeping full-universe scans fast. Also exposes
  `evaluate_watchlist_ticker` / `get_watchlist_data` (always returns a
  row, even for non-qualifying stocks, with a Status + Reason) and
  `search_ticker` (on-demand lookup of any single ticker, in or out of
  the current universe, reusing the same evaluation logic).
- `src/detail.py` — on-demand deep-dive info for a single ticker (company
  fundamentals via `.info`, plus price history at a chosen period). Kept
  separate from `scanner.py` since these are slow per-ticker calls that
  must never run across the whole universe.
- `src/deep_dive.py` — on-demand deeper fundamentals for the selected
  stock's "📚 Deep Dive" expander: extended ratios (ROE, ROA, D/E,
  margins, growth, PEG), 5-year/quarterly Income Statement, Balance
  Sheet, and Cash Flow (curated line items via yfinance's
  `.financials`/`.balance_sheet`/`.cashflow`), major-holders snapshot,
  and recent news. This is explicitly NOT a Screener.in-equivalent --
  Yahoo Finance has no India-specific promoter/FII/DII shareholding-
  pattern trends, pledge %, concalls, credit ratings, or annual-report
  links, and the module's docstring and in-UI captions say so rather
  than implying otherwise. "Peers" in this tab means other same-sector
  stocks from *today's own scan results*, not a real industry peer list.
- `src/watchlist.py` — persistent watchlist storage (`data/watchlist.json`,
  a plain JSON list of tickers), independent of any scan. Survives app
  restarts, so a candidate added today is still there tomorrow even if it
  doesn't reappear in that day's scan results.
- `src/scan_history.py` — persistent log of past scans
  (`data/scan_history.json`, capped at `config.SCAN_HISTORY_MAX_ENTRIES`),
  written once per *actual* fresh scan (not on cache-hit reruns) via
  `record()`.
- `src/market_overview.py` — fetches the benchmark index snapshot (Nifty
  50 for NSE markets, S&P 500 for NYSE) for the Market Overview section.
  Advance/Decline counts are NOT computed here — they come from
  `scan_market`'s actual scanned universe, so they're never fabricated or
  inconsistent with what was really scanned.

## Timeframes: Daily and Weekly

Every scan runs the full strategy **twice per ticker**: once on daily
bars (as before) and once on weekly bars, resampled locally from the
same already-downloaded daily history (`scanner._resample_weekly` --
week ending Friday, `Open`=first/`High`=max/`Low`=min/`Close`=last/
`Volume`=sum). This adds no extra network calls or API load, just more
local computation. `scan_market` returns four result tables:
`breakout` / `near_breakout` (daily) and `weekly_breakout` /
`weekly_near_breakout`.

Weekly does NOT reuse the daily SMA50/SMA200 trend template -- on weekly
bars those would span ~1/~4 years and rarely trigger. It uses
`config.WEEKLY_SMA_FAST` (10) / `config.WEEKLY_SMA_SLOW` (40), the
classic weekly trend-template pairing, plus `config.WEEKLY_RESISTANCE_LOOKBACKS`
(1M-3Y in weeks instead of trading days) and `config.WEEKLY_VOLUME_AVG_PERIOD`
(10 weeks). `_evaluate_ticker(..., timeframe="daily"|"weekly")` selects
between the two conventions; `indicators.py`'s `is_uptrend`,
`find_resistance_signals`, `find_nearest_support`/`find_nearest_resistance`,
and `compute_quality_score` all accept optional `sma_fast`/`sma_slow`/
`lookbacks` overrides for this (defaulting to the daily config constants,
so existing daily-only callers are unaffected).

`app.py` has a "Timeframe" radio (Daily/Weekly) on both the Home and
Scanner pages that switches which pair of tables is displayed; the
summary cards always show daily counts (with weekly counts as a
secondary caption underneath), since they render before the toggle
exists on the page. The detail panel's `combined` lookup (for the
selected-stock analysis) includes all four tables plus the watchlist, so
a stock that qualifies only on the weekly timeframe still resolves
correctly. The Watchlist/search-box evaluation path
(`evaluate_watchlist_ticker`/`search_ticker`) is daily-only for now --
a deliberate scope decision, not a bug.

## Signal definitions

A stock must pass all of these to appear in either list:

- **Minimum price** (mandatory, all NSE markets): current price strictly
  greater than `config.MIN_PRICE_INR` (₹100 by default) — a price of
  exactly ₹100.00 does **not** qualify, only ₹100.01+ does. User-adjustable
  upward only (the sidebar control's `min_value` is pinned to the
  mandatory floor). Not applied to the NYSE market. See `_evaluate_ticker`
  in `src/scanner.py`.
- **Uptrend**: current price above its 50-day SMA, and 50-day SMA above
  its 200-day SMA (falls back to just price > SMA50 if <200 days of
  history exist).
- **Volume spike**: today's volume > the configured multiple (default 2x)
  of the prior 20-day average volume.
- **Momentum**: RSI(14) within the configured range, inclusive (default
  40-80 via the "RSI Range" slider — a tuple-valued `st.slider`, min and
  max both adjustable).
- **Resistance**: for at least one *selected* lookback window (5D-3Y by
  default, narrowable via the "Breakout Period" control), today's close
  is either above the prior high for that window (→ **Breakout**) or
  within the configured distance below it (→ **Near Breakout**, default
  3%).

If a stock qualifies as a breakout on any selected window, it's
classified as Breakout (the longest/most significant window is shown).
Otherwise, if it qualifies as near-breakout on any window, it's Near
Breakout (the closest window is shown). Ranking within each list weighs
volume ratio, RSI, and distance from/above the resistance level.

### Breakout Quality Score (0-100)

Deterministic, documented in `indicators.compute_quality_score` — five
sub-scores (each 0-100), combined via `config.QUALITY_SCORE_WEIGHTS`:
resistance breakout/proximity strength, volume-ratio strength, RSI's
distance from a healthy momentum sweet spot, trend strength (price vs
SMA50/SMA200), and today's close position within today's High-Low range.
No randomness, no external "AI score" — every input is a value already
computed elsewhere in the same scan.

### Why it qualified / why it didn't

Every Breakout/Near Breakout row carries a `Why Qualified` string (built
in `_evaluate_ticker`) — a `✓`-prefixed checklist of the actual conditions
that passed for *that* stock. Watchlist/search rows that do NOT fully
qualify instead carry a `Reason` string (`evaluate_watchlist_ticker`)
listing exactly which conditions are missing (e.g. "price ₹82.40 does not
satisfy Price > ₹100.00"). The detail panel in `app.py` shows whichever
of the two is present. Neither is invented — both are derived directly
from that scan's real computed values.

Each result row also carries reference levels for someone planning a
trade, computed purely from price history (not investment advice):
- **Buy Level**: for Breakout rows, the signal already triggered, so this
  is just the current price. For Near Breakout rows, it's the confirmed-
  breakout trigger price (resistance + 0.5% buffer).
- **Stop Loss**: `config.STOP_LOSS_BUFFER` (2%) below the nearest support
  level, or below the buy level if no support was found in any window.
- **Support Level**: the nearest prior swing low below the current price
  (mirrors resistance detection, but on Lows — see
  `indicators.find_nearest_support`).
- **52W High**: highest High over the trailing ~252 trading days,
  including today (`indicators.compute_52w_high`).

## Watchlist, search, and the shared detail panel

A third "⭐ Watchlist" tab shows a live snapshot (`scanner.get_watchlist_data`)
of whatever tickers are saved via `src/watchlist.py`, independent of the
selected market and of whether they currently pass the scan filters. Each
row gets a Status (🚀 Breakout / 👀 Near Breakout / ⏳ Watching / ⚠️ No data).
The Status badge only fires Breakout/Near Breakout when ALL scan criteria
pass together, not just the resistance condition alone — a stock near
resistance but missing the volume spike is "Watching", not "Breakout",
since it isn't actually in that day's real scan results.

The search box (`scanner.search_ticker`) looks up any single ticker on
demand — typed as "RELIANCE" or "RELIANCE.NS" — even if it's nowhere in
the current scan results, evaluating it under the live sidebar strategy
settings (including the mandatory price floor) and routing it into the
same detail panel via `st.session_state.selected_ticker` /
`st.session_state.search_result`.

Clicking a row in any of the three tables, or a successful search
(Streamlit's native dataframe row selection, `on_select="rerun"`), shows
a detail panel below: a ⭐ Watch / 🗑️ Unwatch button (writes to
`src/watchlist.py`), the Why-Qualified/Reason panel described above,
company fundamentals (market cap, EPS, dividend yield, beta, analyst
target/recommendation, business summary) from `detail.get_company_info`,
technical metrics (in a "Key Levels" tab), and a **Plotly candlestick**
chart (1M/3M/6M/1Y selector, "Price Chart" tab) from
`detail.get_price_history` with the row's Resistance/Support overlaid as
dashed reference lines. A third "Reason for Match" tab shows the
Why-Qualified/Reason text. Note: yfinance's
`dividendYield` field is already a percentage (e.g. `0.47` means 0.47%),
not a fraction — don't multiply by 100 when displaying it.

## Export

Each table (Breakout, Near Breakout, Watchlist) has a single "⬇️" icon
in its top-right corner (`_export_buttons` in `app.py`) that opens a
`st.popover` with CSV and Excel download buttons, plus the sidebar has a
"📥 Export All (Excel)" button that bundles all three into one multi-sheet
workbook. Excel export uses `openpyxl` via `pandas.ExcelWriter`.

## Market status, market overview, sector summary, scan history

- **Market OPEN/CLOSED badge** (`app.py`'s `_market_status`): based purely
  on exchange trading hours in the exchange's local timezone (IST for
  NSE/NSE_ALL, US/Eastern for NYSE) and weekday. Does **not** account for
  exchange holidays — no holiday calendar is wired up; this is called out
  in a caption next to the badge.
- **Market Overview**: benchmark index level/change (`market_overview.py`)
  plus Advances/Declines/Unchanged, computed by `scan_market` from the
  actual scanned universe's close-vs-prior-close (not the full exchange,
  and not fabricated).
- **Sector Breakout Summary**: `scanner.summarize_sectors` counts
  qualifying (Breakout + Near Breakout) rows per sector from that scan's
  real results.
- **Scan History**: `src/scan_history.py`, one entry per real fresh scan.

A sector multiselect above the result tabs filters what's *displayed* in
the Breakout/Near Breakout tables only — the summary cards above always
reflect the full, unfiltered scan counts.

## Windows TLS interception workaround

On some Windows machines (corporate proxy/antivirus doing TLS
inspection), `yfinance`'s HTTP backend (`curl_cffi`) fails with
`CERTIFICATE_VERIFY_FAILED` even though plain `requests` calls succeed,
because `curl_cffi` doesn't automatically trust the Windows certificate
store. `src/certs.py` works around this: on first run it builds a merged
CA bundle (Windows trusted roots + certifi) at `data/ca_bundle.pem` and
points `CURL_CA_BUNDLE`/`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` at it. This
runs automatically via `src/__init__.py` and is a no-op on non-Windows
platforms or machines that don't need it.

## Known limitations

- yfinance data is not truly real-time — expect ~15-20 minute delays,
  more so for NSE. "Near real-time" here means the dashboard re-scans and
  re-renders on its auto-refresh cadence, not that quotes are live tick
  data. This is disclosed explicitly in the UI (see "Data source" above).
- P/E ratio, company name, and sector all come from `yfinance`'s `.info`;
  fields can be `None`/"N/A" for stocks Yahoo doesn't report them for
  (e.g. loss-making companies, some smaller NSE names).
- Scanning the full Nifty 500 / All Stocks / S&P 500 takes real time
  (bulk OHLCV download plus per-candidate `.info` lookups) and can
  occasionally hit Yahoo Finance rate limits — if a scan comes back
  mostly empty, wait a bit and use "Run Scan" / "Refresh now" rather than
  repeatedly re-running it.
- The NSE archives and Wikipedia scrape are both unauthenticated public
  endpoints and can change format or block scripted requests without
  notice; if the universe fetch fails, cached or fallback lists are used
  (see `src/universe.py`), so counts may lag the true index composition.
- Market OPEN/CLOSED does not account for exchange holidays (see above).
- There is no true Nifty 500 index feed used for Market Overview (Yahoo
  doesn't reliably serve one); both NSE markets show the Nifty 50 index
  there instead, and this is not hidden or mislabeled.
- The Watchlist/search-box "not yet applied to a restricted Breakout
  Period" — `evaluate_watchlist_ticker`, `get_watchlist_data`, and
  `search_ticker` always check resistance across the full 5D-3Y window
  set, regardless of the sidebar's "Breakout Period" selection (which
  only narrows the main Breakout/Near Breakout scan). This is a
  deliberate scope decision, not a bug — flag it if you want it unified.
- No inline per-row "Watchlist Action" button in the result tables
  (would require switching from `st.dataframe` to `st.data_editor`,
  a bigger change with more risk of breaking existing selection/export
  behavior). Add/remove is done via the shared detail panel's Watch/
  Unwatch button instead, reachable by clicking any row.
- True per-cell color coding (green/orange backgrounds) isn't supported
  by `st.dataframe` while keeping row-selection interactive; the
  Signal/Status columns use emoji prefixes (🚀/👀/⏳) as the practical
  equivalent instead.

## Conventions

- Config/thresholds live only in `src/config.py`; don't hardcode numbers
  like the RSI or volume-spike threshold elsewhere.
- `src/indicators.py` functions take plain DataFrames/Series and return
  plain values — no Streamlit or I/O in that module, so it stays easily
  testable in isolation.
- Prefer Streamlit's `width="stretch"` over the deprecated
  `use_container_width=True` (removed after 2025-12-31).
