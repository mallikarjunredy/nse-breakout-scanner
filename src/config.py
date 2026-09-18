"""Central configuration and tunable thresholds for the scanner."""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Resistance lookback windows: label -> trading days.
# Trading-day approximations: ~21/month, ~252/year.
RESISTANCE_LOOKBACKS = {
    "5D": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
    "2Y": 504,
    "3Y": 756,
}

# How much history to download so the longest lookback (3Y) plus the
# indicator warm-up period (SMA200) are both fully covered.
HISTORY_PERIOD = "4y"
HISTORY_INTERVAL = "1d"

RSI_PERIOD = 14
RSI_THRESHOLD = 50

VOLUME_AVG_PERIOD = 20
VOLUME_SPIKE_MULTIPLIER = 2.0

# A stock within this % below a resistance level counts as "near breakout".
NEAR_BREAKOUT_PCT = 0.03

# Minimum current price (in INR) applied to every NSE market (Nifty 500
# and All Stocks alike) to filter out penny stocks. A stock priced at
# exactly MIN_PRICE_INR does NOT qualify -- only strictly above it does.
MIN_PRICE_INR = 100.0

SMA_FAST = 50
SMA_SLOW = 200

# Weekly-timeframe equivalents, used for the parallel weekly scan
# (resampled from the same daily history already downloaded -- no extra
# API calls). These are NOT the daily 50/200 reused on weekly bars (which
# would span ~1/~4 years and rarely trigger); 10-week/40-week is the
# classic weekly trend-template pairing (Stan Weinstein style).
WEEKLY_RESISTANCE_LOOKBACKS = {
    "1M": 4,
    "3M": 13,
    "6M": 26,
    "1Y": 52,
    "2Y": 104,
    "3Y": 156,
}
WEEKLY_SMA_FAST = 10
WEEKLY_SMA_SLOW = 40
WEEKLY_VOLUME_AVG_PERIOD = 10
WEEKLY_RSI_PERIOD = 14

# Weights for the deterministic 0-100 Breakout Quality Score. Must sum to
# 1.0. Each sub-score is itself computed on a 0-100 scale before weighting
# -- see indicators.compute_quality_score for the formula.
QUALITY_SCORE_WEIGHTS = {
    "resistance": 0.25,   # how decisively price has broken/is near resistance
    "volume": 0.20,       # strength of the volume spike vs the required minimum
    "rsi": 0.15,          # RSI in a healthy momentum zone, not overbought
    "trend": 0.20,        # price's distance above SMA50/SMA200 (trend strength)
    "close_strength": 0.20,  # today's close vs today's range (near the high = strong)
}

# Benchmark index ticker (for the Market Overview section) per market.
BENCHMARK_INDEX = {
    "NSE": "^NSEI",       # Nifty 50
    "NSE_ALL": "^NSEI",   # Nifty 50 (no reliably free Nifty 500 index feed on Yahoo)
    "NYSE": "^GSPC",      # S&P 500
}
BENCHMARK_NAME = {
    "NSE": "Nifty 50",
    "NSE_ALL": "Nifty 50",
    "NYSE": "S&P 500",
}

# NSE trading hours in IST (used only for the OPEN/CLOSED badge; this does
# NOT account for market holidays, since no holiday calendar is wired up).
NSE_OPEN_HOUR, NSE_OPEN_MINUTE = 9, 15
NSE_CLOSE_HOUR, NSE_CLOSE_MINUTE = 15, 30
# US market hours in US/Eastern (same caveat: no holiday calendar).
NYSE_OPEN_HOUR, NYSE_OPEN_MINUTE = 9, 30
NYSE_CLOSE_HOUR, NYSE_CLOSE_MINUTE = 16, 0

SCAN_HISTORY_MAX_ENTRIES = 200

# Streamlit auto-refresh cadence (ms) and matching scan cache TTL (s).
AUTOREFRESH_INTERVAL_MS = 5 * 60 * 60 * 1000
SCAN_CACHE_TTL_SECONDS = 5 * 60 * 60

# Suggested stop-loss sits this fraction below the nearest support level
# (a small buffer so a minor wick through support doesn't count as a stop-out).
STOP_LOSS_BUFFER = 0.02

# How many worker threads to use when fetching P/E ratios for the
# (small) list of stocks that pass the technical filters.
PE_FETCH_WORKERS = 8

# "Upside Buy Movement" strategy: a pre-breakout consolidation scanner,
# separate from the main Breakout/Near Breakout strategy above. Every
# threshold here is a fixed rule from that strategy's definition, not a
# user-adjustable scan parameter -- see src/pre_breakout.py.
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

MARKETS = {
    f"NSE (Nifty 500, ₹{MIN_PRICE_INR:.0f}+)": "NSE",
    f"NSE (All Stocks, ₹{MIN_PRICE_INR:.0f}+)": "NSE_ALL",
    "US (S&P 500 / NYSE)": "NYSE",
}
MARKET_LABELS = {
    "NSE": "Nifty 500",
    "NSE_ALL": "All Stocks",
    "NYSE": "S&P 500 (NYSE)",
}
