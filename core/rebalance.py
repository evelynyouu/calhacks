"""Drift and rebalancing.  OWNER: Saahil.  STUB."""
from __future__ import annotations

from core.models import Portfolio


def allocate_new_money(p: Portfolio, current: dict[str, float], amount: float) -> dict[str, float]:
    """Send new cash to the most underweight funds first. Returns {ticker: dollars}."""
    total = sum(current.values()) + amount
    gaps = {h.ticker: max(0.0, h.weight * total - current.get(h.ticker, 0.0)) for h in p.holdings}
    gap_sum = sum(gaps.values()) or 1.0
    return {t: round(amount * g / gap_sum, 2) for t, g in gaps.items() if g > 0}


def drift(p: Portfolio, current: dict[str, float]) -> dict[str, float]:
    total = sum(current.values()) or 1.0
    return {h.ticker: round(current.get(h.ticker, 0.0) / total - h.weight, 4) for h in p.holdings}
