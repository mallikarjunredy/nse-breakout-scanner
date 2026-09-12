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
