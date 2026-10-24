"""Backtest, Monte Carlo, crash test.  OWNER: Saahil.

STUB: closed-form lognormal bands with placeholder return/vol per risk level.
Saahil replaces with a bootstrap of cached monthly returns.
"""
from __future__ import annotations

import math

from core.models import CrashTestResult, Portfolio, SimResult
from core.portfolio import build_portfolio

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


def crash_test(p: Portfolio, balance: float, choice: str, interests: list[str] | None = None,
               drop_pct: float = -30.0) -> CrashTestResult:
    loss = round(balance * drop_pct / 100, 2)
    facts = [f"A {abs(drop_pct):.0f}% drop on ${balance:,.0f} is ${abs(loss):,.0f} on paper."]
    new = None
    if choice == "sell" and p.risk_level > 1:
        new = build_portfolio(p.risk_level - 1, interests)
        facts.append(f"Selling would lock in that loss. Moving you from {p.risk_label} to {new.risk_label}.")
        facts.append(f"Worst historical year goes from {p.worst_year_return:.1f}% to {new.worst_year_return:.1f}%.")
    elif choice == "sell":
        facts.append("You're already at the most conservative level.")
    else:
        facts.append(f"Holding through it keeps your {p.risk_label} fund.")
    return CrashTestResult(drop_pct, loss, choice, new is not None, new, facts)
