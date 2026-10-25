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
from core.models import WARNING_KINDS, Holding, Portfolio, Reason

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
    kind = RISK_LABELS[risk_level].lower()
    cap = TILT_CAP_BY_RISK[risk_level]
    tilts = list(dict.fromkeys(THEMES[i] for i in interests or [] if i in THEMES))
    reasons: list[Reason] = []

    room = int(round(cap / MIN_TILT))
    for t in tilts[room:]:
        reasons.append(Reason("dropped", f"A {kind} fund has room for {room} interests, so this one was left out: "
                                         f"{ETFS[t]['label']}.", t, {"room": room}))
    tilts = tilts[:room]

    picks = [t for t in dict.fromkeys(s.upper() for s in stocks or []) if t not in ETFS]
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    room = int(round(stock_cap / MIN_TILT))
    for t in picks[room:]:
        reasons.append(Reason("dropped", f"A {kind} fund has room for {room} single companies, so this one was "
                                         f"left out: {t}.", t, {"room": room}))
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
    reasons = _holding_reasons(holdings, kind) + reasons + _overlap_reasons(tilts)
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[risk_level]
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr,
                     [r.text for r in reasons if r.kind in WARNING_KINDS], reasons)


def _holding_reasons(holdings: list[Holding], kind: str) -> list[Reason]:
    out = []
    for h in holdings:
        pct = f"{h.weight:.0%}"
        if h.ticker == "BND":
            out.append(Reason("bonds", f"{pct} in bonds (BND). Bonds usually hold steady when stocks fall, so they "
                                       f"cushion drops. That's the bond share for a {kind} fund.", "BND",
                              {"bond_share": h.weight}))
        elif h.role == "core":
            out.append(Reason("core", f"{pct} in {ETFS[h.ticker]['label'].lower()} ({h.ticker}): "
                                      f"{ETFS[h.ticker]['about'].lower()}. Owning the whole market means no single "
                                      f"company can sink you.", h.ticker, {"weight": h.weight}))
        elif h.role == "tilt":
            out.append(Reason("tilt", f"{pct} in {h.ticker} because you picked {ETFS[h.ticker]['label']}: "
                                      f"{ETFS[h.ticker]['about'].lower()}.", h.ticker, {"weight": h.weight}))
        else:
            out.append(Reason("stock", f"{pct} in {h.name} ({h.ticker}) because you added it. One company can swing "
                                       f"on its own news, so each is kept small.", h.ticker, {"weight": h.weight}))
    return out


def _holding(ticker: str, weight: float, role: str) -> Holding:
    if role == "stock":
        return Holding(ticker, market.name(ticker), weight, role, 0.0)   # owning a company directly has no fund fee
    r = ETFS[ticker]
    return Holding(ticker, r["name"], weight, role, float(r["expense_ratio"]))


def _overlap_reasons(tilts: list[str]) -> list[Reason]:
    out = []
    for i, a in enumerate(tilts):
        for b in tilts[i + 1:]:
            wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
            shared = sorted(set(wa) & set(wb), key=lambda c: -(wa[c] + wb[c]))
            if shared and sum(min(wa[c], wb[c]) for c in shared) >= OVERLAP_WARN:
                out.append(Reason("overlap", f"{a} and {b} both hold {', '.join(shared)}, so those companies "
                                             f"count twice.", a, {"with": b, "shared": shared}))
    return out
