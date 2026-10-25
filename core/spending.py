"""Where the customer spends money, read from the bank, turned into companies.  OWNER: Evelyn.

Source: the persona's activity feed (nessie.recent_activity), so live Nessie, demo data and the
offline fallback all give the same answer. Counted: [spend] purchases in the last WINDOW_DAYS of the
sim clock, by dollars. Not counted: bills, and one-off [event] purchases like the car repair.
Fast-forward writes one lump of "day-to-day spending" for the days it skips; that lump is spread over
the persona's usual merchants by their share (personas.json "merchants").

A merchant maps to a company by name (data/merchants.csv, first pattern that matches). A merchant with
no public company (a local shop, a private chain like Trader Joe's) falls back to its category's biggest
public companies (data/category_leaders.csv).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from core import nessie, seed
from core.market import COMPANIES, DATA
from core.state import SIM_START

WINDOW_DAYS = 90
LUMP = "day-to-day spending"   # fast-forward's [spend] text

with (DATA / "merchants.csv").open() as _f:
    BRANDS = [(re.compile(r["pattern"], re.I), r["ticker"] or None) for r in csv.DictReader(_f)]
with (DATA / "category_leaders.csv").open() as _f:
    CATEGORY_LEADERS = {r["category"]: r["tickers"].split(";") for r in csv.DictReader(_f)}


@dataclass(frozen=True)
class Spend:
    merchant: str
    category: str
    dollars: float
    ticker: str | None                              # the merchant's own company, if it has one we hold
    via: Literal["brand", "category", "none"]       # how it maps to companies


def spending_profile(cid: str, days: int = WINDOW_DAYS) -> list[Spend]:
    """One Spend per merchant, most dollars first."""
    feed = nessie.recent_activity(cid, None)
    as_of = max([date.fromisoformat(i["on"]) for i in feed] + [SIM_START])
    start = as_of - timedelta(days=days)
    usual = nessie.persona_seed(cid).get("merchants", [])
    dollars: dict[str, float] = {}
    for item in feed:
        if item["tag"] != "spend" or not start < date.fromisoformat(item["on"]) <= as_of:
            continue
        if item["text"] == LUMP and usual:
            for m in usual:
                dollars[m["name"]] = dollars.get(m["name"], 0.0) + item["amount"] * m["share"]
        else:
            dollars[item["text"]] = dollars.get(item["text"], 0.0) + item["amount"]
    out = [_spend(name, round(d, 2)) for name, d in dollars.items() if d > 0]
    return sorted(out, key=lambda s: (-s.dollars, s.merchant))


def company_spend(profile: list[Spend]) -> dict[str, float]:
    """{ticker: dollars}. A category fallback splits its dollars evenly over the category's leaders."""
    out: dict[str, float] = {}
    for s in profile:
        tickers = [s.ticker] if s.ticker else CATEGORY_LEADERS.get(s.category, []) if s.via == "category" else []
        for t in tickers:
            out[t] = out.get(t, 0.0) + s.dollars / len(tickers)
    return out


def _spend(merchant: str, dollars: float) -> Spend:
    category = {name: cat for name, cat in seed.MERCHANTS.values()}.get(merchant, "")
    ticker = next((t for rx, t in BRANDS if rx.search(merchant)), None)
    if ticker and ticker in COMPANIES:
        return Spend(merchant, category, dollars, ticker, "brand")
    return Spend(merchant, category, dollars, None, "category" if category in CATEGORY_LEADERS else "none")
