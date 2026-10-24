"""10-year outcome range.  OWNER: Saahil.

STUB: closed-form lognormal bands with placeholder return/vol per risk level.
Saahil replaces with a bootstrap of cached monthly returns.
"""
from __future__ import annotations

import math

from core.models import Portfolio, SimResult

# PLACEHOLDER annual (mean, vol) by risk level
_PARAMS = {1: (0.045, 0.06), 2: (0.055, 0.08), 3: (0.065, 0.11), 4: (0.075, 0.14)}


def simulate(p: Portfolio, start: float, monthly: float, years: int = 10) -> SimResult:
    mu, vol = _PARAMS[p.risk_level]
    out = {"p10": [], "p50": [], "p90": []}
    for y in range(years + 1):
        base = start * (1 + mu) ** y + (monthly * 12 * (((1 + mu) ** y - 1) / mu) if y else 0)
        spread = vol * math.sqrt(y)
        out["p10"].append(round(base * math.exp(-1.2816 * spread), 2))
        out["p50"].append(round(base, 2))
        out["p90"].append(round(base * math.exp(1.2816 * spread), 2))
    return SimResult(list(range(years + 1)), out["p10"], out["p50"], out["p90"],
                     monthly, start, round(start + monthly * 12 * years, 2))

