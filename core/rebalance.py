"""Drift and rebalancing.  OWNER: Saahil.

A custom index (core/portfolio.py) holds ~55 companies weighted by size. Like any market-cap index fund,
it doesn't trade when one company outgrows another: inside the US index, each company's target follows its
current value (targets()); only the index's total share, and the personal picks, count as drift.
"""
from __future__ import annotations

from core.models import Portfolio

MIN_TRADE = 1.0   # dollars; smaller trades aren't worth placing
# A fund is "off target" when it's this far from its target weight: 5 points, or 25% of the target if that's
# smaller (so a 2% slice that doubles is caught), as long as the gap is worth at least DRIFT_MIN_DOLLARS.
DRIFT_ABS = 0.05
DRIFT_REL = 0.25
DRIFT_MIN_DOLLARS = 25.0


def targets(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """Target weight per ticker. For a custom index, the US index's companies share its target in proportion to
    what they're worth now (personal parts of a position stay fixed), so price moves inside it aren't drift."""
    base = {h.ticker: h.weight for h in p.holdings}
    index = (getattr(p, "parts", None) or {}).get("us_core", {})
    total = sum(current.values())
    if not index or not total:
        return base
    personal = {t: base[t] - index.get(t, 0.0) for t in index}       # the rest of each position is a fixed pick
    held = {t: max(0.0, current.get(t, 0.0) - personal[t] * total) for t in index}
    held_sum = sum(held.values())
    if not held_sum:
        return base
    share = sum(index.values())
    return {**base, **{t: personal[t] + share * held[t] / held_sum for t in index}}


NEW_MONEY_PICKS = 10   # a paycheck buys at most this many holdings: a 58-holding fund would otherwise get ~58 tiny orders


def allocate_new_money(p: Portfolio, current: dict[str, float], amount: float, picks: int | None = None) -> dict[str, float]:
    """Send new cash to the most underweight holdings first. Returns {ticker: dollars}, none under MIN_TRADE
    (a custom index has ~60 holdings; tiny orders aren't worth placing and a broker may refuse them).

    `picks` limits how many holdings get money: the ones furthest below target, which catch up first; the rest
    catch up on later paychecks. None means every underweight holding, which is what a first deposit wants."""
    if amount < MIN_TRADE:
        return {}                                   # not worth an order; the money waits for the next paycheck
    total = sum(current.values()) + amount
    goal = targets(p, current)
    gaps = {t: max(0.0, w * total - current.get(t, 0.0)) for t, w in goal.items()}
    gaps = {t: g for t, g in gaps.items() if g > 0}
    if picks:
        gaps = dict(sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))[:picks])
    while gaps:
        gap_sum = sum(gaps.values())
        small = [t for t, g in gaps.items() if amount * g / gap_sum < MIN_TRADE]
        if not small or len(small) == len(gaps):
            break
        del gaps[min(small, key=gaps.get)]   # drop the smallest, then share again
    if not gaps:
        return {}
    gap_sum = sum(gaps.values())
    out = {t: round(amount * g / gap_sum, 2) for t, g in gaps.items()}
    top = max(out, key=out.get)   # cents lost to rounding go to the biggest buy
    out[top] = round(out[top] + round(amount, 2) - round(sum(out.values()), 2), 2)
    return out


def drift(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """Actual weight minus target weight, per ticker. Holdings no longer in the fund have target 0."""
    total = sum(current.values()) or 1.0
    goal = targets(p, current)
    return {t: round(current.get(t, 0.0) / total - goal.get(t, 0.0), 4) for t in {**goal, **current}}


def rebalance_trades(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """Dollars to trade per ticker to get back to target: positive buys, negative sells."""
    total = sum(current.values())
    goal = targets(p, current)
    trades = {t: round(goal.get(t, 0.0) * total - current.get(t, 0.0), 2) for t in {**goal, **current}}
    return {t: v for t, v in trades.items() if abs(v) >= MIN_TRADE}


def band(target: float) -> float:
    """How far (in weight) a holding may sit from its target before it counts as off target."""
    return min(DRIFT_ABS, DRIFT_REL * target)


def off_target(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """{ticker: drift} for holdings outside their band, ignoring gaps too small in dollars to matter."""
    total = sum(current.values())
    goal = targets(p, current)
    return {t: d for t, d in drift(p, current).items()
            if abs(d) >= band(goal.get(t, 0.0)) and abs(d) * total >= DRIFT_MIN_DOLLARS}
