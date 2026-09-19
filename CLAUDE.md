# NSE Upside Buy Movement Scanner

A Streamlit dashboard with one strategy, **"Upside Buy Movement"** — a
pre-breakout consolidation scanner — run over two universes as two
separate pages: **"🎯 Upside Buy Movement"** (Nifty 500) and
**"💹 Upside Buy Movement above 100"** (all NSE stocks priced above
₹100). Both look for stocks that have NOT yet broken out but show the
technical fingerprint of a stock immediately before a strong breakout:
tight consolidation just under a meaningful resistance level,
contracting volatility and volume, and healthy (not overbought)
momentum. Results split into **Near Resistance**, **Consolidating**,
and **Already Broken Out** (reference only), plus a persistent
Watchlist, a stock-search box, a Market Overview (Nifty 50 / Bank Nifty
/ Sensex), Indian market news, and scan history.

There used to be a second, adjustable "Breakout / Near Breakout"
strategy with its own Strategy Settings draft/save page and Scanner
tabs. It was deliberately removed (not merely hidden) at the user's
request so this app has exactly one, fixed-rule strategy — see git
history before this change if that logic is ever needed again.

**Data source**: `yfinance` (Yahoo Finance) only. There is no official
NSE real-time feed integration. The UI always labels this explicitly
("📡 Data Source: Yahoo Finance • Delayed / Cached / EOD data • Not
official NSE real-time feed") — never claim real-time/official NSE data
anywhere in the UI or docs. The app never claims that any stock WILL
break out on the next session — it only reports today's measured
technical state.

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
this app.

On the user's own machine, `start_scanner.ps1` (launched hidden via a
Windows Startup-folder `.vbs`, since Task Scheduler creation was denied
on that machine) runs as a **self-healing watchdog**: it loops for the
whole logon session, checking every 30s whether something is listening
on port 8501, and relaunches Streamlit whenever it isn't -- so the app
recovers on its own after a crash or manual kill, with no need to ask
for a restart.

First run fetches and caches the Nifty 500 ticker list
(`data/nifty500.csv`, refreshed weekly). If that fetch fails (no
internet, NSE archives unreachable), a small built-in fallback list is
used instead — see `src/universe.py`.

## Architecture

- `app.py` — single-file Streamlit app with lightweight, in-script
  "page" navigation (a sidebar icon button list sets
  `st.session_state.nav_page`; an `if/elif` chain near the bottom of the
  file renders whichever page is active). This is deliberately NOT
  Streamlit's file-based multi-page routing (`st.navigation`/`st.Page`)
  -- a single script keeps the existing `st.session_state`-based
  scan/watchlist caching working unchanged. Pages: Home (Nifty-500-scoped
  dashboard summary), Upside Buy Movement and Upside Buy Movement above
  100 (full scan results for each universe: Near Resistance /
  Consolidating / Already Broken Out tabs), Watchlist, Stock Analysis
  (search-driven detail view), Scan History, Help & Support. The header
  bar (logo, search box, market status/date badges), the Nifty-500 scan/
  watchlist fetch, and `render_detail_panel()` / `render_footer()` all
  run before/independent of the page routing, so every page can use
  them; the "above 100" page runs its own separate, lazily-triggered
  scan (see "The strategy" section) since Home doesn't need it.
  Auto-refreshes every `config.AUTOREFRESH_INTERVAL_MS` (currently 5
  hours) via `streamlit-autorefresh`.
- **There is nothing user-adjustable.** Every threshold the strategy
  uses is a fixed constant in `src/config.py` (`PRE_BREAKOUT_*`). There
  is no Strategy Settings page, no draft/save flow, and no per-scan
  threshold overrides -- `pre_breakout.scan_pre_breakout()` takes only
  `min_price` (defaults to `config.MIN_PRICE_INR`), a progress callback,
  and `universe_name` (`"nifty500"` or `"all_nse"` -- see "The strategy"
  section for the two-page setup this drives). The scan result is cached in
  `st.session_state.pre_breakout_cache` as a bare `(timestamp, result)`
  tuple (not the old dict-keyed-by-parameters cache, since there are no
  varying parameters), refreshed on `config.SCAN_CACHE_TTL_SECONDS` TTL
  or when "▶️ Run Scan" sets `st.session_state["_trigger_scan"]`. This
  scan runs once, unconditionally, near the top of `app.py` (before page
  routing) so every page can use its `result`/`scan_time`.
- `src/scanner.py` holds only two generic, strategy-agnostic helpers:
  `download_history(tickers)` (bulk `yfinance.download`, one call for
  the whole universe) and `fetch_candidate_info(tickers)` (parallel
  per-ticker `.info` lookups for name/sector -- never run across a whole
  universe, only the small set of tickers that need it). Both are public
  (no leading underscore) since `src/pre_breakout.py` calls them across
  modules.
- `src/pre_breakout.py` is where the strategy actually lives:
  `_evaluate_ticker` (strict, all-or-nothing, used by the bulk scan),
  `evaluate_watchlist_ticker` (lenient, always returns a row with a
  Reason string explaining what's missing -- used by the Watchlist and
  search box), `search_ticker`/`get_watchlist_data` (thin wrappers around
  the lenient evaluator), and `scan_pre_breakout` (the bulk-scan
  orchestrator). See "The strategy" section below for the full rule set.
- Home's cards (`render_market_overview_card()`,
  `render_watchlist_overview_card()`, `render_latest_opportunities_card()`,
  `render_recent_scan_history_card()`) use plain `st.dataframe` inside
  `st.container(border=True)`, not hand-built HTML tables -- an earlier
  HTML-table version of preview tables hit two real bugs from static,
  unstyleable-by-Streamlit HTML: it needed a fixed, narrow column set to
  avoid horizontal scrolling, and its ⭐/☆ watch-indicator cells looked
  clickable but weren't (`st.markdown` has no click handler). Watch/
  Unwatch is done via the shared detail panel's button instead (select
  any row, then use the button there) -- no per-row watch toggle in the
  preview cards.
- `st.container(border=True)` cards are styled via
  `div[data-testid="stVerticalBlockBorderWrapper"]` in the global CSS --
  that's Streamlit's actual test-id for a bordered container in this
  version. Each such card also gets an explicit `.card-header` (colored
  dot + title) so adjacent cards read as clearly separate.
- **Bug class to watch for**: never write a bare ternary as a statement
  whose branches are Streamlit calls, e.g.
  `st.dataframe(x) if cond else st.caption(y)` on its own line. Streamlit's
  "magic" auto-display wraps any non-None top-level expression statement
  in `st.write()`; the ternary's *value* (the DeltaGenerator `st.dataframe`
  returns) gets passed to `st.write`, which falls through to `st.help()`
  for unrecognized objects and tries to `ast.parse` the single source
  line to recover a variable name -- and crashes if that line is a
  multi-line statement. Always use a real `if/else` block instead when
  the branches are Streamlit calls.
- **Bug class to watch for**: any code path that changes
  `st.session_state.nav_page` mid-script (the search box's "jump to Stock
  Analysis" being the example that broke once) MUST call `st.rerun()`
  right after. The page-routing `if/elif` chain reads a **local**
  `nav_page` variable captured near the top of the script; without a
  rerun, that local variable stays stale for the rest of that same run.
- **Bug class to watch for**: never write to a widget-bound
  `st.session_state` key from inside that same widget's own (or a later)
  button's click handler in the same script run -- by the time the
  button's `if st.button(...)` block executes, any widget above it has
  already instantiated for this run, and Streamlit raises
  `StreamlitWidgetAlreadyInstantiatedError` on a further write to its
  key. Set a "pending" flag and `st.rerun()` instead, applying the actual
  write at the top of the next run, before that widget exists again.
  (This is exactly why the old Strategy Settings' "Reset to Saved" button
  needed a `_pending_strategy_reset` flag before that whole feature was
  removed -- keep the pattern in mind if a similar reset-a-widget need
  comes up again.)
- The "▶️ Run Scan" button doesn't call `scan_pre_breakout` directly: it
  sets `st.session_state["_trigger_scan"] = True` and calls `st.rerun()`.
  The actual scan-or-reuse-cache decision happens once, unconditionally,
  near the top of the script (before page routing), where that flag is
  popped and treated the same as a cache-miss.
- `src/config.py` — every tunable constant (all `PRE_BREAKOUT_*`
  thresholds, `MIN_PRICE_INR`, refresh cadence, NSE trading hours). No
  per-scan overrides exist, so there's no cache key beyond the
  fixed `min_price` -- changing a value here changes behavior for every
  future scan, with no UI equivalent.
- `src/universe.py` — loads and caches the Nifty 500 ticker list from
  NSE's archives CSV (`get_nifty_500()`), falling back to a small
  built-in list if both the live fetch and the on-disk cache fail. This
  app is NSE-only (no NYSE universe -- removed along with the old
  strategy); `get_nse_all()` (NSE's "Nifty Total Market" list, ~750
  stocks) backs the "above 100" page's broader universe.
- `src/indicators.py` — four pure, generic indicator functions used by
  the strategy: `compute_rsi` (Wilder's RSI), `compute_ema`,
  `compute_atr` (Wilder's ATR), `compute_macd_histogram` (standard
  12/26/9 MACD histogram). Plain DataFrames/Series in, plain values out
  -- no Streamlit or I/O, so it stays easily testable in isolation.
- `src/detail.py` — on-demand deep-dive info for a single ticker (company
  fundamentals via `.info`, plus price history at a chosen period). Kept
  separate from the scan pipeline since these are slow per-ticker calls
  that must never run across the whole universe.
- `src/deep_dive.py` — on-demand deeper fundamentals for the selected
  stock's "📚 Deep Dive" expander: extended ratios (ROE, ROA, D/E,
  margins, growth, PEG), 5-year/quarterly Income Statement, Balance
  Sheet, and Cash Flow, major-holders snapshot, and recent news
  (`get_recent_news`, also reused directly for Home's "Indian Market
  News" caption by passing `config.PRE_BREAKOUT_BENCHMARK_INDEX` i.e.
  `^NSEI` instead of a stock ticker). Explicitly NOT a Screener.in-
  equivalent -- Yahoo Finance has no India-specific promoter/FII/DII
  shareholding-pattern trends, pledge %, concalls, credit ratings, or
  annual-report links. "Peers" means other same-sector stocks from
  *today's own scan results*, not a real industry peer list.
- `src/watchlist.py` — persistent watchlist storage (`data/watchlist.json`,
  a plain JSON list of tickers), independent of any scan. Survives app
  restarts, so a candidate added today is still there tomorrow even if it
  doesn't reappear in that day's scan results.
- `src/scan_history.py` — persistent log of past scans
  (`data/scan_history.json`, capped at `config.SCAN_HISTORY_MAX_ENTRIES`),
  written once per *actual* fresh scan (not on cache-hit reruns) via
  `record(result)`. Timestamps are formatted in IST explicitly
  (`datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Kolkata"))`) rather than
  the server's local time -- important since this app can run on a cloud
  host (Streamlit Community Cloud, UTC) where local-time formatting
  would silently show the wrong hour for NSE data. Also records
  `failures` (`universe_size - scanned`). Entries recorded under the old
  two-strategy app have different fields -- the Scan History page reads
  every field with `.get(...)`/a default rather than assuming it exists.
- `src/market_overview.py` — `get_indices_snapshot()` returns Nifty 50 /
  Bank Nifty / Sensex level + change%, skipping (never fabricating) any
  index whose data isn't available right now.

## The strategy: "Upside Buy Movement" (two universe variants)

The same rule set runs over two different universes, as two separate
pages with separate caches (`st.session_state.pre_breakout_cache` for
Nifty 500, `pre_breakout_all_cache` for the all-NSE variant) and
separate scan-history entries (distinguished by the `market` field --
"Nifty 500" vs "All NSE Stocks (₹100+)"):

- **"🎯 Upside Buy Movement"** -- Nifty 500 only.
- **"💹 Upside Buy Movement above 100"** -- all NSE stocks priced above
  ₹100 (NSE's broadest official list, "Nifty Total Market" via
  `universe.get_nse_all()`, ~750 stocks, as a practical stand-in for
  "all NSE stocks" -- same reasoning as the old two-strategy app's
  "All Stocks" market).

`pre_breakout.scan_pre_breakout(universe_name=...)` selects the loader
via `_UNIVERSE_LOADERS` (`"nifty500"` or `"all_nse"`) and stamps the
result with a human-readable `universe_label` used for both the page
caption and the Scan History `market` field. Both pages are otherwise
identical in structure (their own "▶️ Run Scan" button + TTL cache +
Near Resistance/Consolidating/Already Broken Out tabs); only Home's
cards are Nifty-500-scoped -- the "above 100" page has no Home
equivalent dashboard, by design, since nothing asked for one.

**Universe** (Nifty 500 variant): Nifty 500 only, with the mandatory
`config.MIN_PRICE_INR` (₹100) price floor -- a stock priced at exactly
₹100.00 does NOT qualify, only strictly above it does. The "above 100"
variant applies the same price floor to the broader all-NSE list.

**Every condition below must pass together** for a stock to appear as a
candidate at all (`pre_breakout._evaluate_ticker`):

- Close > EMA20 > EMA50, and both EMAs rising (compared to 5 trading
  days ago).
- Higher-high **and** higher-low over the trailing
  `PRE_BREAKOUT_TREND_LOOKBACK_DAYS` (60 trading days, ~3 months): that
  window is split into two halves, and the second half's max High must
  exceed the first half's, with the same for min Low. A deliberate
  simplification of full swing-point/zigzag detection -- documented as
  such rather than implying a more precise trend read.
- RSI(14) between `PRE_BREAKOUT_RSI_MIN`/`MAX` (50-65) -- healthy
  momentum, not overbought.
- MACD histogram (standard 12/26/9) is flat-or-improving vs. 4 days ago,
  or was positive at some point in the last 5 days.
- 5-day average volume < 20-day average volume (volume contraction).
- ATR% (`ATR/Close*100`) averaged over the last
  `PRE_BREAKOUT_ATR_RECENT_DAYS` (5) is lower than the same average over
  the `PRE_BREAKOUT_ATR_PRIOR_DAYS` (20) sessions before that.
- The last `PRE_BREAKOUT_RANGE_RECENT_DAYS` (10) sessions' High-Low range
  is narrower than the `PRE_BREAKOUT_RANGE_PRIOR_DAYS` (20) sessions
  immediately before that.
- A resistance level exists across the 20/40/60-day windows
  (`_find_resistance` -- prefers the shortest lookback whose level places
  today's close within the "meaningful" zone) with today's close within
  0-`PRE_BREAKOUT_NEAR_RESISTANCE_PCT` (5)% below it (or already through
  it -- see "Already Broken Out" below).
- If not already broken out: a "consolidation streak"
  (`_count_consolidation_days` -- trailing days, scanning backward,
  within a wider ±8% band of that resistance level) between
  `PRE_BREAKOUT_CONSOLIDATION_MIN_DAYS`/`MAX_DAYS` (7-20 sessions).
- Positive 20-day relative strength vs. **Nifty 50** (`^NSEI`), not a
  true Nifty 500 index -- no reliable free Nifty 500 index feed on
  Yahoo. Sector-index relative strength is intentionally NOT
  implemented -- no reliable per-stock-sector-to-index mapping is
  available from this data source.

**Three-way split** (mutually exclusive, computed together in one pass):

- **Already Broken Out**: close is already above the resistance level.
  Still requires the EMA/RSI/trend "quality" conditions (so it's showing
  "a similar setup that already moved," not just any stock above any
  level); the consolidation-length/range-contraction checks don't apply
  post-breakout and are skipped. Shown for reference/contrast only --
  explicitly excluded from being a pre-breakout candidate.
- **Near Resistance**: a full candidate within
  0-`PRE_BREAKOUT_NEAR_VS_CONSOLIDATING_SPLIT_PCT` (2)% of resistance --
  the closest, most immediate-looking setups.
- **Consolidating**: a full candidate further out, from that split point
  up to the 5% ceiling -- still building the base.

**Watchlist/search** (`evaluate_watchlist_ticker`) recomputes the same
metrics but never hard-filters -- it collects a `reasons` list for
anything that fails and always returns a row, with `Setup Status` one of
the three qualifying statuses, or `"⏳ Watching"` if anything is missing,
plus a `Reason` string listing exactly what's missing (mirrors the old
two-strategy app's watchlist convention). This is intentionally a
separate, more lenient code path from the strict bulk-scan evaluator --
some duplication between the two is accepted, matching how the app was
already structured before this strategy replaced the old one.

**Detail panel integration**: clicking a row sets
`st.session_state.selected_ticker` the same way every table in the app
does; `render_detail_panel()`'s `combined` lookup includes all three
result tables plus the watchlist snapshot. Rows carry their own
"Why Qualified" (✓-prefixed) or "Reason" (missing-conditions) string,
shown in the "Reason for Match" tab and the "Why it qualified?" card.
There is no Quality Score, Buy Level, Stop Loss, or 52W High for this
strategy (those were old-strategy concepts) -- the Key Levels tab shows
EMA20/EMA50/RSI/ATR%/Volume Ratio/Consolidation Days/Relative Strength
instead, and the price chart overlays only the Resistance level (no
Support line, since this strategy doesn't compute one).

## Third strategy: "Daily Rising Channel: Pre-Breakout & Breakout Scanner"

A third, independent strategy (`src/rising_channel.py`, page "📐 Daily
Rising Channel") -- unlike the two Upside Buy Movement pages, this one
is designed to be **adjustable from the UI** (an "⚙️ Adjust Thresholds"
expander with sliders for touch tolerance, parallelism tolerance,
containment %, lookback window, RSI range, distance-to-resistance band,
breakout volume multiplier, and minimum touch count, plus two optional
filter checkboxes). Its scan cache
(`st.session_state.rising_channel_cache`) is therefore keyed by
`(universe_name, tuple(sorted(params.items())))` -- changing any slider
is a cache miss and triggers a fresh scan on that combination, the same
auto-scan-on-cache-miss pattern the old (deleted) adjustable strategy
used. It is NOT logged to `scan_history` -- that log's schema
(`near_resistance`/`consolidating`/`already_broken_out` counts) belongs
to the Upside Buy Movement strategy and forcing this differently-shaped
strategy into it would mislabel the counts.

**Universe**: Nifty 500 or all NSE stocks (`universe.get_nse_all()`),
user's choice, with closing price > `config.RISING_CHANNEL_MIN_PRICE_INR`
(₹50 -- a different, lower floor than Upside Buy Movement's ₹100).
Downloads **split/dividend-adjusted** OHLC
(`scanner.download_history(tickers, auto_adjust=True)` -- yfinance's
standard back-adjustment method: prices before a split/dividend are
scaled down by the split ratio so the ex-date shows no artificial gap)
specifically so a stock split doesn't fake a channel breakdown; the
other two strategies still use raw (unadjusted) prices, unchanged.

**Channel detection** (`find_swing_points` / `_try_channel` /
`select_best_channel`):

- A swing high at position i requires High[i] to strictly exceed the
  High of `pivot_n` (default 3) bars on *both* sides -- swing low
  mirrors this on Low. A swing only exists in the result once `pivot_n`
  bars after it are present in the data passed in, which is what makes
  this leak-free: pass only data "as of" the date being evaluated, and
  the last `pivot_n` bars simply never produce a pivot yet.
- Searches window lengths from `lookback_min` to `lookback_max` (step
  `lookback_step`), every window ending at the same "as of" index, and
  fits a least-squares line to that window's swing highs (resistance)
  and swing lows (support).
- Hard requirements per window: both slopes positive; slopes parallel
  within `parallel_tolerance_pct` (relative difference vs. their
  average magnitude); at least `min_touches` swing points per boundary
  actually within `touch_tolerance_atr_mult × ATR14` of their fitted
  line, each pair at least `min_touch_separation` sessions apart;
  resistance strictly above support at every index in the window; at
  least `min_containment_pct` of closes within the tolerance-widened
  band.
- Among windows that pass, picks the highest deterministic `score`
  (touch count + containment % − fit-error % + a small bonus for the
  preferred higher-high/higher-low shape) -- see `_try_channel`'s
  docstring for the exact formula. No valid window -> `None` ("No valid
  channel"), and that ticker simply doesn't appear in any result table.

**Pre-Breakout Watchlist**: today's own channel (searched with
`as_of_idx` = today) with today's close at/below its projected
resistance, `prebreakout_distance_min/max_pct` (default 0-3%) below it,
close above rising SMA20 *and* SMA50 (vs. `sma_rising_lookback`, default
5, sessions ago), RSI(14) in `prebreakout_rsi_min/max` (default 50-65).
The two optional filters (10D range narrower than the preceding 30D;
5D avg volume below the preceding 60D avg) only apply when their
checkbox is on.

**Confirmed Breakout / Breakout — Volume Unconfirmed**: the channel is
searched with `as_of_idx` = *yesterday* and its slope/intercept frozen
-- "freezing" just means the same two numbers get reused to project a
resistance value at *today's* index, never refit including today's bar.
Requires yesterday's close at/below that frozen resistance (wasn't
already through it), and today's close above the *projected* resistance
by at least `max(breakout_min_pct% of resistance, breakout_atr_mult ×
yesterday's ATR14)`. If today's volume is also >= `breakout_volume_mult`
times the prior `breakout_volume_avg_period`-session average (today
excluded), it's a **Confirmed Breakout**; the identical price condition
without that volume confirmation is a **Breakout — Volume Unconfirmed**
row instead -- both computed in the same pass, never silently dropped.
A wick above resistance with a close below it fails the close-based
price condition and produces neither row, by construction (High is
never compared to resistance for this decision, only Close).

**Charting** (`rising_channel.build_channel_chart`): a 3-row Plotly
subplot (price+lines+swings+SMA20/50 / Volume / RSI14), built on demand
for whichever row the user selects (`st.session_state.rc_selected_ticker`
-- a page-local selection, deliberately NOT the shared
`selected_ticker`/`render_detail_panel()` used by the other two
strategies, since their chart only draws a flat resistance line and has
no concept of sloped channels or swing markers). The channel/signal-index
data needed to draw it comes from the scan result's `channels`/
`signal_idx` dicts (keyed by ticker); the underlying OHLC for the chart
itself is fetched on demand for just that one ticker
(`scanner.download_history([ticker], auto_adjust=True)`) rather than
kept in memory for the whole scanned universe. The resistance/support
lines are drawn out to whichever is later, the channel's own fitted end
or the signal index, so a frozen (yesterday-fit) breakout channel's
projected level at the signal candle is visible, not just its
historical fit -- and, since the lines are stored as fixed slope/
intercept numbers from the moment of the scan, a historical signal's
chart never changes shape when later data arrives.

This strategy is not integrated with the Watchlist/search box (those
remain scoped to `pre_breakout.py`'s Upside Buy Movement evaluator) and
is not logged to Scan History, both deliberate scope decisions given
how differently shaped its rows and its "channel" concept are from the
other two strategies' rows.

## Fourth strategy: "Daily Trend + Consolidation Breakout" (+ its backtest)

`src/trend_consolidation.py`, two pages: "🧭 Daily Trend + Consolidation"
(live scan) and "🧪 Trend + Consolidation Backtest" (walk-forward paper
trading over the same rules). Adjustable from the UI, like Rising
Channel; unlike it, resistance/support here is a **flat** number (the
highest High / lowest Low over a fixed trailing window), not a fitted
sloped line -- much simpler geometry, but the same causal-computation
discipline matters just as much because this strategy also drives a
real backtest.

**The one core design decision that makes both the live scan and the
backtest correct and consistent**: `_compute_signal_frame(df, ...)` is
a single vectorized function, called identically by both. Every rolling
calculation is `.shift(1)` *before* `.rolling(...)`, so day T's own bar
never contributes to its own resistance/support/volume-baseline/ADTV --
this is what "never include the breakout candle when calculating its
resistance" means in code, and it holds for every single day in a
ticker's history at once, not just "today." The live scan just reads
the last row of this frame; the backtest (`generate_backtest_signals`)
reads every historical `confirmed_breakout_ok == True` row. There is
exactly one place the strategy's rules live.

**Live scan requirements** (all must hold, `_compute_signal_frame`):
Nifty 50 close > its own SMA200 (`benchmark_available=False` and *zero*
candidates shown, never a silent pass, if the index download fails);
stock close > SMA50 > SMA200, SMA50 above its value
`sma_fast_rising_lookback` (10) sessions ago; stock's `relative_strength_days`
(63) -session return > Nifty 50's own; 20-session average traded value
(Close × Volume, shift(1)'d) > `min_traded_value` (₹10 crore); resistance/
support = max(High)/min(Low) over the `consolidation_period` (15)
sessions strictly before today; consolidation width % ≤ `max_width_pct`
(8%). **Pre-Breakout Watchlist** adds: close ≤ resistance, distance
between `prebreakout_distance_min/max_pct` (0-3%). **Confirmed
Breakout** adds: yesterday's close ≤ resistance (checked explicitly per
the spec, though for this fixed-window definition it's true by
construction), close ≥ resistance × (1 + `breakout_buffer_pct`/100),
volume ≥ `volume_multiplier` × the preceding 20-session average
(signal day excluded). The identical price condition without the volume
confirmation is **Price Breakout — Volume Unconfirmed** instead of
being dropped.

**Backtest** (`generate_backtest_signals` / `run_backtest` /
`compute_metrics` / `run_full_backtest`): every historical Confirmed
Breakout date becomes a candidate entry at the *next* session's open,
skipped if that open is > `entry_gap_max_pct` (2%) above the signal
close or at/below the signal's resistance. Position size risks
`risk_pct` (0.5%) of *current* equity per trade, capped by available
cash (no leverage); stop = entry − `stop_atr_mult` (2) × signal-day
ATR14; target = entry + `target_rr_mult` (2) × the initial per-share
risk; time-exit at the close of session `max_holding_sessions` (20,
entry day = session 1). Gap-through-the-stop exits at the day's open,
not the unreachable stop price. A day with both stop and target touched
(High ≥ target AND Low ≤ stop) has no way to know the true intra-day
order from daily OHLC alone -- resolved as a stop-out and flagged
(`ambiguous_stop_target=True`, surfaced in the UI and counted in
`compute_metrics`'s `ambiguous_count`) rather than guessed either way.
One open position per ticker; when several signals compete for cash on
the same entry day, the deterministic (documented, not random)
allocation order is strongest volume ratio first, ticker alphabetical
as the tie-break. Brokerage/transaction-charges/slippage are each a
configurable % applied per side. The development and out-of-sample
periods run as **two fully independent simulations** (each starting
fresh from `initial_equity`) specifically so a position opened near the
boundary date can never blend dev-period state into the out-of-sample
report.

**Survivorship bias, disclosed not fixed**: the backtest applies
*today's* Nifty 500 / All-NSE membership list to every past date, since
no free historical point-in-time membership snapshot exists for either
index. A stock removed from the index during the backtest window is
still treated as a member for dates before its removal (and a stock
added recently is treated as a member even before it actually joined).
The backtest page states this plainly rather than attempting to correct
for it. Similarly, the UI states outright that these are "a starting
hypothesis, not a proven profitable strategy" and never reports a
"success rate" framed as a probability of profit -- the live scan's
"Why Qualified" explains which rules matched, nothing more.

## Branding and header

The app is branded "🕉️ Lifeline Trade" (top-left header, `.app-logo-title`)
with subtitle "NSE Market Terminal · Delayed Data" -- replacing the old
"📊 NSE Scanner" title. The Om symbol (🕉️) is a deliberate placeholder for
a requested "small, respectful Lord Ganesh icon": no image-generation or
asset-sourcing tool was available, and sourcing a random web image for
religious iconography without the user's explicit sign-off wasn't
appropriate. Swap it for a real supplied icon file if/when the user
provides one.

## Top ticker tape

`render_ticker_tape()` (in `app.py`, defined right after `watchlist_tickers
= watchlist.load()` since it's called immediately below the header --
before the page-routing functions further down the file are even defined)
renders a horizontally-scrollable strip (`.ticker-tape-wrap`/`.ticker-chip`
CSS) below the header **on every page**: Nifty 50, Bank Nifty, Sensex
first (`market_overview.get_indices_snapshot()`), then the user's own
watchlist tickers (`market_overview.get_ticker_tape_quotes()`), green/red
per instrument via `.tt-positive`/`.tt-negative`. Both queries are wrapped
in `_get_ticker_tape_data()`, a `@st.cache_data(ttl=300)` function keyed on
`tuple(watchlist_tickers)` -- a short TTL independent of
`config.SCAN_CACHE_TTL_SECONDS`, since the tape doesn't need to wait on a
full universe scan to refresh. The strip itself is plain, non-clickable
HTML (see the "fake-clickable HTML" bug class above); a separate
`st.selectbox` ("Jump chart to instrument", key `ticker_tape_jump`) right
below it is the actual click-to-load-chart control -- picking a name
resolves it via `_resolve_chart_symbol()` and sets
`st.session_state.chart_symbol`, then reruns.

## Home page layout: terminal-style split

Top to bottom:

1. `render_home_header()` -- welcome message, the "Discipline today,
   better trades tomorrow" tagline, and a meta line showing the data
   source plus the *scan* timestamp and the underlying *price data*
   date **separately**, both in IST. If the universe's last daily
   candle's date equals today's IST date *and* the market is currently
   open, an inline warning notes that candle may still be forming.
2. A resizable two-column split (`st.slider("↔️ Dashboard / Chart width",
   key="home_split_pct")` driving `st.columns([split_pct, 100 -
   split_pct])`, default 50/50, 30-70 range in steps of 5) -- the
   practical substitute for a true pixel drag-to-resize divider, which
   pure Streamlit can't do without a custom JS component. Narrower
   screens stack the two columns automatically (Streamlit's own
   responsive behavior), so nothing is hidden there.
   - **Left column**: the existing dashboard cards, stacked vertically --
     `render_market_overview_card()`, `render_watchlist_overview_card()`,
     `render_fifty_two_week_card()` (New 52W High/New 52W Low tabs, from
     `src/fifty_two_week.py`, computed inside `pre_breakout.scan_pre_breakout()`
     off the already-downloaded universe history), `render_latest_opportunities_card()`
     (top 10 Near-Resistance + Consolidating candidates plus "📰 Indian
     Market News"), `render_recent_scan_history_card()` (last 5 scan_history
     entries).
   - **Right column**: `render_chart_pane()` inside its own
     `st.container(border=True)` -- a live candlestick/line chart with a
     synced RSI(14) panel. Defaults to `^NSEI` (Nifty 50), daily interval.
3. The usual divider + "🔎 Selected Stock Analysis" + `render_detail_panel()`
   (unchanged, full-width, below the split -- selecting a row from any
   dashboard card still populates this the same way it always has).

Home still doesn't show the mandatory-price-floor pill, the "View Full
Results"/"Run Scan" shortcut row, or the four summary tiles -- that
removal (see prior entry, `render_home_top_controls()`/
`render_home_summary_tiles()`) stands; nothing in this redesign brought
them back.

### Chart pane (`render_chart_pane()`, `_build_chart_figure()`, `_fullscreen_chart_dialog()`)

A right-aligned toolbar, left-to-right: **Symbol search** (free-text,
resolved via `_resolve_chart_symbol()` -- index name aliases like "NIFTY
50"/"BANK NIFTY"/"SENSEX" map to `^NSEI`/`^NSEBANK`/`^BSESN`, anything
else falls back to `pre_breakout.normalize_ticker()`, i.e. NSE stocks) →
**Timeframe** (`chart_interval`: 5 Minute / Daily / Weekly / Monthly /
Yearly, via `detail.INTERVAL_LABELS`) → **Range** (`chart_range_<interval>`,
options vary per interval via `detail.RANGE_OPTIONS_BY_INTERVAL` -- e.g.
5-Minute only offers 5D/1M since Yahoo doesn't keep intraday bars longer
than that) → **Chart type** (Candlestick / Line) → **Indicators** popover
(a single "RSI (14)" checkbox, default **on**; no EMA/volume/other
overlay options exist here by design) → **⛶ Full Screen** button, which
opens `_fullscreen_chart_dialog()` (`@st.dialog("Full Screen Chart",
width="large")`, a taller copy of the same figure).

`detail.get_chart_data(ticker, interval, range_key)` is the data source:
5-Minute/Daily hit yfinance's native intervals directly; Weekly/Monthly/
Yearly are **locally resampled from daily EOD data**
(`_RESAMPLE_RULE = {"1wk": "W-FRI", "1mo": "ME", "1y": "YE"}`) since Yahoo
has no native interval that reaches back far enough for those (or, for
yearly, at all) -- real aggregated data, not fabricated, but disclosed via
a `note` string shown as a caption under the chart whenever it applies.
An empty result (bad symbol, no data for that interval/range) shows
`st.warning(note)` and returns -- never a fabricated candle.

`_build_chart_figure()` builds a **light/white-themed** `plotly_white`
figure (`#FFFFFF` background, `#EDEDED` grid) -- deliberately different
from the rest of the app's dark theme, matching the terminal screenshot's
clean chart look. Candlesticks are green `#26A69A`/red `#EF5350`; when
RSI is on, a `make_subplots(rows=2, row_heights=[0.75, 0.25])` puts a
purple (`#8E24AA`) RSI(14) line in the bottom 25%, with a shaded
`add_hrect(y0=30, y1=70)` band and dotted 30/70 reference lines, x-axes
shared so pan/zoom/crosshair stay in sync between the two rows. No EMA
lines, moving averages, volume bars, or buy/sell/order overlays are drawn
here -- those belong to the strategy detail-panel chart
(`render_detail_panel()`'s own `tab_chart`, unchanged), not this
general-purpose instrument chart.

Drawing tools come from Plotly's own modebar (`config=
{"modeBarButtonsToAdd": ["drawline", "drawrect", "drawopenpath",
"eraseshape"], "scrollZoom": True}`) -- trend line, rectangle, freehand,
and erase, plus Plotly's native pan/zoom/crosshair-on-hover. This is the
"practical version" scope the user explicitly signed off on in place of
the full spec: it does **not** include Fibonacci retracement, parallel
channel, a text tool, measurement tools, lock/hide toggles, undo/redo, a
separate right-edge toolbar (Plotly's modebar stays in its own default
position, not repositioned to the page's right edge), or per-instrument
persisted drawings (drawings reset when the figure rebuilds on any
control change, since nothing saves them) -- none of those are buildable
in pure Streamlit+Plotly without embedding a custom JS component, and the
user chose to ship everything else now rather than block on that.

## Export

Each result table (the three Upside Buy Movement tabs, and Watchlist)
has a single "⬇️" icon in its top-right corner (`_export_buttons` in
`app.py`) that opens a `st.popover` with CSV and Excel download buttons,
plus the sidebar has a "📥 Export All (Excel)" button that bundles the
three scan tables + the watchlist snapshot into one multi-sheet
workbook. Excel export uses `openpyxl` via `pandas.ExcelWriter`.

## Market status and market overview

- **Market OPEN/CLOSED badge** (`app.py`'s `_market_status`): based
  purely on NSE trading hours in IST and weekday. Does **not** account
  for exchange holidays -- no holiday calendar is wired up; this is
  called out in the header's data-source popover.
- **Market Overview** (Home): Nifty 50, Bank Nifty, and Sensex level +
  change%, independent of the strategy's own Nifty 50 relative-strength
  calculation (same index, different purpose -- display vs. a filter
  condition).

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

- yfinance data is not truly real-time — expect ~15-20 minute delays.
  "Near real-time" here means the dashboard re-scans and re-renders on
  its auto-refresh cadence, not that quotes are live tick data.
- Company name/sector come from `yfinance`'s `.info`; fields can be
  `None`/"N/A" for stocks Yahoo doesn't report them for.
- Scanning the full Nifty 500 (bulk OHLCV download plus per-candidate
  `.info` lookups) takes real time and can occasionally hit Yahoo
  Finance rate limits — if a scan comes back mostly empty, wait a bit
  and use "▶️ Run Scan" rather than repeatedly re-running it. This
  strategy's condition set is intentionally strict (every condition
  must pass together), so a handful of matches out of 500 scanned is
  the expected common case, not a bug.
- The NSE archives endpoint is an unauthenticated public endpoint and
  can change format or block scripted requests without notice; if the
  universe fetch fails, the cached or fallback list is used (see
  `src/universe.py`), so the count may lag the true index composition.
- Market OPEN/CLOSED does not account for exchange holidays (see above).
- Relative strength is measured against the Nifty 50, not a true Nifty
  500 index (no reliable free feed for one on Yahoo); sector-index
  relative strength is not implemented.
- No inline per-row "Watchlist Action" button in the result tables
  (would require switching from `st.dataframe` to `st.data_editor`, a
  bigger change with more risk of breaking existing selection/export
  behavior). Add/remove is done via the shared detail panel's Watch/
  Unwatch button instead, reachable by clicking any row.

## Conventions

- Config/thresholds live only in `src/config.py`; don't hardcode numbers
  elsewhere.
- `src/indicators.py` functions take plain DataFrames/Series and return
  plain values — no Streamlit or I/O in that module, so it stays easily
  testable in isolation.
- Prefer Streamlit's `width="stretch"` over the deprecated
  `use_container_width=True` (removed after 2025-12-31).
- **Tag every feature commit.** After committing and pushing a feature
  (a new strategy, a page, a meaningful behavior change -- not every
  tiny copy/threshold tweak), create an annotated git tag on that commit
  and push it too: `git tag -a v<NN>-<short-kebab-name> <commit> -m "<what
  it added>"` then `git push origin --tags`. Numbers increment from
  whatever the highest existing `vNN-*` tag is (currently up to
  `v17-rsi-chart-panel`). This is a standing user preference so GitHub
  always has a named, permanent checkpoint for "the app right after
  feature X" -- distinct from Streamlit Community Cloud's deploy, which
  only ever tracks `master`'s moving HEAD and has no per-feature
  versioning of its own.
