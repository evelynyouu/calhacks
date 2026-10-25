"""Allocation engine: the customer's own index.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps), Evelyn (custom index).

A fund, in slices (core/models.SLICES):
  bonds     BND; the share follows the risk score (65% at 0 down to 10% at 100, in 5-point steps)
  intl      VXUS, INTL_SHARE of the stock part (only a handful of foreign companies trade in the US)
  us_core   about 55 big US companies (core/universe.py), weighted by size like an index fund, none over
            CORE_NAME_CAP of the slice
  spend     companies where the customer spends money (core/spending.py), by dollars, SPEND_CAP_BY_RISK
  interest  each interest's THEME_PICKS biggest companies, or its theme fund if we hold none
  added     companies the customer added (each capped, and capped in total, by risk level)
Spend, interest and added come out of the US index, so the bond and international shares are never
touched. A company held for two reasons is one position, and no position reaches COMPANY_CAP. Every
decision comes with a Reason. Choosing the score from the answers, with the horizon and max-drop
ceilings, is core/builder.py.
"""
from __future__ import annotations

import math
from functools import lru_cache

from core import market, universe
from core.market import COMPANIES, ETFS, HOLDINGS
from core.models import WARNING_KINDS, Holding, Portfolio, Reason

RISK_LABELS = {1: "Conservative", 2: "Moderate", 3: "Growth", 4: "Aggressive"}
LEVEL_BAND = 25           # score 0-24 -> level 1, 25-49 -> 2, 50-74 -> 3, 75-100 -> 4
# Bonds: 65% at score 0 down to 10% at 100, in 5-point steps, so a score of 49 and 50 differ by at
# most one step. LEVEL_BONDS is each level's share when a caller gives only a level.
BOND_MAX, BOND_MIN, BOND_STEP = 0.65, 0.10, 0.05
LEVEL_BONDS = {1: 0.65, 2: 0.45, 3: 0.25, 4: 0.10}
US_SHARE = 0.70           # of the stock part; the rest is international, roughly the world market's split
INTL_SHARE = 1 - US_SHARE
CORE_NAME_CAP = 0.06      # of the US index: about the biggest company's share of the whole US market
COMPANY_CAP = 0.08        # of the fund, all slices together; matches the report's diversify rule
# Where you spend: a share of the fund by risk level, the biggest SPEND_PICKS companies by dollars, none
# under MIN_PICK. It personalizes the fund; it doesn't predict anything, and the reasons say so.
SPEND_CAP_BY_RISK = {1: 0.05, 2: 0.08, 3: 0.10, 4: 0.12}
SPEND_PICKS = 8
MIN_PICK = 0.005
THEME_PICKS = 2           # an interest becomes its theme's biggest companies; a theme with none keeps its fund
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
                    *, bond_share: float | None = None, score: int | None = None, spending=None) -> Portfolio:
    """The fund for a level (caps), a bond share (default: the level's), interests, added companies and a
    spending profile (core/spending.Spend list; None = not given, [] = no purchases found)."""
    risk_level = min(4, max(1, int(risk_level)))
    bond_share = LEVEL_BONDS[risk_level] if bond_share is None else bond_share
    kind = RISK_LABELS[risk_level].lower()
    reasons: list[Reason] = []
    parts, budgets = _compose(risk_level, interests, stocks, bond_share, spending, reasons)
    holdings = _holdings(parts)
    held_funds = [t for t in budgets if t in parts["interest"]]   # interests that stayed funds
    overlaps = {f: _overlap_score(f, list(budgets)) for f in budgets}
    reasons = (_holding_reasons(holdings, parts, kind, score, overlaps) + reasons + _overlap_reasons(held_funds)
               + _lookthrough_reasons(holdings))
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _worst_of_parts(parts)
    exact = {k: {t: round(w, 4) for t, w in v.items() if round(w, 4) > 0} for k, v in parts.items()}
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr,
                     warnings_from(reasons), reasons, exact, score)


def worst_return_for(risk_level: int, interests, stocks, *, bond_share: float, spending=None) -> float:
    """Just the fund's real-worst-year return (percent) for these choices: what core/builder.py searches
    over for the max-drop ceiling, without building reasons."""
    parts, _ = _compose(min(4, max(1, int(risk_level))), interests, stocks, bond_share, spending, [])
    return _worst_of_parts(parts)[1]


def _worst_of_parts(parts: dict[str, dict[str, float]]) -> tuple[int, float]:
    """The fund's worst of 2008 and 2022 from its exact slice weights (so the max-drop search and the built fund
    agree to the decimal). The US index follows the market by design, so that part counts as VTI's real return;
    VXUS and BND are real; other companies are beta x VTI's real return (an estimate)."""
    weights: dict[str, float] = {"VTI": sum(parts.get("us_core", {}).values())}
    for k, part in parts.items():
        if k != "us_core":
            for t, w in part.items():
                weights[t] = weights.get(t, 0.0) + w
    return worst_year([Holding(t, t, w, "core", 0.0) for t, w in weights.items()])


def _compose(risk_level: int, interests, stocks, bond_share: float, spending, reasons: list[Reason]):
    """Every slice's weights: ({slice: {ticker: weight}}, interest budgets by theme fund). Adds the reasons
    for what was sized, trimmed or left out."""
    kind = RISK_LABELS[risk_level].lower()
    parts: dict[str, dict[str, float]] = {}   # slice -> {ticker: weight}

    tilts = list(dict.fromkeys(THEMES[i] for i in interests or [] if i in THEMES))
    budgets = _size_interests(tilts, TILT_CAP_BY_RISK[risk_level], kind, reasons)
    parts["interest"] = _interest_companies(budgets, reasons)

    picks = [t for t in dict.fromkeys(s.upper() for s in stocks or []) if t not in ETFS]
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    room = int(round(stock_cap / MIN_TILT))
    for t in picks[room:]:
        reasons.append(Reason("dropped", f"A {kind} fund has room for {room} single companies, so this one was "
                                         f"left out: {t}.", t, {"room": room}))
    picks = picks[:room]
    each = min(STOCK_EACH, stock_cap / len(picks)) if picks else 0.0
    parts["added"] = {t: each for t in picks}

    parts["spend"] = _spend_slice(spending, SPEND_CAP_BY_RISK[risk_level], reasons)

    stocks_share = 1 - bond_share
    parts["bonds"] = {"BND": bond_share}
    parts["intl"] = {"VXUS": stocks_share * INTL_SHARE}
    us_core = stocks_share * US_SHARE - sum(sum(parts[k].values()) for k in ("interest", "added", "spend"))
    parts["us_core"] = _core_weights(us_core)
    _cap_companies(parts, reasons)
    return parts, budgets


SLICE_ORDER = ("added", "spend", "interest", "us_core", "intl", "bonds")   # most personal first: a ticker's slice


def _holdings(parts: dict[str, dict[str, float]]) -> list[Holding]:
    """One Holding per ticker (its weights from every slice added up), in display order: bonds,
    international, the US index biggest first, then spend, interests and added by size."""
    total: dict[str, float] = {}
    for k in SLICE_ORDER:
        for t, w in parts.get(k, {}).items():
            total[t] = total.get(t, 0.0) + w
    home = {t: next(k for k in SLICE_ORDER if t in parts.get(k, {})) for t in total}
    rank = {k: i for i, k in enumerate(("bonds", "intl", "us_core", "spend", "interest", "added"))}
    size = {t: -universe.market_cap(t) if t in COMPANIES else 0.0 for t in total}
    ordered = sorted(total, key=lambda t: (rank[home[t]], -round(total[t], 4), size[t], t))
    holdings = [_holding(t, round(total[t], 4), home[t]) for t in ordered if round(total[t], 4) > 0]
    # rounding lands on VXUS (a fund: no per-company cap to push it past), else the biggest holding
    sink = next((h for h in holdings if h.ticker == "VXUS"), None) or max(holdings, key=lambda h: h.weight)
    sink.weight = round(sink.weight + 1 - sum(h.weight for h in holdings), 4)
    return holdings


ROLE_OF_SLICE = {"bonds": "core", "intl": "core", "us_core": "core", "spend": "spend", "interest": "tilt",
                 "added": "stock"}


def _holding(ticker: str, weight: float, slice_: str) -> Holding:
    role = ROLE_OF_SLICE[slice_]
    if ticker in ETFS:
        r = ETFS[ticker]
        return Holding(ticker, r["name"], weight, role, float(r["expense_ratio"]), "", slice_)
    sector = universe.sector(ticker) if ticker in COMPANIES else ""
    return Holding(ticker, market.name(ticker), weight, role, 0.0, sector, slice_)   # a company has no fund fee


def plain_index(total: float) -> dict[str, float]:
    """`total` spread over the US index by size with nothing personal: the report's benchmark for the US part."""
    return _core_weights(total)


def _core_weights(total: float) -> dict[str, float]:
    """The US index: each core company in proportion to its market cap, none over CORE_NAME_CAP of it."""
    return {t: total * w for t, w in _core_shares().items()}


@lru_cache(maxsize=1)
def _core_shares() -> dict[str, float]:
    names = universe.core_companies()
    return _capped_shares({t: universe.market_cap(t) for t in names}, CORE_NAME_CAP)


def _capped_shares(sizes: dict[str, float], cap: float) -> dict[str, float]:
    """Shares proportional to `sizes`, summing to 1, none above `cap`: cap the biggest, share the rest out
    in proportion, repeat until nothing is over (how capped index funds are weighted)."""
    fixed: dict[str, float] = {}
    free = dict(sizes)
    while free:
        room = 1 - sum(fixed.values())
        total = sum(free.values())
        over = {t for t, v in free.items() if room * v / total > cap}
        if not over:
            return {**fixed, **{t: room * v / total for t, v in free.items()}}
        for t in over:
            fixed[t] = cap
            del free[t]
    return fixed


def _interest_companies(budgets: dict[str, float], reasons: list[Reason]) -> dict[str, float]:
    """Each interest's budget split over its theme's biggest companies by size; its fund if there are none."""
    out: dict[str, float] = {}
    for fund, budget in budgets.items():
        theme = ETFS[fund]["theme"]
        names = universe.theme_companies(theme, THEME_PICKS)
        if not names:
            out[fund] = out.get(fund, 0.0) + budget
            reasons.append(Reason("tilt", f"We don't hold single {ETFS[fund]['label'].lower()} companies, so this "
                                          f"interest stays a fund: {fund}.", fund,
                                  {"theme": theme, "fund": True, "overlap": _overlap_score(fund, list(budgets))}))
            continue
        caps = {t: universe.market_cap(t) for t in names}
        for t in names:
            out[t] = out.get(t, 0.0) + budget * caps[t] / sum(caps.values())
        for t in names:
            reasons.append(Reason("tilt", f"{market.name(t)} ({t}) is one of the {len(names)} biggest "
                                          f"{ETFS[fund]['label']} companies we hold, and you picked "
                                          f"{ETFS[fund]['label']}.", t,
                                  {"theme": theme, "weight": round(budget * caps[t] / sum(caps.values()), 4),
                                   "overlap": _overlap_score(fund, list(budgets))}))
    return out


def _spend_slice(spending, cap: float, reasons: list[Reason]) -> dict[str, float]:
    """Companies where the customer spends, by dollars: the top SPEND_PICKS, none under MIN_PICK."""
    if spending is None:
        return {}
    from core.spending import company_spend   # spending reads the bank; imported here to keep this module light
    dollars = company_spend(spending)
    if not dollars:
        reasons.append(Reason("spend", "We didn't find purchases in the last 90 days, so there's no “where you "
                                       "spend” slice; that share stays in your US index.", None, {"empty": True}))
        return {}
    ranked = sorted(dollars, key=lambda t: (-dollars[t], t))
    kept = ranked[:SPEND_PICKS]
    while kept:
        total = sum(dollars[t] for t in kept)
        small = [t for t in kept if cap * dollars[t] / total < MIN_PICK]
        if not small:
            break
        kept.remove(small[-1])
    for t in ranked:
        if t not in kept:
            reasons.append(Reason("spend", f"Your spending that maps to {market.name(t)} (${dollars[t]:,.0f}) is "
                                           f"too small next to the rest to hold on its own.", t,
                                  {"dropped": True, "dollars": round(dollars[t], 2)}))
    total = sum(dollars[t] for t in kept)
    out = {t: math.floor(cap * dollars[t] / total * 1e4) / 1e4 for t in kept}   # down, so rounding can't pass the cap
    from core.spending import company_spend_detail
    detail = company_spend_detail(spending)
    for s in spending:
        if s.via == "category" and not s.ticker:
            from core.spending import CATEGORY_LEADERS
            leaders = [t for t in CATEGORY_LEADERS.get(s.category, []) if t in out]
            if leaders:
                reasons.append(Reason("spend", f"{s.merchant} isn't a public company, so your "
                                               f"{s.category.lower()} spending there (${s.dollars:,.0f}) goes to the "
                                               f"biggest {s.category.lower()} companies we hold: "
                                               f"{', '.join(market.name(t) for t in leaders)}.", None,
                                      {"merchant": s.merchant, "category": s.category, "tickers": leaders}))
    for t, w in out.items():
        where = " and ".join(f"${d:,.0f} at {m}" if how == "brand" else f"${d:,.0f} of your spending at {m}"
                             for m, d, how in sorted(detail.get(t, []), key=lambda x: -x[1]))
        reasons.append(Reason("spend", f"{share(w)} in {market.name(t)} ({t}): you spent {where} in the last 90 "
                                       f"days. This personalizes your fund; it isn't a prediction that the company "
                                       f"will do well.", t, {"weight": round(w, 4), "dollars": round(dollars[t], 2)}))
    return out


def _cap_companies(parts: dict[str, dict[str, float]], reasons: list[Reason]) -> None:
    """No company (all its slices together) reaches COMPANY_CAP. The excess comes off its US-index part
    first, then its other parts, and goes to the rest of the US index in proportion."""
    limit = COMPANY_CAP - 0.001
    for _ in range(5):   # moving weight can push another company over; a few passes settle it
        total: dict[str, float] = {}
        for k in ("us_core", "spend", "interest", "added"):
            for t, w in parts[k].items():
                total[t] = total.get(t, 0.0) + w
        over = {t: w - limit for t, w in total.items() if w > limit}
        if not over:
            return
        freed = 0.0
        for t, excess in over.items():
            before = total[t]
            for k in ("us_core", "interest", "spend", "added"):
                take = min(excess, parts[k].get(t, 0.0))
                if take:
                    parts[k][t] -= take
                    excess -= take
                    freed += take
            reasons.append(Reason("cap", f"{market.name(t)} would have been {share(before)} of your fund counting "
                                         f"every reason it's in it; we keep any one company under "
                                         f"{share(COMPANY_CAP)}, so it's {share(before - over[t] + excess)}.", t,
                                  {"before": round(before, 4), "cap": COMPANY_CAP}))
        others = {t: w for t, w in parts["us_core"].items() if t not in over and w > 0}
        base = sum(others.values())
        for t, w in others.items():
            parts["us_core"][t] = w + freed * w / base
        for k in parts:
            parts[k] = {t: w for t, w in parts[k].items() if w > 1e-9}


def share(w: float) -> str:
    """A weight as reasons write it: one decimal, like the fund card ("7.4%", "40%")."""
    return f"{w * 100:.1f}".removesuffix(".0") + "%"


def _lower_first(label: str) -> str:
    """"US stocks" stays as is; "Bonds" -> "bonds" (mid-sentence)."""
    return label if label[:2].isupper() else label[:1].lower() + label[1:]


def warnings_from(reasons: list[Reason]) -> list[str]:
    return [r.text for r in reasons if r.kind in WARNING_KINDS or r.facts.get("warn")]


def _holding_reasons(holdings: list[Holding], parts: dict, kind: str, score: int | None,
                     overlaps: dict[str, float]) -> list[Reason]:
    out = []
    core_rank = {t: i + 1 for i, t in enumerate(universe.core_companies())}
    for h in holdings:
        pct = share(h.weight)
        if h.ticker == "BND":
            why = (f"A risk score of {score} out of 100 sets it: the share goes from 65% at 0 down to 10% at 100."
                   if score is not None else f"That's the bond share for a {kind} fund.")
            out.append(Reason("bonds", f"{pct} in bonds (BND). Bonds usually hold steady when stocks fall, so they "
                                       f"cushion drops. {why}", "BND", {"bond_share": h.weight, "score": score}))
        elif h.ticker == "VXUS":
            out.append(Reason("core", f"{pct} in {_lower_first(ETFS['VXUS']['label'])} (VXUS). {ETFS['VXUS']['about']}. "
                                      f"Only a handful of foreign companies trade in the US, so this part stays a "
                                      f"fund.", "VXUS", {"weight": h.weight}))
        if h.ticker in parts["us_core"]:
            w = parts["us_core"][h.ticker]
            out.append(Reason("core", f"{share(w)} in {h.name} ({h.ticker}): number {core_rank[h.ticker]} by size "
                                      f"of the US companies in your index, in {h.sector}. Bigger companies get more, "
                                      f"like an index fund; none gets more than {share(CORE_NAME_CAP)} of your US "
                                      f"index.", h.ticker, {"weight": round(w, 4), "rank": core_rank[h.ticker]}))
        if h.ticker in parts["added"]:
            out.append(Reason("stock", f"{share(parts['added'][h.ticker])} in {h.name} ({h.ticker}) because you added "
                                       f"it. One company can swing on its own news, so each is kept small.", h.ticker,
                              {"weight": parts["added"][h.ticker]}))
        if h.ticker in parts["interest"] and h.ticker in ETFS:
            out.append(Reason("tilt", f"{pct} in {h.ticker} because you picked {ETFS[h.ticker]['label']}. "
                                      f"{ETFS[h.ticker]['about']}.", h.ticker,
                              {"weight": h.weight, "overlap": overlaps.get(h.ticker, 0.0)}))
        held_in = [k for k in ("us_core", "spend", "interest", "added") if parts[k].get(h.ticker)]
        if len(held_in) > 1:
            out.append(Reason("combined", f"{h.name} is in your fund for {len(held_in)} reasons, so it's {pct} in all.",
                              h.ticker, {"slices": held_in}))
    return out


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
    # rounded down when trimmed, so rounding can't push the total past the cap
    sizes = {t: round(max(MIN_TILT, (math.floor(wanted[t] * scale * 1e4) / 1e4) if scale < 1 else wanted[t]), 4)
             for t in kept}
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
    out = []   # interest funds that share companies; a company held twice directly gets a "combined" reason
    for i, a in enumerate(tilts):
        for b in tilts[i + 1:]:
            wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
            shared = sorted(set(wa) & set(wb), key=lambda c: -(wa[c] + wb[c]))
            if shared and _overlap(a, b) >= OVERLAP_WARN:
                out.append(Reason("overlap", f"{a} and {b} both hold {', '.join(shared)}, so those companies "
                                             f"count twice.", a, {"with": b, "shared": shared}))
    return out


def lookthrough(holdings: list[Holding]) -> dict:
    """Exposure of the whole fund to each company and sector. Companies held directly count in full;
    funds count through their biggest holdings in data/holdings.csv (APPROXIMATE: top holdings only),
    and an interest fund's whole weight counts toward its theme's sector."""
    companies: dict[str, float] = {}
    via: dict[str, list[str]] = {}
    sectors: dict[str, float] = {}

    def add(c, w, how):
        companies[c] = companies.get(c, 0.0) + w
        via.setdefault(c, []).append(how)

    for h in holdings:
        if h.ticker in COMPANIES:
            add(h.ticker, h.weight, "directly")
            sectors[_sector_of(h.ticker)] = sectors.get(_sector_of(h.ticker), 0.0) + h.weight
            continue
        if h.ticker in ETFS and ETFS[h.ticker]["role"] == "tilt":
            s = _fund_sector(h.ticker)
            sectors[s] = sectors.get(s, 0.0) + h.weight
        for c, w in HOLDINGS.get(h.ticker, []):
            add(c, h.weight * w, h.ticker)
            if h.ticker == "VXUS":
                sectors[_sector_of(c)] = sectors.get(_sector_of(c), 0.0) + h.weight * w
    return {"companies": {c: round(w, 4) for c, w in companies.items()}, "via": via,
            "sectors": {s: round(w, 4) for s, w in sectors.items()}}


def _sector_of(company: str) -> str:
    return universe.sector(company) if company in COMPANIES else "Other"


def _fund_sector(fund: str) -> str:
    """An interest fund's sector: its companies' sector, or its own label (Gold, Emerging markets...)."""
    theme = ETFS[fund]["theme"]
    names = universe.theme_companies(theme, 1)
    return universe.sector(names[0]) if names else ETFS[fund]["label"]


def _lookthrough_reasons(holdings: list[Holding]) -> list[Reason]:
    lt = lookthrough(holdings)
    out = []
    for c, w in sorted(lt["companies"].items(), key=lambda cw: -cw[1]):
        if w >= CONCENTRATION_NOTE:
            out.append(Reason("concentration", f"About {w:.0%} of your fund ends up in {market.name(c)} (through "
                                               f"{', '.join(lt['via'][c])}). The report suggests spreading out once one "
                                               f"company passes {CONCENTRATION_NOTE:.0%} (approximate: we only see each "
                                               f"fund's top holdings).", c, {"share": w, "via": lt["via"][c]}))
    # The plain index: the same US stock share spread over the US index by size, nothing personal
    us = sum(h.weight for h in holdings if h.ticker not in ("BND", "VXUS"))
    plain = [Holding(t, "", w, "core", 0.0, universe.sector(t), "us_core") for t, w in _core_weights(us).items()]
    market_mix = lookthrough(plain + [h for h in holdings if h.ticker == "VXUS"])["sectors"]
    for s, w in sorted(lt["sectors"].items(), key=lambda sw: -sw[1]):
        base = market_mix.get(s, 0.0)
        if s != "Other" and w - base >= SECTOR_EXCESS:
            out.append(Reason("sector", f"About {w:.0%} of your fund is in {s}, against about {base:.0%} in a plain "
                                        f"index of the same companies. That's your call; it just means more of your "
                                        f"money rides on one part of the economy (approximate).", None,
                              {"sector": s, "share": w, "market": base}))
    return out
