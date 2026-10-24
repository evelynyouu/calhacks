"""Drift and rebalancing.  OWNER: Saahil."""
from __future__ import annotations

from core.models import Portfolio

MIN_TRADE = 1.0   # dollars; smaller trades aren't worth placing


def allocate_new_money(p: Portfolio, current: dict[str, float], amount: float) -> dict[str, float]:
    """Send new cash to the most underweight funds first. Returns {ticker: dollars}."""
    total = sum(current.values()) + amount
    gaps = {h.ticker: max(0.0, h.weight * total - current.get(h.ticker, 0.0)) for h in p.holdings}
    gap_sum = sum(gaps.values()) or 1.0
    return {t: round(amount * g / gap_sum, 2) for t, g in gaps.items() if g > 0}


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
