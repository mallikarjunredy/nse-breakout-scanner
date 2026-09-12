"""On-demand deep financial data for a single stock's detail view --
income statement, balance sheet, cash flow, extended ratios, major
holders, and recent news. Like detail.py, these are slow per-ticker
calls made only for the one stock currently open in the UI, never
across a whole scan universe.

This is not a Screener.in-equivalent: Yahoo Finance (yfinance's only
data source) does not carry India-specific data like promoter/FII/DII
shareholding-pattern trends, pledge percentages, concalls, credit
ratings, or annual-report links. What it DOES carry -- multi-year and
quarterly financial statements, a broad set of ratios, holder
percentages, and news -- is exposed here as faithfully as the source
data allows, with missing fields shown as unavailable rather than
guessed at.
"""

import pandas as pd
import yfinance as yf

_INCOME_ROWS = ["Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income", "Basic EPS"]
_BALANCE_ROWS = ["Total Debt", "Stockholders Equity", "Working Capital", "Net Tangible Assets", "Common Stock Equity"]
_CASHFLOW_ROWS = ["Free Cash Flow", "Capital Expenditure", "Financing Cash Flow", "End Cash Position"]


def _select_rows(df: pd.DataFrame, wanted_rows: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    available = [r for r in wanted_rows if r in df.index]
    if not available:
        return pd.DataFrame()
    out = df.loc[available].copy()
    out.columns = [c.strftime("%b %Y") if hasattr(c, "strftime") else str(c) for c in out.columns]
    return out


def get_income_statement(ticker: str, quarterly: bool = False) -> pd.DataFrame:
    try:
        t = yf.Ticker(ticker)
        df = t.quarterly_financials if quarterly else t.financials
        return _select_rows(df, _INCOME_ROWS)
    except Exception:
        return pd.DataFrame()


def get_balance_sheet(ticker: str, quarterly: bool = False) -> pd.DataFrame:
    try:
        t = yf.Ticker(ticker)
        df = t.quarterly_balance_sheet if quarterly else t.balance_sheet
        return _select_rows(df, _BALANCE_ROWS)
    except Exception:
        return pd.DataFrame()


def get_cash_flow(ticker: str, quarterly: bool = False) -> pd.DataFrame:
    try:
        t = yf.Ticker(ticker)
        df = t.quarterly_cashflow if quarterly else t.cashflow
        return _select_rows(df, _CASHFLOW_ROWS)
    except Exception:
        return pd.DataFrame()


def get_extended_ratios(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception:
        info = {}
    return {
        "roe": info.get("returnOnEquity"),
        "roa": info.get("returnOnAssets"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "quick_ratio": info.get("quickRatio"),
        "book_value": info.get("bookValue"),
        "price_to_book": info.get("priceToBook"),
        "profit_margin": info.get("profitMargins"),
        "operating_margin": info.get("operatingMargins"),
        "gross_margin": info.get("grossMargins"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "peg_ratio": info.get("pegRatio"),
        "payout_ratio": info.get("payoutRatio"),
        "insider_holding": info.get("heldPercentInsiders"),
        "institution_holding": info.get("heldPercentInstitutions"),
        "face_value": info.get("faceValue"),
    }


def get_major_holders(ticker: str) -> pd.DataFrame:
    try:
        mh = yf.Ticker(ticker).major_holders
        if mh is None or mh.empty:
            return pd.DataFrame()
        return mh
    except Exception:
        return pd.DataFrame()


def get_recent_news(ticker: str, limit: int = 6) -> list[dict]:
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        raw = []
    out = []
    for item in raw[:limit]:
        content = item.get("content", item)
        title = content.get("title")
        if not title:
            continue
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or content.get("link")
        )
        publisher = (content.get("provider") or {}).get("displayName") or content.get("publisher")
        out.append({"title": title, "link": url, "publisher": publisher, "pub_date": content.get("pubDate")})
    return out
