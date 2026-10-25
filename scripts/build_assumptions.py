"""Rebuild data/assumptions.csv and data/correlations.csv.  OWNER: Lance.

Run with network access:  python -m scripts.build_assumptions

Where each number comes from:
  * US stocks, international stocks, bonds, gold, REITs: J.P. Morgan Asset Management, 2026 Long-Term
    Capital Market Assumptions (Oct 2025), "2026 estimates and correlations | U.S. dollar assumptions"
    (https://am.jpmorgan.com/us/en/asset-management/institutional/insights/portfolio-insights/ltcma/).
    Return = arithmetic return 2026 (the simulator turns it into a median path); volatility = annualized.
  * Sector funds and single companies are not forecast by J.P. Morgan. Their expected return is the US large
    cap return (no sector or stock-picking premium is assumed). Their volatility and their correlations with
    everything else are measured from about two years of daily closes (core/feed.py), a short and mostly
    rising window, so they are rough.
"""
from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path

from core import feed

DATA = Path(__file__).resolve().parent.parent / "data"
JPM = "J.P. Morgan 2026 LTCMA"
PRICES = "measured from daily closes"

# (arithmetic return %, volatility %, J.P. Morgan asset class)
JPM_CLASSES = {
    "large": (7.94, 16.47, "U.S. Large Cap"), "small": (8.89, 21.10, "U.S. Small Cap"),
    "eafe": (8.90, 17.63, "EAFE Equity"), "em": (9.74, 20.93, "Emerging Markets Equity"),
    "agg": (4.91, 4.76, "U.S. Aggregate Bonds"), "reit": (10.15, 17.40, "U.S. REITs"), "gold": (6.78, 16.68, "Gold"),
}
# fund -> (group, J.P. Morgan class)
JPM_FUNDS = {
    "VTI": ("us_stocks", "large"), "SPY": ("us_stocks", "large"), "DIA": ("us_stocks", "large"),
    "SCHD": ("us_stocks", "large"), "VB": ("us_stocks", "small"),
    "VXUS": ("intl_stocks", "eafe"), "VWO": ("intl_stocks", "em"),
    "BND": ("bonds", "agg"), "GLD": ("gold", "gold"), "VNQ": ("sector", "reit"),
}
# Correlations between groups, from the J.P. Morgan matrix (class pair in the note)
JPM_CORR = [
    ("us_stocks", "us_stocks", 0.90, "Large Cap vs Small Cap"), ("intl_stocks", "intl_stocks", 0.86, "EAFE vs EM"),
    ("bonds", "bonds", 1.00, "single fund"), ("gold", "gold", 1.00, "single fund"),
    ("us_stocks", "intl_stocks", 0.87, "Large Cap vs EAFE"), ("us_stocks", "bonds", 0.30, "Large Cap vs Aggregate"),
    ("intl_stocks", "bonds", 0.34, "EAFE vs Aggregate"), ("gold", "bonds", 0.38, "Gold vs Aggregate"),
    ("gold", "us_stocks", 0.03, "Gold vs Large Cap"), ("gold", "intl_stocks", 0.14, "Gold vs EAFE"),
]


def returns(symbol: str) -> dict:
    rows = feed.history(symbol) or []
    return {d: math.log(b / a) for (_, a), (d, b) in zip(rows, rows[1:])}


def corr(x: dict, y: dict) -> float | None:
    days = sorted(set(x) & set(y))
    if len(days) < 120:
        return None
    return statistics.correlation([x[d] for d in days], [y[d] for d in days])


def avg_corr(a: list[dict], b: list[dict]) -> float:
    vals = [c for i, x in enumerate(a) for j, y in enumerate(b) if (a is not b or i < j) and (c := corr(x, y)) is not None]
    return statistics.mean(vals)


def vol(r: dict) -> float:
    return statistics.stdev(r.values()) * math.sqrt(252) * 100


def main() -> None:
    etfs = list(csv.DictReader((DATA / "etfs.csv").open()))
    companies = [r["ticker"] for r in csv.DictReader((DATA / "companies.csv").open())]
    large = JPM_CLASSES["large"][0]
    series = {t: returns(t) for t in {r["ticker"] for r in etfs} | set(companies)}
    missing = [r["ticker"] for r in etfs if not series[r["ticker"]]]
    if missing:
        raise SystemExit(f"no price history for {missing}; run with network access")
    companies = [t for t in companies if series[t]]          # a few (e.g. OTC ADRs) have no history; skip them

    out = []
    for r in etfs:
        t = r["ticker"]
        if t in JPM_FUNDS:
            group, cls = JPM_FUNDS[t]
            mean, v, label = JPM_CLASSES[cls]
            out.append((t, group, mean, v, f"{JPM}: {label}"))
        else:
            out.append((t, "sector", large, round(vol(series[t]), 1), f"return: {JPM} U.S. Large Cap; volatility {PRICES}"))
    company_vol = round(statistics.median(vol(series[t]) for t in companies), 1)
    out.append(("company", "company", large, company_vol, f"return: {JPM} U.S. Large Cap; volatility {PRICES} (median of {len(companies)} companies)"))

    groups = {"sector": [series[r["ticker"]] for r in etfs if r["ticker"] not in JPM_FUNDS],
              "company": [series[t] for t in companies],
              "us_stocks": [series[t] for t, (g, _) in JPM_FUNDS.items() if g == "us_stocks"],
              "intl_stocks": [series[t] for t, (g, _) in JPM_FUNDS.items() if g == "intl_stocks"],
              "bonds": [series["BND"]], "gold": [series["GLD"]]}
    pairs = [(a, b, round(c, 2), f"{PRICES}") for a, b in [("sector", "sector"), ("company", "company")]
             if (c := avg_corr(groups[a], groups[b]))]
    for other in ("us_stocks", "intl_stocks", "bonds", "gold"):
        pairs.append(("sector", other, round(avg_corr(groups["sector"], groups[other]), 2), PRICES))
        pairs.append(("company", other, round(avg_corr(groups["company"], groups[other]), 2), PRICES))
    pairs.append(("sector", "company", round(avg_corr(groups["sector"], groups["company"]), 2), PRICES))
    pairs = [(a, b, c, f"{n}, {len(series['VTI'])} days") for a, b, c, n in pairs]

    with (DATA / "assumptions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "group", "expected_return_pct", "volatility_pct", "source"])
        w.writerows(out)
    with (DATA / "correlations.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_a", "group_b", "correlation", "source"])
        w.writerows(JPM_CORR + pairs)
    print(f"wrote {len(out)} assumptions and {len(JPM_CORR) + len(pairs)} correlations")


if __name__ == "__main__":
    main()
