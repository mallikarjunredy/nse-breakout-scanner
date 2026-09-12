"""Persistent watchlist: tickers the user wants to keep an eye on across
sessions (e.g. candidates that didn't trigger today but might tomorrow),
stored as a simple JSON list under data/watchlist.json.
"""

import json

from . import config

_PATH = config.DATA_DIR / "watchlist.json"


def load() -> list[str]:
    if not _PATH.exists():
        return []
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sorted(t for t in data if isinstance(t, str))
    except Exception:
        return []


def save(tickers: list[str]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(set(tickers)), f, indent=2)


def add(ticker: str) -> list[str]:
    tickers = load()
    if ticker not in tickers:
        tickers.append(ticker)
        save(tickers)
    return tickers


def remove(ticker: str) -> list[str]:
    tickers = load()
    if ticker in tickers:
        tickers.remove(ticker)
        save(tickers)
    return tickers
