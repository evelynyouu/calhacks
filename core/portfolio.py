"""Allocation engine.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps).

A fund = a broad core (US stocks, international stocks, bonds; the mix set by risk level) plus a
slice for the customer's interests, split evenly across one fund per interest, plus any single
companies they picked (each capped, and capped in total, by risk level). The interest and company
slices come out of the stock part of the core, so the bond share always matches the risk level.
Worst-year numbers are PLACEHOLDERS until real prices are cached.
"""
from __future__ import annotations

from core import market
from core.market import ETFS, HOLDINGS
from core.models import Holding, Portfolio

RISK_LABELS = {1: "Conservative", 2: "Moderate", 3: "Growth", 4: "Aggressive"}
# Core mix by risk level. Stocks are split about 70/30 US/international.
CORE_MIX_BY_RISK = {
    1: {"VTI": 0.25, "VXUS": 0.10, "BND": 0.65},
    2: {"VTI": 0.38, "VXUS": 0.17, "BND": 0.45},
    3: {"VTI": 0.52, "VXUS": 0.23, "BND": 0.25},
    4: {"VTI": 0.63, "VXUS": 0.27, "BND": 0.10},
}
# Share of the fund that goes to interests, by risk level. Sector funds swing far harder than
# the core (ICLN lost over half its value 2021–23), so a conservative fund only gets a taste.
TILT_CAP_BY_RISK = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.25}
MIN_TILT = 0.02           # smaller slices than this aren't worth holding
# Single companies: one company can sink on its own news, so each is small and the total is capped.
STOCK_EACH = 0.05
STOCK_CAP_BY_RISK = {1: 0.05, 2: 0.08, 3: 0.10, 4: 0.15}
THEMES = {r["theme"]: t for t, r in ETFS.items() if r["role"] == "tilt"}   # theme -> ticker
THEME_LABELS = {r["theme"]: r["label"] for r in ETFS.values() if r["role"] == "tilt"}
OVERLAP_WARN = 0.15       # two interest funds sharing this much in the same companies get a note
# PLACEHOLDER worst calendar year per risk level (2022, when stocks and bonds fell together)
_WORST = {1: (2022, -13.9), 2: (2022, -15.6), 3: (2022, -16.9), 4: (2022, -18.1)}


def build_portfolio(risk_level: int, interests: list[str] | None = None, stocks: list[str] | None = None) -> Portfolio:
    risk_level = min(4, max(1, int(risk_level)))
    cap = TILT_CAP_BY_RISK[risk_level]
    tilts = list(dict.fromkeys(THEMES[i] for i in interests or [] if i in THEMES))
    warnings: list[str] = []

    room = int(round(cap / MIN_TILT))
    if len(tilts) > room:
        dropped = [ETFS[t]["label"] for t in tilts[room:]]
        warnings.append(f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} interests, "
                        f"so these were left out: {', '.join(dropped)}.")
        tilts = tilts[:room]
    warnings += _overlap_warnings(tilts)

    picks = [t for t in dict.fromkeys(s.upper() for s in stocks or []) if t not in ETFS]
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    room = int(round(stock_cap / MIN_TILT))
    if len(picks) > room:
        warnings.append(f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} single companies, "
                        f"so these were left out: {', '.join(picks[room:])}.")
        picks = picks[:room]
    each = min(STOCK_EACH, stock_cap / len(picks)) if picks else 0.0

    tilt_total = cap if tilts else 0.0
    core = CORE_MIX_BY_RISK[risk_level]
    equity = core["VTI"] + core["VXUS"]
    keep = (equity - tilt_total - each * len(picks)) / equity
    weights = {"VTI": core["VTI"] * keep, "VXUS": core["VXUS"] * keep, "BND": core["BND"]}
    for t in tilts:
        weights[t] = tilt_total / len(tilts)
    for t in picks:
        weights[t] = each

    roles = {t: "core" if t in core else "tilt" if t in ETFS else "stock" for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[risk_level]
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr, warnings)


def _holding(ticker: str, weight: float, role: str) -> Holding:
    if role == "stock":
        return Holding(ticker, market.name(ticker), weight, role, 0.0)   # owning a company directly has no fund fee
    r = ETFS[ticker]
    return Holding(ticker, r["name"], weight, role, float(r["expense_ratio"]))


def _overlap_warnings(tilts: list[str]) -> list[str]:
    out = []
    for i, a in enumerate(tilts):
        for b in tilts[i + 1:]:
            wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
            shared = sorted(set(wa) & set(wb), key=lambda c: -(wa[c] + wb[c]))
            if shared and sum(min(wa[c], wb[c]) for c in shared) >= OVERLAP_WARN:
                out.append(f"{a} and {b} both hold {', '.join(shared)}, so those companies count twice.")
    return out
