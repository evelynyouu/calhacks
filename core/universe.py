"""The companies a fund can hold: size, sector and country for each, and the US core.  OWNER: Evelyn.

Reads data/companies.csv (through core/market.py, no second reader). market_cap_b is in billions of
dollars, approximate, from one date (docs/data_sources.md). `core` = 1 marks the US index: the 45 biggest
US companies we have, plus the next biggest in any sector that would otherwise have fewer than 3.
A theme's companies are the ones whose home fund (the `etf` column) is that theme's fund.
"""
from __future__ import annotations

from core.market import COMPANIES, ETFS

SECTORS = ("Technology", "Healthcare", "Financials", "Consumer", "Everyday essentials", "Communication",
           "Industrials", "Energy", "Utilities", "Real estate", "Materials")


def market_cap(ticker: str) -> float:
    return float(COMPANIES[ticker]["market_cap_b"])


def sector(ticker: str) -> str:
    return COMPANIES[ticker]["sector"]


def country(ticker: str) -> str:
    return COMPANIES[ticker]["country"]


def core_companies() -> list[str]:
    """The US index, biggest first."""
    return sorted((t for t, r in COMPANIES.items() if r["core"] == "1"), key=market_cap, reverse=True)


def theme_companies(theme: str, n: int) -> list[str]:
    """The theme's n biggest companies; [] when we hold none for it (gold, emerging markets...)."""
    fund = next((t for t, r in ETFS.items() if r["role"] == "tilt" and r["theme"] == theme), None)
    picks = [t for t, r in COMPANIES.items() if fund and r["etf"] == fund]
    return sorted(picks, key=market_cap, reverse=True)[:n]
