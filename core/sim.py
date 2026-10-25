"""10-year outcome range.  OWNER: Saahil.  Assumptions and the portfolio model: Ethan.

The range comes from the fund's own holdings: each fund has an expected return and volatility in
data/assumptions.csv (one row per fund; single companies share the `company` row), and the funds move
together according to CORRELATION below, with stocks and bonds only weakly linked. Return and volatility for
the whole fund follow from the target weights, less the fund's yearly fee. Deposits arrive monthly.

These are ASSUMPTIONS, not forecasts. Returns and volatilities for the broad funds (US and international stocks,
bonds, gold, REITs) and the correlations between them are from J.P. Morgan Asset Management's 2026 Long-Term
Capital Market Assumptions (Oct 2025). Sector funds and single companies are not forecast there, so they get
the US large cap return and a volatility and correlations measured from about two years of daily prices. Each
row of data/assumptions.csv and data/correlations.csv names its source, and scripts/build_assumptions.py
rebuilds both files. The UI labels the projection as assumptions.

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

# Correlation between two different funds, by group (data/correlations.csv). A fund is perfectly correlated
# with itself.
with (DATA / "correlations.csv").open() as _f:
    CORRELATION: dict[frozenset, float] = {frozenset({r["group_a"], r["group_b"]}): float(r["correlation"])
                                           for r in csv.DictReader(_f)}


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
