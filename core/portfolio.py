"""Allocation engine.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps).

A fund = a broad core (US stocks, international stocks, bonds) plus a slice for the customer's
interests, one fund per interest, plus any single companies they picked (each capped, and capped in
total, by risk level). The bond share follows the risk score smoothly (65% at 0 down to 10% at 100,
in 5-point steps); the interest and company slices come out of the stock part, so the bond share is
never touched. Every decision comes with a Reason (core/models.py). Choosing the score from the
intake answers, with the horizon and max-drop ceilings, is core/builder.py.
"""
from __future__ import annotations

import math

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
# Each interest gets up to this much, so one interest can't take the whole slice. A fund that swings more
# than SWING_REF a year (market moves plus its own) gets proportionally less, but never under MIN_TILT.
PER_INTEREST_CAP = 0.08
SWING_REF = 0.20
# Look-through notes (approximate: data/holdings.csv lists only each fund's biggest companies)
CONCENTRATION_NOTE = 0.08   # one company this share of the whole fund; matches the report's diversify rule
SECTOR_EXCESS = 0.10        # a sector this many points above what the whole US market would give
# Interest themes grouped into the sectors a look-through compares (themes not listed are their own)
SECTOR_OF_THEME = {t: "Technology" for t in ("tech", "ai_chips", "software", "cyber", "robotics")}
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
    sizes = _size_interests(tilts, cap, kind, reasons)

    picks = [t for t in dict.fromkeys(s.upper() for s in stocks or []) if t not in ETFS]
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    room = int(round(stock_cap / MIN_TILT))
    for t in picks[room:]:
        reasons.append(Reason("dropped", f"A {kind} fund has room for {room} single companies, so this one was "
                                         f"left out: {t}.", t, {"room": room}))
    picks = picks[:room]
    each = min(STOCK_EACH, stock_cap / len(picks)) if picks else 0.0

    rest = 1 - bond_share - sum(sizes.values()) - each * len(picks)   # what's left of the stock part for the core
    weights = {"VTI": rest * US_SHARE, "VXUS": rest * (1 - US_SHARE), "BND": bond_share, **sizes}
    for t in picks:
        weights[t] = each

    roles = {t: "core" if t in RETURNS else "tilt" if t in ETFS else "stock" for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)
    overlaps = {t: _overlap_score(t, list(sizes)) for t in sizes}
    reasons = (_holding_reasons(holdings, kind, score, overlaps) + reasons + _overlap_reasons(list(sizes))
               + _lookthrough_reasons(holdings))
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = worst_year(holdings)
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr,
                     warnings_from(reasons), reasons, score)


def share(w: float) -> str:
    """A weight as reasons write it: one decimal, like the fund card ("7.4%", "40%")."""
    return f"{w * 100:.1f}".removesuffix(".0") + "%"


def _lower_first(label: str) -> str:
    """"US stocks" stays as is; "Bonds" -> "bonds" (mid-sentence)."""
    return label if label[:2].isupper() else label[:1].lower() + label[1:]


def warnings_from(reasons: list[Reason]) -> list[str]:
    return [r.text for r in reasons if r.kind in WARNING_KINDS or r.facts.get("warn")]


def _holding_reasons(holdings: list[Holding], kind: str, score: int | None, overlaps: dict) -> list[Reason]:
    out = []
    for h in holdings:
        pct = share(h.weight)
        if h.ticker == "BND":
            why = (f"A risk score of {score} out of 100 sets it: the share goes from 65% at 0 down to 10% at 100."
                   if score is not None else f"That's the bond share for a {kind} fund.")
            out.append(Reason("bonds", f"{pct} in bonds (BND). Bonds usually hold steady when stocks fall, so they "
                                       f"cushion drops. {why}", "BND", {"bond_share": h.weight, "score": score}))
        elif h.role == "core":
            out.append(Reason("core", f"{pct} in {_lower_first(ETFS[h.ticker]['label'])} ({h.ticker}). "
                                      f"{ETFS[h.ticker]['about']}. Owning the whole market means no single "
                                      f"company can sink you.", h.ticker, {"weight": h.weight}))
        elif h.role == "tilt":
            out.append(Reason("tilt", f"{pct} in {h.ticker} because you picked {ETFS[h.ticker]['label']}. "
                                      f"{ETFS[h.ticker]['about']}.", h.ticker,
                              {"weight": h.weight, "overlap": overlaps.get(h.ticker, 0.0)}))
        else:
            out.append(Reason("stock", f"{pct} in {h.name} ({h.ticker}) because you added it. One company can swing "
                                       f"on its own news, so each is kept small.", h.ticker, {"weight": h.weight}))
    return out


def _holding(ticker: str, weight: float, role: str) -> Holding:
    if role == "stock":
        return Holding(ticker, market.name(ticker), weight, role, 0.0)   # owning a company directly has no fund fee
    r = ETFS[ticker]
    return Holding(ticker, r["name"], weight, role, float(r["expense_ratio"]))


def swing(ticker: str) -> float:
    """How much a fund moves in a typical year: its share of the market's moves plus its own."""
    r = ETFS[ticker]
    return math.hypot(float(r["beta"]) * market.MARKET_VOL, float(r["resid_vol"]))


def _overlap(a: str, b: str) -> float:
    """Share of fund a's money in companies fund b also holds (the smaller of the two weights each)."""
    wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
    return sum(min(wa[c], wb[c]) for c in set(wa) & set(wb))


def _overlap_score(t: str, tilts: list[str]) -> float:
    """How much an interest fund repeats what the fund already holds: the US core, and the other interests."""
    return round(_overlap(t, "VTI") + max((_overlap(t, u) for u in tilts if u != t), default=0.0), 4)


def _size_interests(tilts: list[str], cap: float, kind: str, reasons: list[Reason]) -> dict[str, float]:
    """Each interest gets PER_INTEREST_CAP, less if it swings more than SWING_REF (never under MIN_TILT).
    If together they pass the level's cap, each is trimmed by the same share, but none below MIN_TILT.
    Only when there isn't room for all of them at MIN_TILT is one left out: the one that overlaps most
    with the rest of the fund (ties: the one picked last)."""
    wanted = {t: max(MIN_TILT, PER_INTEREST_CAP * min(1.0, SWING_REF / swing(t))) for t in tilts}
    for t, w in wanted.items():
        if w < PER_INTEREST_CAP:
            reasons.append(Reason("swing", f"{ETFS[t]['label']} ({t}) swings more than most funds (about "
                                           f"{swing(t):.0%} a year, against {swing('VTI'):.0%} for US stocks), so it "
                                           f"gets {w:.1%} instead of the {PER_INTEREST_CAP:.0%} a steadier interest "
                                           f"gets.", t, {"swing": round(swing(t), 3), "size": round(w, 4)}))
    kept = list(tilts)
    while len(kept) * MIN_TILT > cap + 1e-9:
        scores = {t: _overlap_score(t, kept) for t in kept}
        out = max(reversed(kept), key=lambda t: scores[t])
        kept.remove(out)
        reasons.append(Reason("dropped", f"A {kind} fund keeps interests to {cap:.0%}, which is room for "
                                         f"{int(cap / MIN_TILT + 1e-9)} at {MIN_TILT:.0%} each, so this one was left out: "
                                         f"{ETFS[out]['label']}. Of your picks, it has the most overlap with what the "
                                         f"fund already holds.", out, {"overlap": scores[out], "cap": cap}))
    if not kept:
        return {}
    total = sum(wanted[t] for t in kept)
    scale = _fit_scale([wanted[t] for t in kept], cap) if total > cap else 1.0
    sizes = {t: round(max(MIN_TILT, wanted[t] * scale), 4) for t in kept}
    if scale < 1:
        for t in kept:
            reasons.append(Reason("cap", f"Your interests add up to {total:.0%}, more than the {cap:.0%} a {kind} fund "
                                         f"keeps for them, so each was trimmed by the same share (to no less than "
                                         f"{MIN_TILT:.0%}): {t} from {wanted[t]:.1%} to {sizes[t]:.1%}.", t,
                                  {"wanted": round(wanted[t], 4), "size": sizes[t], "cap": cap}))
    return sizes


def _fit_scale(wanted: list[float], cap: float) -> float:
    """The share s with sum(max(MIN_TILT, w * s)) == cap (bisection; the sum only grows with s)."""
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if sum(max(MIN_TILT, w * mid) for w in wanted) > cap:
            hi = mid
        else:
            lo = mid
    return lo


def _overlap_reasons(tilts: list[str]) -> list[Reason]:
    out = []
    for t in tilts:
        shared = _overlap(t, "VTI")
        if shared >= OVERLAP_WARN:
            top = [market.name(c) for c, _ in sorted(HOLDINGS[t], key=lambda cw: -cw[1])
                   if c in dict(HOLDINGS["VTI"])][:3]
            out.append(Reason("overlap", f"{t}'s biggest companies ({', '.join(top)}) are also VTI's biggest, so "
                                         f"adding {t} leans the fund further into them (approximate: we only see each "
                                         f"fund's top holdings).", t, {"with": "VTI", "shared": round(shared, 4)}))
    for i, a in enumerate(tilts):
        for b in tilts[i + 1:]:
            wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
            shared = sorted(set(wa) & set(wb), key=lambda c: -(wa[c] + wb[c]))
            if shared and _overlap(a, b) >= OVERLAP_WARN:
                out.append(Reason("overlap", f"{a} and {b} both hold {', '.join(shared)}, so those companies "
                                             f"count twice.", a, {"with": b, "shared": shared}))
    return out


def lookthrough(holdings: list[Holding]) -> dict:
    """APPROXIMATE exposure of the whole fund (core included) to each company and sector, from each fund's
    biggest holdings in data/holdings.csv plus companies held directly."""
    companies: dict[str, float] = {}
    via: dict[str, list[str]] = {}
    sectors: dict[str, float] = {}
    for h in holdings:
        if h.role == "stock":
            companies[h.ticker] = companies.get(h.ticker, 0.0) + h.weight
            via.setdefault(h.ticker, []).append("directly")
            sectors[_sector_of(h.ticker)] = sectors.get(_sector_of(h.ticker), 0.0) + h.weight
            continue
        if h.role == "tilt":
            s = SECTOR_OF_THEME.get(ETFS[h.ticker]["theme"], ETFS[h.ticker]["label"])
            sectors[s] = sectors.get(s, 0.0) + h.weight
        for c, w in HOLDINGS.get(h.ticker, []):
            companies[c] = companies.get(c, 0.0) + h.weight * w
            via.setdefault(c, []).append(h.ticker)
            if h.role == "core":
                sectors[_sector_of(c)] = sectors.get(_sector_of(c), 0.0) + h.weight * w
    return {"companies": {c: round(w, 4) for c, w in companies.items()}, "via": via,
            "sectors": {s: round(w, 4) for s, w in sectors.items()}}


def _sector_of(company: str) -> str:
    home = market.COMPANIES.get(company, {}).get("etf")
    if not home or home not in ETFS:
        return "Other"
    return SECTOR_OF_THEME.get(ETFS[home]["theme"], ETFS[home]["label"])


def _lookthrough_reasons(holdings: list[Holding]) -> list[Reason]:
    lt = lookthrough(holdings)
    out = []
    for c, w in sorted(lt["companies"].items(), key=lambda cw: -cw[1]):
        if w >= CONCENTRATION_NOTE:
            out.append(Reason("concentration", f"About {w:.0%} of your fund ends up in {market.name(c)} (through "
                                               f"{', '.join(lt['via'][c])}). The report suggests spreading out once one "
                                               f"company passes {CONCENTRATION_NOTE:.0%} (approximate: we only see each "
                                               f"fund's top holdings).", c, {"share": w, "via": lt["via"][c]}))
    stocks = sum(h.weight for h in holdings if h.ticker != "BND")
    market_mix = lookthrough([Holding("VTI", "", stocks, "core", 0.0)])["sectors"]   # all stocks in VTI instead
    for s, w in sorted(lt["sectors"].items(), key=lambda sw: -sw[1]):
        base = market_mix.get(s, 0.0)
        if s != "Other" and w - base >= SECTOR_EXCESS:
            out.append(Reason("sector", f"About {w:.0%} of your fund is in {s}, against about {base:.0%} if all your "
                                        f"stocks were in the whole US market (VTI). That's your call; it just means "
                                        f"more of your money rides on one part of the economy (approximate).", None,
                              {"sector": s, "share": w, "market": base}))
    return out
