"""On-demand deep-dive info for a single stock. Unlike scanner.py (which
bulk-downloads the whole universe), these functions are only called for
one ticker at a time -- when the user selects it in the UI -- so slower
per-ticker calls like `.info` and a longer price history are fine here.
"""

import pandas as pd
import yfinance as yf


def get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    try:
        return yf.Ticker(ticker).history(period=period, interval="1d")
    except Exception:
        return pd.DataFrame()


# Chart-pane interval/range support. "1wk"/"1mo"/"1y" have no native
# yfinance interval that reaches back far enough (yfinance's own "1wk"/
# "1mo" intervals are truncated by Yahoo for longer ranges, and there is
# no "1y" interval at all) -- so those three are built by resampling
# daily EOD data locally instead. This is real aggregated data, not
# fabricated, but it's still worth disclosing since it's a step removed
# from "Yahoo's own weekly/monthly candle."
INTERVAL_LABELS = {"5m": "5 Minute", "1d": "Daily", "1wk": "Weekly", "1mo": "Monthly", "1y": "Yearly"}

# Which viewing ranges make sense for each interval -- Yahoo only keeps
# ~60 days of 5-minute bars, so offering "5Y" there would just silently
# return nothing. Ranges are removed rather than shown disabled, since
# Streamlit's selectbox has no "disabled option" state; the reason is
# surfaced as a caption next to the control instead.
RANGE_OPTIONS_BY_INTERVAL = {
    "5m": ["5D", "1M"],
    "1d": ["1M", "3M", "6M", "1Y", "2Y", "5Y", "Max"],
    "1wk": ["6M", "1Y", "2Y", "5Y", "Max"],
    "1mo": ["1Y", "2Y", "5Y", "Max"],
    "1y": ["5Y", "Max"],
}
_RANGE_TO_YF_PERIOD = {
    "5D": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "Max": "max",
}
_RESAMPLE_RULE = {"1wk": "W-FRI", "1mo": "ME", "1y": "YE"}


def get_chart_data(ticker: str, interval: str, range_key: str) -> tuple[pd.DataFrame, str | None]:
    """Returns (df, note) for the chart pane. `note` is a short honesty
    caption to show under the chart (e.g. "resampled from daily data"),
    or None when the bars came directly from Yahoo Finance at the
    requested interval. An empty df means no data was available --
    never a fabricated candle.
    """
    yf_period = _RANGE_TO_YF_PERIOD.get(range_key, "1y")
    try:
        if interval in ("5m", "1d"):
            df = yf.Ticker(ticker).history(period=yf_period, interval=interval)
            note = None
        else:
            daily = yf.Ticker(ticker).history(period=yf_period, interval="1d")
            if daily.empty:
                return pd.DataFrame(), "No price data available for this range."
            rule = _RESAMPLE_RULE[interval]
            df = daily.resample(rule).agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
            }).dropna(subset=["Close"])
            note = (
                f"{INTERVAL_LABELS[interval]} candles are aggregated locally from daily EOD data -- "
                f"Yahoo Finance has no native {INTERVAL_LABELS[interval].lower()} interval covering this range."
            )
    except Exception:
        return pd.DataFrame(), "Price data unavailable right now."

    if df is None or df.empty:
        return pd.DataFrame(), "No price data available for this range/interval combination."
    return df, note


def get_company_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception:
        info = {}

    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "eps": info.get("trailingEps"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "avg_volume": info.get("averageVolume"),
        "target_mean_price": info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey"),
        "summary": info.get("longBusinessSummary"),
        "website": info.get("website"),
    }
