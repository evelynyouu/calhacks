"""10-year outcome range.  OWNER: Saahil.  Assumptions and the portfolio model: Ethan.

The range comes from the fund's own holdings: each fund has an expected return and volatility in
data/assumptions.csv (one row per fund; single companies share the `company` row), and the funds move
together according to CORRELATION below, with stocks and bonds only weakly linked. Return and volatility for
the whole fund follow from the target weights, less the fund's yearly fee. Deposits arrive monthly.

These are ASSUMPTIONS, not forecasts. They are rounded planning figures in the general range of long-run
historical US returns (stocks near 10% a year, bonds near 5%, before today's lower starting yields) and of
published long-term capital-market assumptions; they are not copied from one source and should be replaced
with sourced figures. The UI labels the projection as assumptions.

simulate() keeps its old signature. The band at each year is the sum, over every dollar put in, of that
dollar's own lognormal band (its low/typical/high after the time it has been invested), which treats the
dollars as moving together: a deliberately wide range.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from core.models import Portfolio, SimResult

DATA = Path(__file__).resolve().parent.parent / "data"
Z_90 = 1.2816                 # one-sided 90th percentile of the normal distribution
MONTHS = 12

with (DATA / "assumptions.csv").open() as _f:
    ASSUMPTIONS: dict[str, dict] = {r["ticker"]: {"group": r["group"], "mean": float(r["expected_return_pct"]) / 100,
                                                  "vol": float(r["volatility_pct"]) / 100} for r in csv.DictReader(_f)}

# Correlation between two different funds, by group. A fund is perfectly correlated with itself.
# Stocks and bonds are only weakly linked (they fell together in 2022, but not usually); single companies
# share less with each other than funds do.
CORRELATION: dict[frozenset, float] = {
    frozenset({"us_stocks"}): 0.95, frozenset({"intl_stocks"}): 0.90, frozenset({"sector"}): 0.75,
    frozenset({"company"}): 0.50, frozenset({"bonds"}): 0.90, frozenset({"gold"}): 1.0,
    frozenset({"us_stocks", "intl_stocks"}): 0.85, frozenset({"us_stocks", "sector"}): 0.85,
    frozenset({"us_stocks", "company"}): 0.65, frozenset({"intl_stocks", "sector"}): 0.75,
    frozenset({"intl_stocks", "company"}): 0.55, frozenset({"sector", "company"}): 0.60,
    frozenset({"us_stocks", "bonds"}): 0.10, frozenset({"intl_stocks", "bonds"}): 0.10,
    frozenset({"sector", "bonds"}): 0.05, frozenset({"company", "bonds"}): 0.05,
    frozenset({"gold", "bonds"}): 0.20, frozenset({"gold", "us_stocks"}): 0.05,
    frozenset({"gold", "intl_stocks"}): 0.10, frozenset({"gold", "sector"}): 0.05, frozenset({"gold", "company"}): 0.05,
}


def assumption(ticker: str) -> dict:
    return ASSUMPTIONS.get(ticker) or ASSUMPTIONS["company"]


def _correlation(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return CORRELATION[frozenset({assumption(a)["group"], assumption(b)["group"]})]


def portfolio_assumptions(p: Portfolio) -> dict:
    """Expected return and volatility of the whole fund (yearly, as fractions) from its target weights."""
    w = {h.ticker: h.weight for h in p.holdings}
    mean = sum(x * assumption(t)["mean"] for t, x in w.items()) - p.annual_fee_pct / 100
    var = sum(w[a] * w[b] * _correlation(a, b) * assumption(a)["vol"] * assumption(b)["vol"] for a in w for b in w)
    return {"mean": mean, "vol": math.sqrt(var),
            "rows": [{"ticker": t, "weight": x, "mean": assumption(t)["mean"], "vol": assumption(t)["vol"]}
                     for t, x in w.items()]}


def simulate(p: Portfolio, start: float, monthly: float, years: int = 10) -> SimResult:
    a = portfolio_assumptions(p)
    mean, vol = a["mean"], a["vol"]
    log_vol = vol / (1 + mean)                                          # volatility of the log return
    drift = math.log(1 + mean) - log_vol ** 2 / 2                        # typical (median) log growth a year
    out = {"p10": [], "p50": [], "p90": []}
    for y in range(years + 1):
        lows, mids, highs = [start * math.exp(drift * y - Z_90 * log_vol * math.sqrt(y))], [start * math.exp(drift * y)], \
            [start * math.exp(drift * y + Z_90 * log_vol * math.sqrt(y))]
        for k in range(1, y * MONTHS + 1):                               # each monthly deposit, at the end of month k
            left = y - k / MONTHS
            mids.append(monthly * math.exp(drift * left))
            lows.append(monthly * math.exp(drift * left - Z_90 * log_vol * math.sqrt(left)))
            highs.append(monthly * math.exp(drift * left + Z_90 * log_vol * math.sqrt(left)))
        out["p10"].append(round(sum(lows), 2))
        out["p50"].append(round(sum(mids), 2))
        out["p90"].append(round(sum(highs), 2))
    return SimResult(list(range(years + 1)), out["p10"], out["p50"], out["p90"],
                     monthly, start, round(start + monthly * 12 * years, 2),
                     expected_return_pct=round(mean * 100, 2), volatility_pct=round(vol * 100, 2))
