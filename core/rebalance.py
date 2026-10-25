"""Drift and rebalancing.  OWNER: Saahil."""
from __future__ import annotations

from core.models import Portfolio

MIN_TRADE = 1.0   # dollars; smaller trades aren't worth placing
# A fund is "off target" when it's this far from its target weight: 5 points, or 25% of the target if that's
# smaller (so a 2% slice that doubles is caught), as long as the gap is worth at least DRIFT_MIN_DOLLARS.
DRIFT_ABS = 0.05
DRIFT_REL = 0.25
DRIFT_MIN_DOLLARS = 25.0


def allocate_new_money(p: Portfolio, current: dict[str, float], amount: float) -> dict[str, float]:
    """Send new cash to the most underweight funds first. Returns {ticker: dollars}."""
    total = sum(current.values()) + amount
    gaps = {h.ticker: max(0.0, h.weight * total - current.get(h.ticker, 0.0)) for h in p.holdings}
    gap_sum = sum(gaps.values()) or 1.0
    out = {t: round(amount * g / gap_sum, 2) for t, g in gaps.items() if g > 0}
    if out:   # cents lost to rounding (a custom index has ~60 holdings) go to the biggest buy
        top = max(out, key=out.get)
        out[top] = round(out[top] + round(amount, 2) - round(sum(out.values()), 2), 2)
    return out


def drift(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """Actual weight minus target weight, per ticker. Holdings no longer in the fund have target 0."""
    total = sum(current.values()) or 1.0
    targets = {h.ticker: h.weight for h in p.holdings}
    return {t: round(current.get(t, 0.0) / total - targets.get(t, 0.0), 4) for t in {**targets, **current}}


def rebalance_trades(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """Dollars to trade per ticker to get back to target: positive buys, negative sells."""
    total = sum(current.values())
    targets = {h.ticker: h.weight for h in p.holdings}
    trades = {t: round(targets.get(t, 0.0) * total - current.get(t, 0.0), 2) for t in {**targets, **current}}
    return {t: v for t, v in trades.items() if abs(v) >= MIN_TRADE}


def band(target: float) -> float:
    """How far (in weight) a holding may sit from its target before it counts as off target."""
    return min(DRIFT_ABS, DRIFT_REL * target)


def off_target(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    """{ticker: drift} for holdings outside their band, ignoring gaps too small in dollars to matter."""
    total = sum(current.values())
    targets = {h.ticker: h.weight for h in p.holdings}
    return {t: d for t, d in drift(p, current).items()
            if abs(d) >= band(targets.get(t, 0.0)) and abs(d) * total >= DRIFT_MIN_DOLLARS}
