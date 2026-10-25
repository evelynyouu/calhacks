"""Allocation engine.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps).

A fund = a broad core (US stocks, international stocks, bonds) plus a slice for the customer's
interests, one fund per interest, plus any single companies they picked (each capped, and capped in
total, by risk level). The bond share follows the risk score smoothly (65% at 0 down to 10% at 100,
in 5-point steps); the interest and company slices come out of the stock part, so the bond share is
never touched. Every decision comes with a Reason (core/models.py). Choosing the score from the
intake answers, with the horizon and max-drop ceilings, is core/builder.py.
"""
from __future__ import annotations

from core import market
from core.market import ETFS, HOLDINGS
from core.models import WARNING_KINDS, Holding, Portfolio, Reason

RISK_LABELS = {1: "Conservative", 2: "Moderate", 3: "Growth", 4: "Aggressive"}
LEVEL_BAND = 25           # score 0-24 -> level 1, 25-49 -> 2, 50-74 -> 3, 75-100 -> 4
# Bonds: 65% at score 0 down to 10% at 100, in 5-point steps, so a score of 49 and 50 differ by at
# most one step. LEVEL_BONDS is each level's share when a caller gives only a level.
BOND_MAX, BOND_MIN, BOND_STEP = 0.65, 0.10, 0.05
LEVEL_BONDS = {1: 0.65, 2: 0.45, 3: 0.25, 4: 0.10}
US_SHARE = 0.70           # of the stock part; the rest is international, roughly the world market's split
# Real calendar-year total returns (percent, dividends reinvested, nominal) in the two years that hurt
# most: 2008, when stocks crashed and bonds rose, and 2022, when both fell. Source:
# totalrealreturns.com/n/VTI,VGTSX,BND,VXUS (read 2026-10-24). VXUS launched in 2011, so its 2008 is
# Vanguard's Total International Stock Index fund (VGTSX), which tracks the same index.
RETURNS = {"VTI": {2008: -36.99, 2022: -19.52}, "VXUS": {2008: -44.07, 2022: -16.09},
           "BND": {2008: 6.87, 2022: -13.11}}
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


def bond_share_for(score: float) -> float:
    """Risk score 0..100 -> bond share, BOND_MAX down to BOND_MIN in BOND_STEP steps."""
    raw = BOND_MAX - (BOND_MAX - BOND_MIN) * min(100, max(0, score)) / 100
    return round(round(raw / BOND_STEP) * BOND_STEP, 2)


def level_for_score(score: float) -> int:
    return min(4, 1 + int(score) // LEVEL_BAND)


def worst_year(holdings: list[Holding]) -> tuple[int, float]:
    """The worse of the fund's real 2008 and 2022, held at these weights all year. The core funds use
    their real returns; interest funds and single companies are APPROXIMATED as their beta times US
    stocks' return (we don't have their 2008 prices)."""
    def year(y):
        return sum(h.weight * (RETURNS[h.ticker][y] if h.ticker in RETURNS else market.beta(h.ticker) * RETURNS["VTI"][y])
                   for h in holdings)
    y = min(RETURNS["VTI"], key=year)
    return y, round(year(y), 1)


def build_portfolio(risk_level: int, interests: list[str] | None = None, stocks: list[str] | None = None,
                    *, bond_share: float | None = None, score: int | None = None) -> Portfolio:
    """The fund for a level (interest and company caps) and a bond share (default: the level's)."""
    risk_level = min(4, max(1, int(risk_level)))
    bond_share = LEVEL_BONDS[risk_level] if bond_share is None else bond_share
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
    rest = 1 - bond_share - tilt_total - each * len(picks)   # what's left of the stock part for the core
    weights = {"VTI": rest * US_SHARE, "VXUS": rest * (1 - US_SHARE), "BND": bond_share}
    for t in tilts:
        weights[t] = tilt_total / len(tilts)
    for t in picks:
        weights[t] = each

    roles = {t: "core" if t in RETURNS else "tilt" if t in ETFS else "stock" for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)
    reasons = _holding_reasons(holdings, kind, score) + reasons + _overlap_reasons(tilts)
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = worst_year(holdings)
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr,
                     warnings_from(reasons), reasons, score)


def warnings_from(reasons: list[Reason]) -> list[str]:
    return [r.text for r in reasons if r.kind in WARNING_KINDS or r.facts.get("warn")]


def _holding_reasons(holdings: list[Holding], kind: str, score: int | None) -> list[Reason]:
    out = []
    for h in holdings:
        pct = f"{h.weight:.0%}"
        if h.ticker == "BND":
            why = (f"A risk score of {score} out of 100 sets it: the share goes from 65% at 0 down to 10% at 100."
                   if score is not None else f"That's the bond share for a {kind} fund.")
            out.append(Reason("bonds", f"{pct} in bonds (BND). Bonds usually hold steady when stocks fall, so they "
                                       f"cushion drops. {why}", "BND", {"bond_share": h.weight, "score": score}))
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
