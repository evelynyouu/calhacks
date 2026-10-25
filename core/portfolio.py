"""Allocation engine.  OWNER: Saahil.  RULES: Lance (ETF list, tilt caps).

Three fund modes:

build_spending_portfolio (DEFAULT) – builds a portfolio of individual stocks directly from the
  customer's Nessie spending history + declared interests. "You shop at Costco -> hold COST."
  BND stays as the bond anchor; VTI/VXUS fills whatever equity isn't covered by stocks.
  Falls back to build_portfolio if no Nessie signals are present.

build_portfolio – a broad core (VTI/VXUS/BND) plus one sector ETF per interest and any
  single companies the customer explicitly picked.

build_custom_portfolio – the same core and bond share, but each interest's slice is filled with
  that ETF's top individual stocks instead of the ETF itself.  ETFs that have no stock breakdown
  (GLD, VB) fall back to the ETF.  Spending-derived and explicit stocks are added on top.

Worst-year numbers are PLACEHOLDERS until real prices are cached.
"""
from __future__ import annotations

from core import market
from core.market import ETFS, HOLDINGS
from core.models import Holding, Portfolio

RISK_LABELS = {1: "Conservative", 2: "Moderate", 3: "Growth", 4: "Aggressive"}
# Core mix by risk level. Stocks are split about 70/30 US/international.
CORE_MIX_BY_RISK = {
    1: {"VTI": 0.25, "VXUS": 0.10, "BND": 0.65},
    2: {"VTI": 0.38, "VXUS": 0.17, "BND": 0.45},
    3: {"VTI": 0.52, "VXUS": 0.23, "BND": 0.25},
    4: {"VTI": 0.63, "VXUS": 0.27, "BND": 0.10},
}
# Share of the fund that goes to interests, by risk level. Sector funds swing far harder than
# the core (ICLN lost over half its value 2021–23), so a conservative fund only gets a taste.
TILT_CAP_BY_RISK = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.25}
MIN_TILT = 0.02           # smaller slices than this aren't worth holding
# Single companies: one company can sink on its own news, so each is small and the total is capped.
STOCK_EACH = 0.05
STOCK_CAP_BY_RISK = {1: 0.05, 2: 0.08, 3: 0.10, 4: 0.15}
THEMES = {r["theme"]: t for t, r in ETFS.items() if r["role"] == "tilt"}   # theme -> ticker
THEME_LABELS = {r["theme"]: r["label"] for r in ETFS.values() if r["role"] == "tilt"}
OVERLAP_WARN = 0.15       # two interest funds sharing this much in the same companies get a note
STOCKS_PER_INTEREST = 3   # top companies to use per interest in custom mode
# PLACEHOLDER worst calendar year per risk level (2022, when stocks and bonds fell together)
_WORST = {1: (2022, -13.9), 2: (2022, -15.6), 3: (2022, -16.9), 4: (2022, -18.1)}


def build_portfolio(risk_level: int, interests: list[str] | None = None, stocks: list[str] | None = None) -> Portfolio:
    risk_level = min(4, max(1, int(risk_level)))
    cap = TILT_CAP_BY_RISK[risk_level]
    tilts = list(dict.fromkeys(THEMES[i] for i in interests or [] if i in THEMES))
    warnings: list[str] = []

    room = int(round(cap / MIN_TILT))
    if len(tilts) > room:
        dropped = [ETFS[t]["label"] for t in tilts[room:]]
        warnings.append(f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} interests, "
                        f"so these were left out: {', '.join(dropped)}.")
        tilts = tilts[:room]
    warnings += _overlap_warnings(tilts)

    picks = [t for t in dict.fromkeys(s.upper() for s in stocks or []) if t not in ETFS]
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    room = int(round(stock_cap / MIN_TILT))
    if len(picks) > room:
        warnings.append(f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} single companies, "
                        f"so these were left out: {', '.join(picks[room:])}.")
        picks = picks[:room]
    each = min(STOCK_EACH, stock_cap / len(picks)) if picks else 0.0

    tilt_total = cap if tilts else 0.0
    core = CORE_MIX_BY_RISK[risk_level]
    equity = core["VTI"] + core["VXUS"]
    keep = (equity - tilt_total - each * len(picks)) / equity
    weights = {"VTI": core["VTI"] * keep, "VXUS": core["VXUS"] * keep, "BND": core["BND"]}
    for t in tilts:
        weights[t] = tilt_total / len(tilts)
    for t in picks:
        weights[t] = each

    roles = {t: "core" if t in core else "tilt" if t in ETFS else "stock" for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[risk_level]
    return Portfolio(risk_level, RISK_LABELS[risk_level], holdings, round(fee, 3), wy, wr, warnings)


def _holding(ticker: str, weight: float, role: str) -> Holding:
    if role == "stock":
        return Holding(ticker, market.name(ticker), weight, role, 0.0)   # owning a company directly has no fund fee
    r = ETFS[ticker]
    return Holding(ticker, r["name"], weight, role, float(r["expense_ratio"]))


def build_custom_portfolio(risk_level: int, interests: list[str] | None = None,
                           stocks: list[str] | None = None,
                           spending_stocks: list[str] | None = None) -> Portfolio:
    """Same core / bond structure as build_portfolio, but replaces sector-ETF tilts with the
    top individual stocks from each ETF (from data/holdings.csv). ETFs with no stock breakdown
    (e.g. GLD) keep the ETF. Spending-derived and explicit stocks are added on top.

    If a stock appears in several interest ETFs (e.g. NVDA in tech + ai_chips) its weight
    accumulates: you're intentionally overweighting it, and a warning is added.
    """
    risk_level = min(4, max(1, int(risk_level)))
    cap = TILT_CAP_BY_RISK[risk_level]
    warnings: list[str] = []

    tilts = list(dict.fromkeys(THEMES[i] for i in (interests or []) if i in THEMES))
    room = int(round(cap / MIN_TILT))
    if len(tilts) > room:
        warnings.append(f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} interests, "
                        f"so these were left out: {', '.join(ETFS[t]['label'] for t in tilts[room:])}.")
        tilts = tilts[:room]

    # Per-group weight (same total tilt as build_portfolio).
    per_group = cap / len(tilts) if tilts else 0.0
    per_stock = per_group / STOCKS_PER_INTEREST

    # Resolve each ETF tilt to individual stocks (or the ETF itself as fallback).
    int_allocs: dict[str, float] = {}    # ticker -> weight
    for etf in tilts:
        top = sorted(HOLDINGS.get(etf, []), key=lambda x: -x[1])[:STOCKS_PER_INTEREST]
        if top:
            for t, _ in top:
                int_allocs[t] = int_allocs.get(t, 0.0) + per_stock
        else:
            int_allocs[etf] = int_allocs.get(etf, 0.0) + per_group   # GLD, VB, …

    # Warn about stocks that appeared in multiple interest ETFs.
    dupes = [t for t, w in int_allocs.items() if w > per_stock + 1e-9 and t not in ETFS]
    if dupes:
        warnings.append(f"{', '.join(dupes)} {'appear' if len(dupes) > 1 else 'appears'} in more than "
                        "one of your interests and will have a larger slice.")

    # Extra stocks: spending-derived + explicitly picked, deduped against interest stocks.
    extra_tickers = list(dict.fromkeys(
        t.upper() for t in list(spending_stocks or []) + list(stocks or [])
        if t.upper() not in ETFS and t.upper() not in int_allocs
    ))
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    extra_room = int(round(stock_cap / MIN_TILT))
    if len(extra_tickers) > extra_room:
        warnings.append(f"Room for {extra_room} extra companies; left out: {', '.join(extra_tickers[extra_room:])}.")
        extra_tickers = extra_tickers[:extra_room]
    each_extra = min(STOCK_EACH, stock_cap / len(extra_tickers)) if extra_tickers else 0.0

    # Core weights, carved down for tilts + extras.
    core = CORE_MIX_BY_RISK[risk_level]
    equity = core["VTI"] + core["VXUS"]
    int_total = sum(int_allocs.values())
    keep = (equity - int_total - each_extra * len(extra_tickers)) / equity
    weights: dict[str, float] = {"VTI": core["VTI"] * keep, "VXUS": core["VXUS"] * keep, "BND": core["BND"]}
    weights.update(int_allocs)
    for t in extra_tickers:
        weights[t] = each_extra

    roles = {t: "core" if t in core else "tilt" if t in ETFS else "stock" for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)
    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[risk_level]
    return Portfolio(risk_level, RISK_LABELS[risk_level] + " · Custom", holdings, round(fee, 3), wy, wr, warnings)


def custom_fund_preview(risk_level: int, interests: list[str] | None = None,
                        spending_stocks: list[str] | None = None) -> list[dict]:
    """What stocks a custom fund would hold for these interests, before committing.
    Returns [{ticker, name, weight_pct, from_interest, etf_label}] sorted by weight."""
    risk_level = min(4, max(1, int(risk_level)))
    cap = TILT_CAP_BY_RISK[risk_level]
    tilts = list(dict.fromkeys(THEMES[i] for i in (interests or []) if i in THEMES))
    per_group = cap / len(tilts) if tilts else 0.0
    per_stock = per_group / STOCKS_PER_INTEREST
    rows = []
    for etf in tilts:
        top = sorted(HOLDINGS.get(etf, []), key=lambda x: -x[1])[:STOCKS_PER_INTEREST]
        label = ETFS[etf]["label"]
        if top:
            for t, _ in top:
                rows.append({"ticker": t, "name": market.name(t),
                             "weight_pct": round(per_stock * 100, 1),
                             "from_interest": label, "replaces_etf": etf})
        else:
            rows.append({"ticker": etf, "name": ETFS[etf]["name"],
                        "weight_pct": round(per_group * 100, 1),
                        "from_interest": label, "replaces_etf": None})
    for t in (spending_stocks or []):
        rows.append({"ticker": t.upper(), "name": market.name(t.upper()),
                    "weight_pct": round(min(STOCK_EACH, STOCK_CAP_BY_RISK[risk_level]) * 100, 1),
                    "from_interest": "spending", "replaces_etf": None})
    return sorted(rows, key=lambda r: -r["weight_pct"])


def build_spending_portfolio(
    customer_id: str,
    risk_level: int,
    interests: list[str] | None = None,
    stocks: list[str] | None = None,
) -> Portfolio:
    """Build a portfolio of individual stocks from Nessie spending + interests + BND anchor.

    Algorithm:
      1. BND: same allocation as build_portfolio for the risk level (bond safety net).
      2. Spending stocks: pull Nessie activity -> map merchants to tickers -> weight by
         dollars spent. A company you spend $500/mo at gets more weight than one you spend
         $50/mo at. Total basket capped by risk level; per-stock capped too.
      3. Interest stocks: top companies from each declared-interest ETF, equal weight per
         interest (same tilt cap as build_portfolio).
      4. Explicit stocks: tickers the user added manually (same STOCK_EACH caps).
      5. VTI/VXUS residual: fills whatever equity isn't covered by steps 2-4, at the
         standard 70/30 US/intl split.

    Falls back to build_portfolio() if there's no spending data and no interests.
    """
    from core import spending as _spending

    risk_level = min(4, max(1, int(risk_level)))
    warnings: list[str] = []
    reasons: dict[str, str] = {}

    # --- 1. Bond anchor (identical to build_portfolio) ---
    bond_pct = CORE_MIX_BY_RISK[risk_level]["BND"]
    equity_pct = 1.0 - bond_pct

    # --- 2. Spending stocks ---
    # Caps: how much of the TOTAL portfolio can go to spending-driven stocks.
    SPEND_BASKET_CAP = {1: 0.12, 2: 0.20, 3: 0.30, 4: 0.40}[risk_level]
    SPEND_PER_STOCK  = {1: 0.02, 2: 0.03, 3: 0.05, 4: 0.07}[risk_level]

    try:
        raw_details = _spending.spending_stock_details(customer_id)
    except Exception:
        raw_details = {}

    # Keep only tickers the market can price; filter out ETFs (we want individual stocks)
    raw_details = {t: v for t, v in raw_details.items() if market.known(t) and t not in ETFS}

    spend_weights: dict[str, float] = {}
    if raw_details:
        total_raw = sum(v["amount"] for v in raw_details.values())
        from core.market import COMPANIES
        for ticker, info in sorted(raw_details.items(), key=lambda x: -x[1]["amount"]):
            dollars = info["amount"]
            prop_weight = (dollars / total_raw) * SPEND_BASKET_CAP
            capped = min(prop_weight, SPEND_PER_STOCK)
            spend_weights[ticker] = capped
            company_name = COMPANIES.get(ticker, {}).get("name", ticker)
            if info["source"] == "bill":
                reasons[ticker] = (f"You pay {info['label']} (${dollars:,.0f}/period) — "
                                   f"owning {company_name} puts you on the other side")
            else:
                reasons[ticker] = f"You shop at {info['label']} (${dollars:,.0f} in history)"

        actual_spend_total = sum(spend_weights.values())
        if not spend_weights:
            warnings.append("No spending activity could be matched to investable companies.")
    else:
        actual_spend_total = 0.0

    # --- 3. Interest stocks (same structure as build_custom_portfolio) ---
    INTEREST_CAP = TILT_CAP_BY_RISK[risk_level]  # fraction of total portfolio
    tilts = list(dict.fromkeys(THEMES[i] for i in (interests or []) if i in THEMES))
    room = int(round(INTEREST_CAP / MIN_TILT))
    if len(tilts) > room:
        warnings.append(
            f"A {RISK_LABELS[risk_level].lower()} fund has room for {room} interests, "
            f"so these were left out: {', '.join(ETFS[t]['label'] for t in tilts[room:])}."
        )
        tilts = tilts[:room]

    per_group = INTEREST_CAP / len(tilts) if tilts else 0.0
    per_stock = per_group / STOCKS_PER_INTEREST

    int_weights: dict[str, float] = {}
    for etf in tilts:
        top = sorted(HOLDINGS.get(etf, []), key=lambda x: -x[1])[:STOCKS_PER_INTEREST]
        label = ETFS[etf]["label"]
        if top:
            for t, _ in top:
                # Skip if already in spending basket (avoid double-counting)
                if t not in spend_weights:
                    int_weights[t] = int_weights.get(t, 0.0) + per_stock
                    if t not in reasons:
                        reasons[t] = f"From your {label} interest"
                else:
                    reasons[t] += f" + {label} interest"
        else:
            # ETF has no stock breakdown (GLD, VB) -> hold the ETF itself
            if etf not in spend_weights:
                int_weights[etf] = int_weights.get(etf, 0.0) + per_group

    # Warn about stocks appearing in multiple interests
    dupes = [t for t, w in int_weights.items() if w > per_stock + 1e-9 and t not in ETFS]
    if dupes:
        warnings.append(
            f"{', '.join(dupes)} {'appear' if len(dupes) > 1 else 'appears'} in more than "
            "one of your interests and will have a larger slice."
        )

    # --- 4. Explicit stocks (user-added single companies) ---
    stock_cap = STOCK_CAP_BY_RISK[risk_level]
    extra = [t.upper() for t in (stocks or [])
             if t.upper() not in ETFS and t.upper() not in spend_weights and t.upper() not in int_weights]
    extra_room = int(round(stock_cap / MIN_TILT))
    if len(extra) > extra_room:
        warnings.append(
            f"Room for {extra_room} manually-added companies; left out: {', '.join(extra[extra_room:])}."
        )
        extra = extra[:extra_room]
    each_extra = min(STOCK_EACH, stock_cap / len(extra)) if extra else 0.0
    for t in extra:
        reasons[t] = "You added this company directly"

    # --- 5. Combine all stocks, compute VTI/VXUS residual ---
    all_stocks_total = (sum(spend_weights.values()) +
                        sum(int_weights.values()) +
                        each_extra * len(extra))

    # VTI/VXUS gets whatever equity is left, split 70/30
    residual = max(0.0, equity_pct - all_stocks_total)
    vti_w  = round(residual * 0.70, 4)
    vxus_w = round(residual * 0.30, 4)

    # If there are NO signals at all, fall back to the ETF-based fund
    if not spend_weights and not int_weights and not extra:
        return build_portfolio(risk_level, interests, stocks)

    # --- Build final weights dict ---
    weights: dict[str, float] = {}
    if vti_w  > MIN_TILT: weights["VTI"]  = vti_w
    if vxus_w > MIN_TILT: weights["VXUS"] = vxus_w
    weights["BND"] = bond_pct
    weights.update(spend_weights)
    weights.update(int_weights)
    for t in extra:
        weights[t] = each_extra

    if vti_w  > MIN_TILT: reasons["VTI"]  = "Broad US stocks — diversifies beyond your specific picks"
    if vxus_w > MIN_TILT: reasons["VXUS"] = "International stocks — global diversification"
    reasons["BND"] = "Bonds — cushion when markets drop"

    # Normalise so weights sum exactly to 1.0 (rounding errors)
    roles = {t: ("core" if t in {"VTI", "VXUS", "BND"} else
                 "tilt" if t in ETFS else "stock") for t in weights}
    holdings = [_holding(t, round(w, 4), roles[t]) for t, w in weights.items()]
    holdings[0].weight = round(holdings[0].weight + 1 - sum(h.weight for h in holdings), 4)

    fee = sum(h.weight * h.expense_ratio for h in holdings)
    wy, wr = _WORST[risk_level]
    label = RISK_LABELS[risk_level] + " · Spending-Driven"
    return Portfolio(risk_level, label, holdings, round(fee, 3), wy, wr, warnings, reasons)


def _overlap_warnings(tilts: list[str]) -> list[str]:
    out = []
    for i, a in enumerate(tilts):
        for b in tilts[i + 1:]:
            wa, wb = dict(HOLDINGS.get(a, [])), dict(HOLDINGS.get(b, []))
            shared = sorted(set(wa) & set(wb), key=lambda c: -(wa[c] + wb[c]))
            if shared and sum(min(wa[c], wb[c]) for c in shared) >= OVERLAP_WARN:
                out.append(f"{a} and {b} both hold {', '.join(shared)}, so those companies count twice.")
    return out
