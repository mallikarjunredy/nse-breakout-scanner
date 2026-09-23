"""Central configuration for this app's strategies. Most values here are
fixed (Upside Buy Movement has nothing user-adjustable); the
RISING_CHANNEL_* block is the one exception -- those are just the
*default* slider values shown on that strategy's page, since its own
spec explicitly asks for adjustable thresholds.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# How much history to download so the trend window, ATR/range-contraction
# lookbacks, and resistance windows are all fully covered.
HISTORY_PERIOD = "4y"
HISTORY_INTERVAL = "1d"

RSI_PERIOD = 14

# Minimum current price (in INR), applied to the Nifty 500 universe to
# filter out penny stocks. A stock priced at exactly MIN_PRICE_INR does
# NOT qualify -- only strictly above it does.
MIN_PRICE_INR = 100.0

# NSE trading hours in IST (used only for the OPEN/CLOSED badge; this does
# NOT account for market holidays, since no holiday calendar is wired up).
NSE_OPEN_HOUR, NSE_OPEN_MINUTE = 9, 15
NSE_CLOSE_HOUR, NSE_CLOSE_MINUTE = 15, 30

SCAN_HISTORY_MAX_ENTRIES = 200

# Streamlit auto-refresh cadence (ms) and matching scan cache TTL (s).
AUTOREFRESH_INTERVAL_MS = 5 * 60 * 60 * 1000
SCAN_CACHE_TTL_SECONDS = 5 * 60 * 60

# How many worker threads to use when fetching company info (name/sector)
# for the (small) list of stocks that pass the technical filters.
PE_FETCH_WORKERS = 8

# "Upside Buy Movement" strategy: a pre-breakout consolidation scanner.
# Every threshold here is a fixed rule from that strategy's definition,
# not a user-adjustable scan parameter -- see src/pre_breakout.py.
PRE_BREAKOUT_EMA_FAST = 20
PRE_BREAKOUT_EMA_SLOW = 50
PRE_BREAKOUT_RSI_MIN = 50.0
PRE_BREAKOUT_RSI_MAX = 65.0
PRE_BREAKOUT_TREND_LOOKBACK_DAYS = 60  # ~2-3 months, split in half for higher-high/higher-low
PRE_BREAKOUT_RESISTANCE_LOOKBACKS = (20, 40, 60)
PRE_BREAKOUT_NEAR_RESISTANCE_PCT = 5.0  # "within 0-5% below resistance" to be any kind of candidate
PRE_BREAKOUT_NEAR_VS_CONSOLIDATING_SPLIT_PCT = 2.0  # split point between the two candidate buckets
PRE_BREAKOUT_CONSOLIDATION_BAND_PCT = 8.0  # wider band used to count the consolidation *run length*
PRE_BREAKOUT_CONSOLIDATION_MIN_DAYS = 7
PRE_BREAKOUT_CONSOLIDATION_MAX_DAYS = 20
PRE_BREAKOUT_RANGE_RECENT_DAYS = 10
PRE_BREAKOUT_RANGE_PRIOR_DAYS = 20  # the 20 sessions immediately before the recent window
PRE_BREAKOUT_ATR_PERIOD = 14
PRE_BREAKOUT_ATR_RECENT_DAYS = 5
PRE_BREAKOUT_ATR_PRIOR_DAYS = 20
PRE_BREAKOUT_VOLUME_AVG_SHORT = 5
PRE_BREAKOUT_VOLUME_AVG_LONG = 20
PRE_BREAKOUT_RELATIVE_STRENGTH_DAYS = 20
PRE_BREAKOUT_BENCHMARK_INDEX = "^NSEI"  # Nifty 50 -- no reliable free Nifty 500 index feed on Yahoo

# "Daily Rising Channel: Pre-Breakout & Breakout Scanner" -- see
# src/rising_channel.py. These are *default* values for the page's
# adjustable-threshold widgets, not fixed rules -- unlike the
# PRE_BREAKOUT_* block above, the user can change all of these live.
RISING_CHANNEL_MIN_PRICE_INR = 50.0
RISING_CHANNEL_MIN_HISTORY_SESSIONS = 250
RISING_CHANNEL_PIVOT_N = 3  # candles required on each side to confirm a swing point
RISING_CHANNEL_LOOKBACK_MIN = 40
RISING_CHANNEL_LOOKBACK_MAX = 120
RISING_CHANNEL_LOOKBACK_STEP = 10  # granularity of the window search between MIN and MAX
RISING_CHANNEL_MIN_TOUCHES = 3  # per boundary
RISING_CHANNEL_MIN_TOUCH_SEPARATION = 5  # sessions between two touches on the same boundary
RISING_CHANNEL_TOUCH_TOLERANCE_ATR_MULT = 0.5
RISING_CHANNEL_PARALLEL_TOLERANCE_PCT = 30.0  # |slope_r - slope_s| / avg(|slope_r|,|slope_s|), as a %
RISING_CHANNEL_MIN_CONTAINMENT_PCT = 80.0  # % of closes that must sit within the channel band
RISING_CHANNEL_ATR_PERIOD = 14
RISING_CHANNEL_PREBREAKOUT_DISTANCE_MIN_PCT = 0.0
RISING_CHANNEL_PREBREAKOUT_DISTANCE_MAX_PCT = 3.0
RISING_CHANNEL_PREBREAKOUT_RSI_MIN = 50.0
RISING_CHANNEL_PREBREAKOUT_RSI_MAX = 65.0
RISING_CHANNEL_SMA_FAST = 20
RISING_CHANNEL_SMA_SLOW = 50
RISING_CHANNEL_SMA_RISING_LOOKBACK = 5  # sessions back, for the "both SMAs rising" check
RISING_CHANNEL_RANGE_RECENT_DAYS = 10  # optional filter: recent range narrower than prior range
RISING_CHANNEL_RANGE_PRIOR_DAYS = 30
RISING_CHANNEL_VOLUME_RECENT_DAYS = 5  # optional filter: volume contraction
RISING_CHANNEL_VOLUME_PRIOR_DAYS = 60
RISING_CHANNEL_BREAKOUT_MIN_PCT = 0.5  # "0.5% of resistance" minimum clearance
RISING_CHANNEL_BREAKOUT_ATR_MULT = 0.25
RISING_CHANNEL_BREAKOUT_VOLUME_MULT = 1.5
RISING_CHANNEL_BREAKOUT_VOLUME_AVG_PERIOD = 20

# "Daily Trend + Consolidation Breakout" -- see src/trend_consolidation.py.
# Like RISING_CHANNEL_*, these are adjustable *defaults*, not fixed rules.
TREND_CONSOL_MIN_PRICE_INR = 100.0
TREND_CONSOL_MIN_HISTORY_SESSIONS = 300
TREND_CONSOL_MIN_TRADED_VALUE_INR = 10_00_00_000.0  # ₹10 crore, close × volume, 20-session average
TREND_CONSOL_TRADED_VALUE_AVG_DAYS = 20
TREND_CONSOL_SMA_FAST = 50
TREND_CONSOL_SMA_SLOW = 200
TREND_CONSOL_SMA_FAST_RISING_LOOKBACK = 10  # sessions back, for the "SMA50 rising" check
TREND_CONSOL_RELATIVE_STRENGTH_DAYS = 63
TREND_CONSOL_CONSOLIDATION_PERIOD = 15  # sessions, excluding the evaluation candle
TREND_CONSOL_MAX_WIDTH_PCT = 8.0
TREND_CONSOL_PREBREAKOUT_DISTANCE_MIN_PCT = 0.0
TREND_CONSOL_PREBREAKOUT_DISTANCE_MAX_PCT = 3.0
TREND_CONSOL_BREAKOUT_BUFFER_PCT = 0.5  # close must clear resistance by at least this %
TREND_CONSOL_VOLUME_AVG_DAYS = 20  # excluding the signal candle
TREND_CONSOL_VOLUME_MULTIPLIER = 1.5
TREND_CONSOL_ATR_PERIOD = 14
TREND_CONSOL_BENCHMARK_INDEX = "^NSEI"  # Nifty 50

# "Bullish Recovery Above EMAs" -- see src/bullish_recovery.py. A fixed-
# rule strategy like Upside Buy Movement: nothing here is user-adjustable.
BULLISH_RECOVERY_EMA_FAST = 10
BULLISH_RECOVERY_EMA_SLOW = 20
BULLISH_RECOVERY_RSI_MIN = 65.0
BULLISH_RECOVERY_MIN_PRICE_INR = 25.0  # this strategy's own floor -- MIN_PRICE_INR (₹100) takes precedence when higher
BULLISH_RECOVERY_MIN_MARKET_CAP_CR = 2000.0

# "Resistance Breakout" ("Previous Swing High Breakout with Volume
# Confirmation") -- see src/resistance_breakout.py. Adjustable defaults,
# like Rising Channel / Trend + Consolidation: unlike Upside Buy Movement
# or Bullish Recovery, this pattern's exact numbers are judgment calls,
# not a fixed spec.
RESISTANCE_BREAKOUT_MIN_PRICE_INR = 100.0
RESISTANCE_BREAKOUT_PIVOT_N = 3  # candles required on each side to confirm a swing point
RESISTANCE_BREAKOUT_LOOKBACK_DAYS = 130  # ~6 months, window to search for the "previous high"
RESISTANCE_BREAKOUT_MIN_HIGH_AGE_DAYS = 15  # previous high must be at least this many sessions old
RESISTANCE_BREAKOUT_MIN_PULLBACK_PCT = 8.0  # minimum decline off the previous high to count as a real base
RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MIN_PCT = 0.0
RESISTANCE_BREAKOUT_PREBREAKOUT_DISTANCE_MAX_PCT = 5.0
RESISTANCE_BREAKOUT_BREAKOUT_MIN_PCT = 0.5  # close must clear resistance by at least this %
RESISTANCE_BREAKOUT_VOLUME_MULT = 1.5
RESISTANCE_BREAKOUT_VOLUME_AVG_PERIOD = 20
# Momentum gate added at the user's request: excludes stocks that are just
# sitting flat/consolidating near the previous high with no real forward
# movement behind them (e.g. KIMS, Max Healthcare) -- close must be up at
# least this % over the trailing lookback for a ticker to qualify at all.
RESISTANCE_BREAKOUT_MOMENTUM_LOOKBACK_DAYS = 10
RESISTANCE_BREAKOUT_MIN_MOMENTUM_PCT = 3.0

# "Triple EMA Golden Cross" -- see src/triple_ema_golden_cross.py.
# Adjustable defaults, like Rising Channel / Trend + Consolidation /
# Resistance Breakout: this pattern came from a visual description, not
# a numbered spec, so its exact periods/thresholds are tunable defaults.
TRIPLE_EMA_MIN_PRICE_INR = 100.0
TRIPLE_EMA_FAST = 10
TRIPLE_EMA_MID = 20
TRIPLE_EMA_SLOW = 50
TRIPLE_EMA_GOLDEN_CROSS_LOOKBACK_DAYS = 90  # how far back to look for the fast-crosses-above-slow event
TRIPLE_EMA_RISING_LOOKBACK_DAYS = 5  # sessions back, for the "each EMA is rising" check
TRIPLE_EMA_RSI_MIN = 45.0
TRIPLE_EMA_RSI_MAX = 80.0
TRIPLE_EMA_BREAKOUT_VOLUME_MULT = 1.5
TRIPLE_EMA_BREAKOUT_VOLUME_AVG_PERIOD = 20

# Paper-trading / backtest defaults for the same strategy.
TREND_CONSOL_BACKTEST_INITIAL_EQUITY_INR = 10_00_000.0  # ₹10 lakh paper capital
TREND_CONSOL_BACKTEST_RISK_PCT = 0.5  # % of current equity risked per trade
TREND_CONSOL_BACKTEST_STOP_ATR_MULT = 2.0
TREND_CONSOL_BACKTEST_TARGET_RR_MULT = 2.0  # target = entry + this x the initial per-share risk
TREND_CONSOL_BACKTEST_MAX_HOLDING_SESSIONS = 20  # entry day counts as session 1
TREND_CONSOL_BACKTEST_ENTRY_GAP_MAX_PCT = 2.0  # skip entry if next open is more than this % above signal close
TREND_CONSOL_BACKTEST_BROKERAGE_PCT = 0.03  # per side, % of trade value
TREND_CONSOL_BACKTEST_TRANSACTION_CHARGES_PCT = 0.10  # STT/exchange/other charges, per side, % of trade value
TREND_CONSOL_BACKTEST_SLIPPAGE_PCT = 0.05  # per side, % of fill price
