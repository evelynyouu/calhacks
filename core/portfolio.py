"""Allocation engine.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps).

STUB: risk -> core ETF, interests -> capped tilt, simple overlap check.
Worst-year numbers are PLACEHOLDERS until prices are cached.
"""
from __future__ import annotations

import csv
from pathlib import Path

from core.models import Holding, Portfolio

_ETFS = {r["ticker"]: r for r in csv.DictReader(
    (Path(__file__).resolve().parent.parent / "data" / "etfs.csv").open())}

CORE_BY_RISK = {1: "AOK", 2: "AOM", 3: "AOR", 4: "AOA"}
RISK_LABELS = {1: "Conservative", 2: "Moderate", 3: "Growth", 4: "Aggressive"}
TILTS_BY_INTEREST = {"tech": ["QQQ", "XLK"], "healthcare": ["XLV"], "clean_energy": ["ICLN"]}
# Max share in interest tilts, by risk level. Sector funds swing far harder than
# the cores (ICLN lost over half its value 2021–23), so a conservative fund only
# gets a taste and only an aggressive one reaches a quarter.
TILT_CAP_BY_RISK = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.25}
# PLACEHOLDER worst calendar year per core (Saahil: compute from cached prices)
_WORST = {"AOK": (2022, -13.9), "AOM": (2022, -15.6), "AOR": (2022, -16.9), "AOA": (2022, -18.1)}
# PLACEHOLDER pairs with heavy holdings overlap (Saahil: compute from holdings)
_OVERLAPS = {frozenset({"QQQ", "XLK"}): "QQQ and XLK hold mostly the same big tech companies."}


def build_portfolio(risk_level: int, interests: list[str] | None = None) -> Portfolio:
    risk_level = min(4, max(1, int(risk_level)))
    core_t = CORE_BY_RISK[risk_level]
    tilt_tickers: list[str] = []
    for i in interests or []:
        tilt_tickers += TILTS_BY_INTEREST.get(i, [])

    warnings: list[str] = []
    for pair, msg in _OVERLAPS.items():
        if pair <= set(tilt_tickers):
            warnings.append(msg + f" Kept {sorted(pair)[0]}, dropped the other.")
            tilt_tickers.remove(sorted(pair)[1])

    holdings = []
    tilt_w = TILT_CAP_BY_RISK[risk_level] if tilt_tickers else 0.0
    holdings.append(_holding(core_t, 1 - tilt_w, "core"))
    for t in tilt_tickers:
        holdings.append(_holding(t, tilt_w / len(tilt_tickers), "tilt"))

    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[core_t]
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr, warnings)


def _holding(ticker: str, weight: float, role: str) -> Holding:
    r = _ETFS[ticker]
    return Holding(ticker, r["name"], round(weight, 4), role, float(r["expense_ratio"]))


def risk_from_answers(timeline_years: float, age: int, loss_reaction: str) -> int:
    """Intake answers -> risk level 1..4.  RULES: Lance. STUB scoring.

    loss_reaction: how they'd react to a big drop: "sell", "wait", or "buy_more".
    """
    score = 1
    score += 1 if timeline_years >= 5 else 0
    score += 1 if timeline_years >= 10 else 0
    score += {"sell": -1, "wait": 0, "buy_more": 1}.get(loss_reaction, 0)
    score += 1 if age < 30 else 0
    return min(4, max(1, score))
