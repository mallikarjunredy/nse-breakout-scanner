"""Central configuration for the "Upside Buy Movement" strategy -- the
app's only strategy. Every threshold here is fixed (not user-adjustable
from the UI); change values here rather than hardcoding elsewhere.
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
