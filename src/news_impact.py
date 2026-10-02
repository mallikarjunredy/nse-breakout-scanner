"""Global News Impact & Stock Forecast -- Phase 1 (vertical slice).

Built from a very large written spec that assumed infrastructure this
app doesn't have: authenticated social-media feeds, paid/licensed news
APIs, and a validated statistical/ML forecasting model. None of those
exist here, and nothing in this module pretends otherwise -- see the
"What this is NOT" section below and the page caption in `app.py`.

**News source**: free, public RSS feeds (Economic Times, Business
Standard, LiveMint, CNBC World, Investing.com Commodities -- confirmed
live and returning current-dated items before building this; Reuters'
public RSS and Moneycontrol's RSS were tried and dropped, the first
404s and the second returned stale ~2024 cached items) plus yfinance's
own `.news` aggregation for a handful of macro tickers (same data
source `deep_dive.get_recent_news` already uses for stock-specific
news). Every item is labelled `"Unverified web source"` -- never
"verified" -- since this app has no way to cryptographically
authenticate an official account or confirm a story against a primary
filing. A human should click through to the original link before
treating any headline as confirmed, which the UI says explicitly.

**Topic tagging and direction** are plain keyword matching, not an AI/
NLP pipeline -- this app has no LLM API key configured, so there is no
real "extract events and explain exposure" model running at request
time. Keyword tagging is disclosed as a simplification everywhere it's
used, and a headline with no clear positive/negative keyword match
always resolves to "Unclear," never a guessed direction.

**Exposure map**: a small seed set of well-known large-cap stocks.
Entries marked `sourced=True` cite a specific, real disclosure found by
direct research before this was built (e.g. TCS's own FY2026 investor
fact sheet); everything else is `sourced=False` -- a qualitative,
uncited profile built from general public knowledge of that company's
business, explicitly flagged as illustrative, not a specific filing
citation. No percentage is ever invented for an unsourced entry.

**What this is NOT** (de-scoped for this pass, and never silently
implied otherwise):

- Not a verified/authenticated news feed. Not a replacement for reading
  the original source.
- Not a numeric price forecast. Every stock's "forecast" is a direction
  (Positive/Negative/Mixed/Unclear) + a confidence label + the plain-
  language event -> factor -> exposure -> impact chain -- never a %
  move, per the spec's own "insufficient evidence" fallback, shown
  verbatim via `NO_PERCENT_FORECAST_MSG`.
- Not a confirmed-vs-rumor classifier. This module does not attempt to
  distinguish an official announcement from a proposal, opinion, or
  rumor -- the UI says so and tells the user to check the source.
- Not a portfolio-level numeric aggregate. Qualitative directions
  (Positive/Negative/Mixed/Unclear) are shown per holding with its
  real weight %, never averaged into a single fabricated "portfolio
  score."
"""

import datetime
import re
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from lxml import etree

from . import config, portfolio, watchlist

IST = ZoneInfo("Asia/Kolkata")

NO_PERCENT_FORECAST_MSG = "Directional assessment only — insufficient evidence for a reliable percentage forecast."
UNVERIFIED_LABEL = "Unverified web source — not independently authenticated; check the original link before acting."

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Confirmed live (returning current-dated items) before building this --
# Reuters' public RSS 404s and Moneycontrol's returned stale ~2024
# cached items, so neither is used.
_RSS_SOURCES = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "india"),
    ("Economic Times Top News", "https://economictimes.indiatimes.com/rssfeedstopstories.cms", "india"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss", "india"),
    ("LiveMint Markets", "https://www.livemint.com/rss/markets", "india"),
    ("CNBC World", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "global"),
    ("Investing.com Commodities", "https://www.investing.com/rss/commodities.rss", "global"),
]

# A handful of macro tickers whose yfinance `.news` tends to surface
# market-moving headlines -- same data source/shape as
# deep_dive.get_recent_news, reused for consistency.
_MACRO_NEWS_TICKERS = [
    ("^NSEI", "Nifty 50"), ("^GSPC", "S&P 500"), ("CL=F", "Crude Oil"),
    ("USDINR=X", "USD/INR"), ("^TNX", "US 10Y Yield"),
]

# Deterministic keyword taxonomy -- not an AI classifier. A headline can
# match more than one topic. Public (no leading underscore): app.py
# reads this directly to populate the News page's topic filter.
TOPIC_KEYWORDS = {
    "interest_rates": ["federal reserve", "fed ", "fomc", "rbi", "repo rate", "interest rate",
                        "rate hike", "rate cut", "monetary policy", "powell"],
    "tariffs_trade": ["tariff", "trade war", "trump", "import duty", "export ban", "sanction",
                       "trade deal", "wto", "trade talks"],
    "oil_energy": ["crude", "oil price", "opec", "brent", "wti", "energy price", "gas price",
                   "oil supply"],
    "geopolitical": ["russia", "ukraine", "iran", "israel", "war ", "conflict", "missile",
                      "ceasefire", "hormuz", "houthi"],
    "currency": ["rupee", "dollar", "usd/inr", "currency", "forex", "depreciat", "appreciat"],
    "commodities": ["gold", "silver", "copper", "commodity", "metal price"],
}

# Plain keyword polarity -- a heuristic, not sentiment analysis. Only
# used when a headline's topic also matches a stock's exposure, and
# only ever nudges toward Positive/Negative; anything ambiguous stays
# "Unclear" rather than guessing.
_POSITIVE_WORDS = ["cut", "cuts", "ease", "eases", "easing", "ceasefire", "deal reached",
                    "removes tariff", "lifts sanctions", "falls", "declines", "drops", "rally", "rallies"]
_NEGATIVE_WORDS = ["hike", "hikes", "raises", "imposes tariff", "new sanctions", "escalat",
                    "surge", "surges", "spikes", "soars", "tensions rise", "attack"]

# Seed exposure map. `sourced=True` entries cite a specific disclosure
# found by direct research before this was built; everything else is a
# qualitative, uncited profile (general public knowledge of the
# business), explicitly flagged as illustrative -- no percentage is
# ever invented for an unsourced entry.
EXPOSURE_MAP = {
    "TCS.NS": {
        "name": "Tata Consultancy Services", "sector": "IT Services",
        "export_dependence": "Very High",
        "profile": "North America 48.6%, UK 17.4%, Continental Europe 15.4% of FY2026 revenue (>81% combined "
                   "from these three regions). IT services -- revenue is people/services, not commodity "
                   "input-dependent, but highly sensitive to US/Europe IT spending and USD/GBP/EUR vs INR.",
        "topics": ["interest_rates", "currency"],
        "sourced": True, "source": "TCS Q4 FY2026 Fact Sheet (tcs.com investor relations)",
        "source_url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/q4/Presentations/Q4%202025-26%20Fact%20Sheet.pdf",
    },
    "INFY.NS": {
        "name": "Infosys", "sector": "IT Services", "export_dependence": "Very High",
        "profile": "Majority North America/Europe-billed IT services revenue, similar export profile to TCS. "
                   "No specific current-period geography split cited here.",
        "topics": ["interest_rates", "currency"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "SUNPHARMA.NS": {
        "name": "Sun Pharmaceutical Industries", "sector": "Pharmaceuticals",
        "export_dependence": "High",
        "profile": "~30% of FY2026 consolidated revenue from the US. US generics pricing pressure and US "
                   "tariff/trade policy on pharma exports are direct, well-documented sensitivities.",
        "topics": ["tariffs_trade", "currency"],
        "sourced": True, "source": "Business Standard / Nomura estimates, FY2026",
        "source_url": "https://www.business-standard.com/companies/news/challenging-year-for-sun-pharma-in-us-as-fy26-revenue-falls-0-9-126052201149_1.html",
    },
    "DRREDDY.NS": {
        "name": "Dr. Reddy's Laboratories", "sector": "Pharmaceuticals", "export_dependence": "High",
        "profile": "Major US generics exporter, similar sensitivity profile to Sun Pharma. No specific "
                   "current-period revenue split cited here.",
        "topics": ["tariffs_trade", "currency"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "RELIANCE.NS": {
        "name": "Reliance Industries", "sector": "Oil, Gas & Petrochemicals", "export_dependence": "Medium",
        "profile": "Crude oil is a major refining input (imported); refined products/petrochemicals are "
                   "partly exported. Also has large domestic telecom (Jio) and retail businesses with no "
                   "direct global-news sensitivity.",
        "topics": ["oil_energy", "currency"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "ONGC.NS": {
        "name": "Oil & Natural Gas Corporation", "sector": "Oil & Gas Exploration", "export_dependence": "Low",
        "profile": "Domestic crude/gas producer -- revenue directly tracks global crude oil prices (higher "
                   "crude price = higher realized revenue per barrel, the opposite exposure direction from "
                   "an oil importer/refiner).",
        "topics": ["oil_energy"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "IOC.NS": {
        "name": "Indian Oil Corporation", "sector": "Oil Marketing & Refining", "export_dependence": "Low",
        "profile": "Refiner/marketer -- crude oil is its primary imported input; margins are sensitive to "
                   "the crude-to-product price spread, not simply the crude price direction.",
        "topics": ["oil_energy", "currency"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "TATAMOTORS.NS": {
        "name": "Tata Motors", "sector": "Automobiles", "export_dependence": "High",
        "profile": "Jaguar Land Rover subsidiary has major UK/Europe/China/US sales exposure -- directly "
                   "sensitive to auto tariffs, UK/EU demand, and China trade conditions.",
        "topics": ["tariffs_trade", "geopolitical", "currency"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "MARUTI.NS": {
        "name": "Maruti Suzuki India", "sector": "Automobiles", "export_dependence": "Low",
        "profile": "Overwhelmingly domestic-market-focused passenger-vehicle sales -- a useful contrast case "
                   "with low direct sensitivity to most global-news categories here.",
        "topics": [], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "HDFCBANK.NS": {
        "name": "HDFC Bank", "sector": "Private Bank", "export_dependence": "Low",
        "profile": "Domestic lender -- primary global-news sensitivity is to RBI/Fed interest-rate policy "
                   "(affects net interest margins and credit demand), not trade/commodity exposure.",
        "topics": ["interest_rates"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "BAJFINANCE.NS": {
        "name": "Bajaj Finance", "sector": "NBFC", "export_dependence": "Low",
        "profile": "Domestic non-bank lender -- directly rate-sensitive (borrowing costs, loan demand) via "
                   "RBI policy; limited direct trade/commodity exposure.",
        "topics": ["interest_rates"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "HINDUNILVR.NS": {
        "name": "Hindustan Unilever", "sector": "FMCG", "export_dependence": "Low",
        "profile": "Domestic-focused FMCG -- some imported/commodity input costs (palm oil, crude-linked "
                   "packaging) but predominantly a low-global-sensitivity contrast case.",
        "topics": ["commodities"], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
    "ITC.NS": {
        "name": "ITC Limited", "sector": "FMCG / Diversified", "export_dependence": "Low",
        "profile": "Predominantly domestic FMCG/cigarettes/hotels/paperboard -- another low-global-sensitivity "
                   "contrast case.",
        "topics": [], "sourced": False,
        "source": "General/uncited company profile — illustrative, not a specific filing citation.",
        "source_url": None,
    },
}


# ---------------------------------------------------------------------------
# News fetching
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 3}


def _is_duplicate(a: str, seen_word_sets: list[set]) -> bool:
    a_words = _normalize_title(a)
    if not a_words:
        return False
    for b_words in seen_word_sets:
        if not b_words:
            continue
        overlap = len(a_words & b_words) / max(len(a_words), len(b_words))
        if overlap >= config.NEWS_IMPACT_DEDUP_TITLE_WORD_OVERLAP:
            return True
    return False


def _tag_topics(title: str) -> list[str]:
    low = title.lower()
    return [topic for topic, kws in TOPIC_KEYWORDS.items() if any(kw in low for kw in kws)]


def _fetch_rss(name: str, url: str, scope: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = etree.fromstring(resp.content)
    except Exception:
        return []
    items = []
    for item in root.findall(".//item")[: config.NEWS_IMPACT_MAX_ITEMS_PER_SOURCE]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        pub_date_raw = item.findtext("pubDate")
        items.append({
            "title": title, "link": link, "source": name, "scope": scope,
            "pub_date_raw": pub_date_raw, "topics": _tag_topics(title),
        })
    return items


def _fetch_macro_yf_news() -> list[dict]:
    items = []
    for ticker, label in _MACRO_NEWS_TICKERS:
        try:
            raw = yf.Ticker(ticker).news or []
        except Exception:
            raw = []
        for entry in raw[:10]:
            content = entry.get("content", entry)
            title = content.get("title")
            if not title:
                continue
            url = (
                (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or content.get("link")
            )
            publisher = (content.get("provider") or {}).get("displayName") or content.get("publisher") or "Yahoo Finance"
            items.append({
                "title": title, "link": url, "source": f"{publisher} (via Yahoo Finance, {label})",
                "scope": "global" if ticker != "^NSEI" else "india",
                "pub_date_raw": content.get("pubDate"), "topics": _tag_topics(title),
            })
    return items


def fetch_global_news() -> list[dict]:
    """Every item: {title, link, source, scope, pub_date_raw, topics,
    collected_at_ist, verification}. Deduplicated across all sources by
    normalized-title word overlap (keeps the first copy seen). Never
    raises -- a source that fails to fetch is simply absent from the
    result, same discipline as every other data source in this app.
    """
    raw_items = []
    for name, url, scope in _RSS_SOURCES:
        raw_items.extend(_fetch_rss(name, url, scope))
    raw_items.extend(_fetch_macro_yf_news())

    collected_at = datetime.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    deduped = []
    seen_word_sets: list[set] = []
    for item in raw_items:
        if _is_duplicate(item["title"], seen_word_sets):
            continue
        seen_word_sets.append(_normalize_title(item["title"]))
        item["collected_at_ist"] = collected_at
        item["verification"] = UNVERIFIED_LABEL
        deduped.append(item)
    return deduped


# ---------------------------------------------------------------------------
# Next trading session
# ---------------------------------------------------------------------------

def resolve_next_session(as_of: datetime.date | None = None) -> datetime.date:
    """Next weekday after `as_of` (default: today in IST). Does NOT
    account for NSE exchange holidays -- no holiday calendar is wired
    up anywhere in this app (same disclosed limitation as the Market
    OPEN/CLOSED badge).
    """
    d = as_of or datetime.datetime.now(IST).date()
    nxt = d + datetime.timedelta(days=1)
    while nxt.weekday() >= 5:  # Sat=5, Sun=6
        nxt += datetime.timedelta(days=1)
    return nxt


# ---------------------------------------------------------------------------
# Impact assessment -- directional only, never a % forecast
# ---------------------------------------------------------------------------

def _assess_direction(matched_items: list[dict]) -> tuple[str, list[str], list[str]]:
    pos_hits, neg_hits = [], []
    for item in matched_items:
        low = item["title"].lower()
        if any(w in low for w in _POSITIVE_WORDS):
            pos_hits.append(item["title"])
        if any(w in low for w in _NEGATIVE_WORDS):
            neg_hits.append(item["title"])
    if pos_hits and neg_hits:
        return "Mixed", pos_hits, neg_hits
    if pos_hits:
        return "Positive", pos_hits, neg_hits
    if neg_hits:
        return "Negative", pos_hits, neg_hits
    return "Unclear", pos_hits, neg_hits


def assess_stock_impact(ticker: str, news_items: list[dict]) -> dict | None:
    """Returns None if `ticker` has no exposure-map entry (never
    fabricates exposure for an unmapped stock). Otherwise returns a
    dict with direction, confidence, matched news, the event -> factor
    -> exposure -> impact explanation, and the mandatory
    NO_PERCENT_FORECAST_MSG disclosure.
    """
    exposure = EXPOSURE_MAP.get(ticker)
    if exposure is None:
        return None

    matched = [item for item in news_items if set(item["topics"]) & set(exposure["topics"])]
    direction, pos_hits, neg_hits = _assess_direction(matched)

    if not matched:
        confidence = "Low"
    elif exposure["sourced"]:
        confidence = "Medium"
    else:
        confidence = "Low"

    chain = []
    if matched:
        for item in matched[:3]:
            chain.append(
                f"{item['source']}: \"{item['title']}\" (topics: {', '.join(item['topics'])}) "
                f"→ {exposure['name']}'s exposure: {exposure['profile']}"
            )
    else:
        chain.append(f"No fetched news items matched {exposure['name']}'s tracked exposure topics "
                      f"({', '.join(exposure['topics']) or 'none tracked'}) in this batch.")

    return {
        "ticker": ticker, "name": exposure["name"], "sector": exposure["sector"],
        "export_dependence": exposure["export_dependence"], "profile": exposure["profile"],
        "sourced": exposure["sourced"], "source": exposure["source"], "source_url": exposure["source_url"],
        "direction": direction, "confidence": confidence,
        "matched_news": matched, "positive_signals": pos_hits, "negative_signals": neg_hits,
        "explanation_chain": chain, "forecast_note": NO_PERCENT_FORECAST_MSG,
    }


def sector_impact_ranking(news_items: list[dict]) -> pd.DataFrame:
    """One row per exposure-map sector (grouping its member stocks),
    ranked by how many matched/positive/negative news items it has --
    a count-based ranking, not a numeric score, since no validated
    weighting model exists.
    """
    by_sector: dict[str, dict] = {}
    for ticker, exposure in EXPOSURE_MAP.items():
        result = assess_stock_impact(ticker, news_items)
        sector = exposure["sector"]
        agg = by_sector.setdefault(sector, {"stocks": 0, "matched_items": 0, "positive": 0, "negative": 0, "mixed": 0, "unclear": 0})
        agg["stocks"] += 1
        agg["matched_items"] += len(result["matched_news"])
        agg[result["direction"].lower()] += 1

    rows = [
        {"Sector": sector, "Stocks Tracked": v["stocks"], "Matched News Items": v["matched_items"],
         "Positive": v["positive"], "Negative": v["negative"], "Mixed": v["mixed"], "Unclear": v["unclear"]}
        for sector, v in by_sector.items()
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Matched News Items", ascending=False).reset_index(drop=True)
    return df


def portfolio_and_watchlist_impact(news_items: list[dict]) -> dict:
    """Per the spec: portfolio rows get a real weight % (from
    `portfolio.get_portfolio()`); watchlist rows never get a fabricated
    weight or ₹ impact, since a watchlist ticker isn't an actual
    holding. Neither gets a numeric ₹ impact, since there's no %
    forecast to multiply a holding value by -- only direction/
    confidence/explanation, consistent with NO_PERCENT_FORECAST_MSG.
    A ticker with no exposure-map entry gets its own clearly-labelled
    "no exposure data" row rather than being silently dropped.
    """
    port = portfolio.get_portfolio()
    port_table = port["table"]
    weight_by_ticker = dict(zip(port_table["Ticker"], port_table["Weight %"])) if not port_table.empty else {}

    def _rows_for(tickers: list[str], include_weight: bool) -> list[dict]:
        rows = []
        for t in tickers:
            result = assess_stock_impact(t, news_items)
            if result is None:
                rows.append({
                    "Ticker": t, "Company Name": t, "Sector": "—", "Direction": "No exposure data",
                    "Confidence": "—", "Weight %": weight_by_ticker.get(t) if include_weight else None,
                    "Explanation": "This stock isn't in the exposure map yet -- no fabricated assessment is shown.",
                })
            else:
                rows.append({
                    "Ticker": t, "Company Name": result["name"], "Sector": result["sector"],
                    "Direction": result["direction"], "Confidence": result["confidence"],
                    "Weight %": weight_by_ticker.get(t) if include_weight else None,
                    "Explanation": result["explanation_chain"][0] if result["explanation_chain"] else "",
                })
        return rows

    port_tickers = portfolio.get_tickers()
    watch_tickers = watchlist.load()

    return {
        "portfolio_rows": _rows_for(port_tickers, include_weight=True),
        "watchlist_rows": _rows_for(watch_tickers, include_weight=False),
        "portfolio_total_value": port["total_value"],
    }
