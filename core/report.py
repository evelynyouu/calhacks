"""The fund report.  OWNER: Lance.

Every number is computed here, in Python. Claude only writes the short narrative on top, from
report["facts"]. Sections: summary, holdings, companies (looking through each fund to the top
companies it holds), the customer's interests, the broad market and what it means for this fund,
recommendations (some with trades the customer can confirm), and the review schedule.
"""
from __future__ import annotations

import csv
import math
from datetime import date, timedelta

from core import intake, market, returns
from core.broker import get_broker, values
from core.finances import build_finances
from core.fund import FundAccount
from core.market import ETFS, HOLDINGS
from core.models import SLICES
from core.policy import tier_for
from core.portfolio import THEME_LABELS, THEMES, plain_index
from core.profile import PAY_PERIOD_DAYS, get_profile
from core.rebalance import band, drift as drift_of, limited_rebalance, off_target
from core.rebalance import targets as rebalance_targets
from core.sim import portfolio_assumptions, simulate

QUARTER_DAYS = 91
US_SHARE_OF_STOCKS = 0.70       # VTI's share of VTI+VXUS when an older fund has neither to read it from
PAYCHECKS_PER_YEAR = round(365 / PAY_PERIOD_DAYS)    # paid every 14 days: 26 a year, not 24
# Approximate yearly income (distributions) per fund, from data/yields.csv. The price feed has no adjusted
# closes, so income is estimated, accrued daily, and kept apart from the balance. Single companies count as 0.
with (market.DATA / "yields.csv").open() as _f:
    YIELDS = {r["ticker"]: float(r["yield_pct"]) / 100 for r in csv.DictReader(_f)}
# Rough yearly dividend yield of a company by sector, for companies held directly (data/yields.csv covers the funds).
# Rounded typical figures, not live and not per company: income from companies is an estimate and says so.
SECTOR_YIELD = {"Technology": 0.006, "Communication": 0.010, "Consumer": 0.010, "Everyday essentials": 0.025,
                "Healthcare": 0.016, "Financials": 0.018, "Industrials": 0.014, "Energy": 0.034, "Utilities": 0.030,
                "Real estate": 0.038, "Materials": 0.018}
BENCHMARK = "SPY"               # "the market" line
NOT_STOCKS = {"BND", "GLD"}

DIP_TRIGGER = -7.0              # your stocks down 7% in a month ...
BELOW_HIGH_TRIGGER = -10.0      # ... or 10% below their 1-year high -> consider investing more
DIP_PCT_STEP = 5                # suggested raise in paycheck percent during a dip
LAG_TRIGGER = -10.0             # an interest 10 points behind the market over 3 months -> hold, don't sell
CONCENTRATION_TRIGGER = 0.08    # one company 8% of the fund -> diversify
TECH_HEAVY_SHARE = 0.6          # 60% of the interest slice in tech-like themes -> look at other sectors
TECH_FUND_TRIGGER = 0.35        # tech-like companies 35% of the whole fund (look-through) -> look at other sectors
HIGH_PCT = 50                   # investing half of each paycheck or more -> check cash
LOWER_PCT_STEP = 10
TECH_THEMES = {"tech", "ai_chips", "software", "cyber", "robotics", "communications", "gaming"}
TECH_SECTORS = {"Technology", "Communication"}
STEADY_THEMES = ["healthcare", "staples", "utilities", "dividends"]
# Which sector an interest fund adds to, for looking through the whole fund. Funds missing here are left out.
THEME_SECTOR = {
    "tech": "Technology", "ai_chips": "Technology", "software": "Technology", "cyber": "Technology",
    "robotics": "Technology", "communications": "Communication", "gaming": "Communication",
    "healthcare": "Healthcare", "biotech": "Healthcare", "financials": "Financials", "real_estate": "Real estate",
    "consumer": "Consumer", "staples": "Everyday essentials", "industrials": "Industrials",
    "space_defense": "Industrials", "utilities": "Utilities", "oil_gas": "Energy", "clean_energy": "Energy",
}
with (market.DATA / "sectors.csv").open() as _f:     # approximate sector mix of the broad funds, rounded
    CORE_SECTORS: dict[str, dict[str, float]] = {}
    for _r in csv.DictReader(_f):
        CORE_SECTORS.setdefault(_r["fund"], {})[_r["sector"]] = float(_r["weight_pct"]) / 100
CALM, CHOPPY = 0.8, 1.25        # volatility vs typical


def build_report(acct: FundAccount) -> dict:
    cid, today, p = acct.customer_id, acct.today, acct.portfolio
    quarter_ago = today - timedelta(days=QUARTER_DAYS)
    vals = values(cid, today)
    total = sum(vals.values())
    tier = tier_for(total)

    rets = _returns(acct, tier)
    period = rets["period"]
    month_ago = period["start"] if period else today - timedelta(days=tier.review_every_days)
    holdings = _holdings(acct, vals, total, month_ago)
    mkt = _market(today, month_ago, quarter_ago)
    since = rets["since_start"]
    contributed = since["deposits"] if since else acct.contributed
    gain = total - contributed
    period_pct = period["twr_pct"] if period and period["twr_pct"] is not None else 0.0
    summary = {
        "value": round(total, 2), "contributed": round(contributed, 2), "gain": round(gain, 2),
        "gain_pct": since["mwr_price_pct"] if since and since["mwr_price_pct"] is not None
        else round(gain / contributed * 100, 2) if contributed else 0.0,
        "period_pct": period_pct, "market_period_pct": mkt["m1"],
        "vs_market_pts": round(period_pct - mkt["m1"], 2),
        "income_estimate": since["income_estimate"] if since else 0.0,
        "benchmark_period_pct": period["benchmark_pct"] if period else None,
        "vs_benchmark_pts": period["vs_benchmark_pts"] if period else None,
        "invest_pct": acct.invest_pct, "fee_pct": p.annual_fee_pct,
        "fee_dollars": round(total * p.annual_fee_pct / 100, 2),
    }
    all_companies = _companies(vals, total, month_ago, today, top=None)
    companies = all_companies[:10]
    interests = _interests(acct, vals, total, month_ago, quarter_ago, today, mkt)
    relation = _relation(vals, total, period_pct, mkt)
    recs = _recommendations(acct, vals, total, holdings, all_companies, interests, mkt, tier)

    paycheck = get_profile(cid).paycheck_amount
    per_paycheck = math.floor(paycheck * acct.invest_pct / 100)        # Nessie moves whole dollars, as fund.paycheck does
    monthly = round(per_paycheck * PAYCHECKS_PER_YEAR / 12, 2)
    sim = simulate(p, total, monthly)
    assumptions = portfolio_assumptions(p)
    next_review = (acct.last_review or today) + timedelta(days=tier.review_every_days)
    rep = {
        "as_of": today, "period_days": period["days"] if period else tier.review_every_days, "summary": summary,
        "returns": rets, "holdings": holdings, "by_slice": _slice_tables(holdings),
        "companies": companies, "interests": interests, "market": mkt, "relation": relation,
        "recommendations": recs, "projection": sim, "history": value_history(acct),
        "projection_inputs": {"per_paycheck": per_paycheck, "paychecks_per_year": PAYCHECKS_PER_YEAR, "monthly": monthly,
                              "return_pct": sim.expected_return_pct, "vol_pct": sim.volatility_pct,
                              "fee_pct": p.annual_fee_pct, "rows": assumptions["rows"]},
        "schedule": {"tier": tier, "review_every_days": tier.review_every_days, "auto_allowed": tier.auto_trades,
                     "auto_on": acct.auto_trade, "next_review": next_review},
        "broker_mode": get_broker().mode,
    }
    by_ticker = {i["ticker"]: i for i in interests}
    for w in (period, since):                     # 3-month view next to each pick, for the hold-through-a-slump rule
        for x in (w["picks"] if w else []):
            i = by_ticker.get(x["ticker"])
            x["m3"], x["vs_market_m3"] = (i["m3"], i["vs_market_m3"]) if i else (None, None)
    rep["sectors"] = [{"sector": k, "share": round(v, 4)} for k, v in
                      sorted(_sector_exposure(vals, total, _own_sectors(p)).items(), key=lambda kv: -kv[1])[:6]]
    rep["data_notes"] = _data_notes(acct, holdings)
    targets = {h.ticker: h.weight for h in p.holdings}
    rep["finances"] = build_finances(
        get_profile(cid), acct.answers, total, per_paycheck, PAYCHECKS_PER_YEAR, sim.expected_return_pct,
        relation["beta"], targets.get("BND", 0.0), _personal_share(p),
        summary["fee_dollars"], sim.p50[-1])
    rep["facts"] = _facts(rep)
    rep["narrative"] = _narrative(rep)
    return rep


def _data_notes(acct: FundAccount, holdings: list[dict]) -> dict:
    """Where the numbers come from, so the page can say it plainly: real or simulated prices, and what is approximate."""
    today, tickers = acct.today, [h["ticker"] for h in holdings]
    last_real = market.last_real_date(BENCHMARK)
    always = [t for t in tickers if market.last_real_date(t) is None]
    if last_real is None:
        price = "All prices here are simulated: real market data isn't available right now."
    elif today > last_real:
        price = (f"Prices after {last_real.strftime('%b')} {last_real.day} are simulated: the demo has moved past the "
                 f"last real closing prices, so anything dated later is a simulation, not the market.")
    else:
        price = "Prices are real Nasdaq closing prices."
    if last_real is not None and always:
        price += f" {', '.join(always)} {'has' if len(always) == 1 else 'have'} no real price data, so always simulated."
    return {"price": price, "last_real_date": last_real, "simulated_tickers": [h["ticker"] for h in holdings if h["simulated"]],
            "income": "Income (fund distributions) is estimated from approximate yearly yields and isn't in your balance; "
                      "returns marked \"price only\" leave it out.",
            "look_through": "Company and sector shares inside your funds are approximate: they use each fund's biggest holdings."}


def _returns(acct: FundAccount, tier) -> dict:
    """What the fund did, replayed from the broker's orders: over the review period and since the start.
    The period runs from the last review (at least one review cadence back); a younger fund gets its whole
    life, labeled as such. With no order history (nothing bought yet, or a broker that can't list orders)
    both windows are None."""
    today, orders = acct.today, get_broker().orders(acct.customer_id)
    if not orders:
        return {"available": False, "first_order": None, "period": None, "since_start": None, "benchmark": None}
    first = min(o["on"] for o in orders)
    start = today - timedelta(days=tier.review_every_days)
    if acct.last_review and acct.last_review < start:
        start = acct.last_review
    young = start <= first   # the fund is younger than the period: show its whole life
    bench = benchmark_weights(acct.portfolio)
    yields = _yields(acct.portfolio)
    return {"available": True, "first_order": first, "benchmark": bench,
            "period": _window(orders, first, today, None if young else start, bench, acct.portfolio, yields),
            "since_start": _window(orders, first, today, None, bench, acct.portfolio, yields)}


def _yields(p) -> dict[str, float]:
    """Yearly income rate per ticker: the fund table where it has one, else the sector's rough rate for a company."""
    return {**{h.ticker: SECTOR_YIELD[h.sector] for h in p.holdings if h.sector in SECTOR_YIELD}, **YIELDS}


def benchmark_weights(p) -> dict[str, float]:
    """For a custom index (the builder recorded p.parts): the plain index, meaning the same US stock share spread
    over the US index by size with nothing personal, plus VXUS and BND at the fund's own shares. "vs benchmark"
    is then only the customer's choices (where they spend, interests, companies added, drift)."""
    if getattr(p, "parts", None):
        bonds = sum(p.parts.get("bonds", {}).values())
        intl = sum(p.parts.get("intl", {}).values())
        plain = {t: round(w, 6) for t, w in plain_index(1 - bonds - intl).items()}
        return {**plain, "VXUS": round(intl, 6), "BND": round(bonds, 6)}
    return _three_fund_benchmark(p)


def _three_fund_benchmark(p) -> dict[str, float]:
    """An older fund's core mix: VTI/VXUS/BND at the fund's stock/bond split. Every US holding (the US index of
    single companies, interests, where you spend, added companies, or a VTI core) counts as US stocks, since
    the personal slices come out of the US part. Read from the target weights, so it holds for any bond share."""
    w = {h.ticker: h.weight for h in p.holdings}
    bonds = w.get("BND", 0.0)
    intl = w.get("VXUS", 0.0)
    us = sum(v for t, v in w.items() if t not in ("BND", "VXUS"))
    us_frac = us / (us + intl) if us + intl > 0 else US_SHARE_OF_STOCKS
    return {"VTI": round((1 - bonds) * us_frac, 6), "VXUS": round((1 - bonds) * (1 - us_frac), 6), "BND": round(bonds, 6)}


def _window(orders: list[dict], first: date, end: date, start: date | None, bench: dict[str, float], p,
            yields: dict[str, float] | None = None) -> dict:
    """Dollars, returns, benchmark and per-holding gains for [start, end]. start=None means from the first
    deposit, which then counts as new money. The benchmark and the S&P 500 line get the same deposits on the
    same days, so "vs benchmark" is only the customer's choices (interests, companies, drift)."""
    px, from_start = market.raw_price, start is None
    begin = returns.day_before(first) if from_start else start
    yields = YIELDS if yields is None else yields
    r = returns.replay(orders, px, begin, end, yields)
    flows = returns.net_flows(orders, px)
    blend = returns.replay(returns.shadow_orders(flows, bench, px), px, begin, end, yields)
    spy = returns.replay(returns.shadow_orders(flows, {BENCHMARK: 1.0}, px), px, begin, end, yields)
    inc = True   # headline returns include estimated income; the *_price_pct twins leave it out
    twr, mwr, b_twr, s_twr = (returns.twr(r, inc), returns.money_weighted(r, inc), returns.twr(blend, inc),
                              returns.twr(spy, inc))
    pct = lambda x: None if x is None else round(x * 100, 2)   # noqa: E731
    gap = lambda a, b: None if a is None or b is None else round((a - b) * 100, 2)   # noqa: E731
    return {
        "start": first if from_start else start, "end": end, "days": (end - (first if from_start else start)).days,
        "from_start": from_start, "start_value": round(r.start_value, 2), "deposits": round(r.deposits, 2),
        "market_gain": round(r.market_gain, 2), "end_value": round(r.end_value, 2),
        "income_estimate": round(r.income, 2),
        "twr_pct": pct(twr), "mwr_pct": pct(mwr),
        "twr_price_pct": pct(returns.twr(r)), "mwr_price_pct": pct(returns.money_weighted(r)),
        "benchmark_price_pct": pct(returns.twr(blend)), "spy_price_pct": pct(returns.twr(spy)),
        "uses_simulated_prices": any(market.simulated_on(t, end) for t in {*bench, BENCHMARK, *(o["symbol"] for o in orders)}),
        "benchmark_pct": pct(b_twr), "benchmark_value": round(blend.end_value, 2), "vs_benchmark_pts": gap(twr, b_twr),
        "spy_pct": pct(s_twr), "vs_spy_pts": gap(twr, s_twr),
        "attribution": _attribution(r, p), "picks": _picks(r, p, bench, px, first if from_start else start),
    }


def _attribution(r: returns.Replay, p) -> list[dict]:
    """What moved the fund: dollars each holding added or cost, biggest first. Adds up to the market gain."""
    roles = {h.ticker: h.role for h in p.holdings}
    raw = returns.attribution(r)
    rows = [{"ticker": t, "label": market.info(t)["label"], "role": roles.get(t, "tilt"), "dollars": round(d, 2)}
            for t, d in raw.items()]
    if rows:   # cents lost rounding ~60 rows go on the biggest, so the rows still add up to the market gain
        top = max(rows, key=lambda x: abs(x["dollars"]))
        top["dollars"] = round(top["dollars"] + round(sum(raw.values()), 2) - round(sum(x["dollars"] for x in rows), 2), 2)
    return sorted(rows, key=lambda x: -abs(x["dollars"]))


def _picks(r: returns.Replay, p, bench: dict[str, float], px, since: date) -> list[dict]:
    """Each interest fund and single company: its price move, the dollars it added, and how it did against
    the core stock funds it replaced (VTI/VXUS at the fund's own US/international split)."""
    stock_part = {t: w for t, w in bench.items() if t != "BND"}   # the stock funds or the plain index
    stocks = sum(stock_part.values())
    core = {t: w / stocks for t, w in stock_part.items()} if stocks else {"VTI": 1.0}
    gains = returns.attribution(r)
    core_pct = round(sum(w * market.change_pct(t, since, r.end) for t, w in core.items()), 2)
    rows = []
    for h in p.holdings:
        if h.role == "core":
            continue
        move = market.change_pct(h.ticker, since, r.end)
        rows.append({"ticker": h.ticker, "label": h.name if h.sector else market.info(h.ticker)["label"], "role": h.role,
                     "slice": dict(SLICES)[p.slice_of(h)], "weight": h.weight, "move_pct": move, "dollars": round(gains.get(h.ticker, 0.0), 2),
                     "core_pct": core_pct, "vs_core_pts": round(move - core_pct, 2),
                     "vs_core_dollars": round(returns.excess_over(r, h.ticker, core, px), 2)})
    return rows


def _holdings(acct, vals, total, month_ago) -> list[dict]:
    p, today = acct.portfolio, acct.today
    targets = rebalance_targets(p, vals)   # inside a custom index, targets follow prices (core/rebalance.py)
    shares, cost = get_broker().positions(acct.customer_id), get_broker().cost_basis(acct.customer_id)
    drifts = drift_of(p, vals) if total else {t: -w for t, w in targets.items()}
    flagged = off_target(p, vals) if total else {}
    where, sector_of = {h.ticker: p.slice_of(h) for h in p.holdings}, _own_sectors(p)
    names = {h.ticker: h.name for h in p.holdings}
    tickers = list(targets) + [t for t in vals if t not in targets]
    day = market.quotes(tickers, today)
    rows = []
    for t in tickers:
        v = vals.get(t, 0.0)
        row = market.info(t)
        rows.append({
            "ticker": t, "name": row["name"], "about": row["about"],
            "label": names[t] if t in sector_of else row["label"],      # a company is shown by its name, a fund by its label
            "slice": where.get(t, "former"), "sector": sector_of.get(t, ""),
            "role": row["role"] if row["role"] in ("core", "stock") else "tilt", "shares": round(shares.get(t, 0.0), 4),
            "day_pct": day[t]["change_pct"] if t in day else None,
            "price": market.price(t, today), "value": round(v, 2),
            "weight": round(v / total, 4) if total else 0.0, "target": targets.get(t, 0.0),
            "drift": drifts.get(t, 0.0), "off_target": t in flagged, "band": round(band(targets.get(t, 0.0)), 4),
            "period_pct": market.change_pct(t, month_ago, today),
            "simulated": market.simulated_on(t, today),
            "gain_pct": round((v / cost[t] - 1) * 100, 2) if cost.get(t) else None,
        })
    return rows


def _slice_tables(rows: list[dict]) -> list[dict]:
    """The holdings table grouped by slice, each with subtotals (the US index also by sector). The period move of a
    group is its holdings' price changes weighted by what's held. A position no longer in the fund is its own group."""
    def subtotal(rs: list[dict]) -> dict:
        value, target = sum(r["value"] for r in rs), sum(r["target"] for r in rs)
        basis = [(r["value"] if value else r["target"], r["period_pct"]) for r in rs]
        denominator = sum(w for w, _ in basis)
        return {"count": len(rs), "value": round(value, 2), "weight": round(sum(r["weight"] for r in rs), 4),
                "target": round(target, 4), "period_pct": round(sum(w * x for w, x in basis) / denominator, 2) if denominator else 0.0}

    out = []
    for key, label in (*SLICES, ("former", "No longer in your fund")):
        rs = sorted((r for r in rows if r["slice"] == key), key=lambda r: -r["value"])
        if not rs:
            continue
        sectors = None
        if key == "us_core" and any(r["sector"] for r in rs):
            by: dict[str, list[dict]] = {}
            for r in rs:
                by.setdefault(r["sector"] or "Other", []).append(r)
            sectors = sorted(({"sector": sec, **subtotal(group), "rows": group} for sec, group in by.items()), key=lambda g: -g["weight"])
        out.append({"key": key, "label": label, **subtotal(rs), "rows": rs, "sectors": sectors})
    return out


def _companies(vals, total, month_ago, today, top: int | None = 10) -> list[dict]:
    expo: dict[str, dict] = {}
    for t, v in vals.items():
        if t not in ETFS:   # a company held directly
            e = expo.setdefault(t, {"dollars": 0.0, "via": []})
            e["dollars"] += v
            e["via"].append("direct")
        for c, w in HOLDINGS.get(t, []):
            e = expo.setdefault(c, {"dollars": 0.0, "via": []})
            e["dollars"] += v * w
            e["via"].append(t)
    rows = [{"ticker": c, "name": market.name(c), "via": e["via"], "dollars": round(e["dollars"], 2),
             "share": round(e["dollars"] / total, 4) if total else 0.0,
             "period_pct": market.change_pct(c, month_ago, today)} for c, e in expo.items()]
    return sorted(rows, key=lambda r: -r["dollars"])[:top]


def _interests(acct, vals, total, month_ago, quarter_ago, today, mkt) -> list[dict]:
    rows = []
    for theme in intake.interests(acct.answers):
        t = THEMES[theme]
        m3 = market.change_pct(t, quarter_ago, today)
        rows.append({"theme": theme, "label": THEME_LABELS[theme], "ticker": t,
                     "m1": market.change_pct(t, month_ago, today), "m3": m3,
                     "vs_market_m3": round(m3 - mkt["m3"], 2),
                     "weight": round(vals.get(t, 0.0) / total, 4) if total else 0.0})
    return rows


def _market(today, month_ago, quarter_ago) -> dict:
    sectors = sorted(({"theme": r["theme"], "label": r["label"], "ticker": t,
                       "m1": market.change_pct(t, month_ago, today)}
                      for t, r in ETFS.items() if r["role"] == "tilt"), key=lambda s: -s["m1"])
    vol = market.volatility_pct(BENCHMARK, today)
    typical = round(market.MARKET_VOL * 100, 1)
    m1 = market.change_pct(BENCHMARK, month_ago, today)
    return {
        "m1": m1, "m3": market.change_pct(BENCHMARK, quarter_ago, today),
        "ytd": market.change_pct(BENCHMARK, date(today.year, 1, 1), today),
        "intl_m1": market.change_pct("VXUS", month_ago, today),
        "bonds_m1": market.change_pct("BND", month_ago, today),
        "below_high": market.below_high_pct(BENCHMARK, today),
        "vol": vol, "vol_typical": typical,
        "vol_word": "calmer than usual" if vol < typical * CALM else "choppier than usual" if vol > typical * CHOPPY else "about as bumpy as usual",
        "direction": "rising" if m1 >= 3 else "falling" if m1 <= -3 else "fairly flat",
        "leaders": sectors[:3], "laggards": sectors[-3:][::-1],
    }


def _relation(vals, total, period_pct, mkt) -> dict:
    weights = {t: v / total for t, v in vals.items()} if total else {}
    fund_beta = round(sum(w * market.beta(t) for t, w in weights.items()), 2)
    stock_share = round(sum(w for t, w in weights.items() if t not in NOT_STOCKS), 4)
    return {"beta": fund_beta, "move_on_10": round(fund_beta * 10, 1), "stock_share": stock_share,
            "fund_m1": period_pct, "market_m1": mkt["m1"]}


def _recommendations(acct, vals, total, holdings, companies, interests, mkt, tier) -> list[dict]:
    """The rules. Every rec lists the numbers that triggered it next to the threshold, in its facts
    ("tech is 41% of your fund; we flag at 35%"), and repeats them in rec["trigger"]."""
    recs: list[dict] = []
    pct = acct.invest_pct

    off = sorted((h for h in holdings if h["off_target"]), key=lambda h: -abs(h["drift"]))
    if total and off:
        facts, trig = [], []
        for h in off[:3]:
            seen, limit = f"{h['weight']:.1%}", f"{h['band'] * 100:.1f} points"
            facts.append(f"{h['ticker']} ({h['label']}) is {seen} of your fund; its target is {h['target']:.1%}. "
                         f"We flag a holding that is {limit} off its target ({abs(h['drift']) * 100:.1f} here).")
            trig.append({"what": h["ticker"], "text": facts[-1], "value_text": f"{abs(h['drift']) * 100:.1f}",
                         "threshold_text": limit})
        if tier.auto_trades:
            trades = [{"symbol": t, "side": "buy" if d > 0 else "sell", "notional": abs(d)}
                      for t, d in sorted(limited_rebalance(acct.portfolio, vals).items(), key=lambda kv: kv[1])]
            recs.append(_rec("rebalance", "Rebalance back to your targets",
                             facts + ["Selling what ran ahead and buying what fell behind keeps your risk where you chose it."],
                             trades=trades, trigger=trig))
        else:
            recs.append(_rec("steer", "Let your next paychecks fix the mix",
                             facts + ["New money goes to the funds furthest below target, so nothing needs selling."],
                             trigger=trig))

    sleeve = _stock_sleeve(holdings, acct.today)
    if sleeve and pct < 100 and (sleeve["m1"] <= DIP_TRIGGER or sleeve["below_high"] <= BELOW_HIGH_TRIGGER):
        new = min(100, pct + DIP_PCT_STEP)
        which = ("m1", f"{_pct(sleeve['m1'], 1)} this month", f"{DIP_TRIGGER:g}%") if sleeve["m1"] <= DIP_TRIGGER else \
            ("below_high", f"{abs(sleeve['below_high']):.1f}% below their high of the past year", f"{abs(BELOW_HIGH_TRIGGER):g}%")
        text = (f"Your stocks, {sleeve['share']:.0%} of your fund, moved {_pct(sleeve['m1'], 1)} this month and are "
                f"{abs(sleeve['below_high']):.1f}% below their high of the past year. We mention this at {DIP_TRIGGER:g}% "
                f"in a month or {abs(BELOW_HIGH_TRIGGER):g}% below the high.")
        recs.append(_rec("invest_more", "Prices are lower: consider investing a bit more for a while",
                         [text,
                          f"That is about {_pct(sleeve['m1'] * sleeve['share'], 1)} on the whole fund, since the rest is bonds and other holdings.",
                          f"Raising your paycheck share from {pct:g}% to {new:g}% buys more shares while they're cheaper.",
                          "Only do this if your checking account still covers your bills."], set_pct=new,
                         trigger=[{"what": "stocks", "text": text, "value_text": which[1], "threshold_text": which[2]}]))

    for i in interests:
        if i["vs_market_m3"] <= LAG_TRIGGER:
            text = (f"{i['ticker']} moved {_pct(i['m3'], 1)} over 3 months, {abs(i['vs_market_m3']):.1f} points behind the market. "
                    f"We mention this at {abs(LAG_TRIGGER):g} points.")
            recs.append(_rec("hold", f"Hold {i['label']} through the slump",
                             [text, "Selling after a drop locks the loss in. Your paychecks are buying it at lower prices now."],
                             trigger=[{"what": i["ticker"], "text": text, "value_text": f"{abs(i['vs_market_m3']):.1f}",
                                       "threshold_text": f"{abs(LAG_TRIGGER):g} points"}]))

    big = [c for c in companies if c["share"] >= CONCENTRATION_TRIGGER]
    if big:
        facts, trig, tilts, direct = [], [], [], []
        for c in big:
            facts.append(f"{c['name']} is about {c['share']:.1%} of your fund, through {', '.join(c['via'])}. "
                         f"We flag a company at {CONCENTRATION_TRIGGER:.0%}.")
            trig.append({"what": c["ticker"], "text": facts[-1], "value_text": f"{c['share']:.1%}",
                         "threshold_text": f"{CONCENTRATION_TRIGGER:.0%}"})
            tilts += [ETFS[t]["theme"] for t in c["via"] if t in ETFS and ETFS[t]["role"] == "tilt"]
            direct += [c["ticker"]] if "direct" in c["via"] else []
        title = f"{big[0]['name']} is a big piece of your fund" if len(big) == 1 else \
            f"{len(big)} companies are big pieces of your fund"
        recs.append(_rec("diversify", title,
                         facts + ["If one company has a bad year, you'll feel it. Fewer overlapping picks spreads that out."],
                         remove_interest=list(dict.fromkeys(tilts)), remove_stock=list(dict.fromkeys(direct)), trigger=trig))

    interest = _interest_parts(acct.portfolio, holdings)
    tilt_total = sum(interest.values())
    tech_picks = sum(w for t, w in interest.items() if _theme_of(t) in TECH_THEMES)
    sectors = _sector_exposure(vals, total, _own_sectors(acct.portfolio))
    tech_fund = sum(v for k, v in sectors.items() if k in TECH_SECTORS)
    held = set(intake.interests(acct.answers))
    picks_heavy = bool(tilt_total) and tech_picks / tilt_total >= TECH_HEAVY_SHARE
    if (picks_heavy or tech_fund >= TECH_FUND_TRIGGER) and not held & set(STEADY_THEMES):
        today, quarter_ago = acct.today, acct.today - timedelta(days=QUARTER_DAYS)
        options = sorted(STEADY_THEMES, key=lambda th: -market.change_pct(THEMES[th], quarter_ago, today))[:2]
        facts, trig = [], []
        text = (f"About {tech_fund:.0%} of your whole fund is in tech and communication companies, counting what's inside "
                f"your broad funds (approximate). We flag it at {TECH_FUND_TRIGGER:.0%}.")
        facts.append(text)
        trig.append({"what": "tech exposure", "text": text, "value_text": f"{tech_fund:.0%}",
                     "threshold_text": f"{TECH_FUND_TRIGGER:.0%}"})
        if picks_heavy:
            text = (f"{tech_picks / tilt_total:.0%} of your interest picks are tech-related, and they tend to rise and fall together. "
                    f"We flag it at {TECH_HEAVY_SHARE:.0%}.")
            facts.append(text)
            trig.append({"what": "interest picks", "text": text, "value_text": f"{tech_picks / tilt_total:.0%}",
                         "threshold_text": f"{TECH_HEAVY_SHARE:.0%}"})
        recs.append(_rec("explore", "Look into steadier sectors",
                         facts + [f"{THEME_LABELS[th]} ({THEMES[th]}) moved {_pct(market.change_pct(THEMES[th], quarter_ago, today), 1)} "
                                  f"over 3 months and usually swings less." for th in options],
                         add_interest=options, trigger=trig))

    if pct >= HIGH_PCT:
        new = max(0, pct - LOWER_PCT_STEP)
        text = f"You're investing {pct:g}% of each paycheck. We mention it at {HIGH_PCT}%."
        recs.append(_rec("invest_less", "Make sure you keep enough cash",
                         [text, "A common rule is to keep 3 to 6 months of expenses in savings before investing heavily.",
                          f"Lowering to {new:g}% leaves more in checking."], set_pct=new,
                         trigger=[{"what": "paycheck share", "text": text, "value_text": f"{pct:g}%", "threshold_text": f"{HIGH_PCT}%"}]))

    if not recs:
        worst = max(holdings, key=lambda h: abs(h["drift"]) / (h["band"] or 1), default=None)
        if worst:
            text = (f"Every fund is inside its band. The furthest is {worst['ticker']} at {abs(worst['drift']) * 100:.1f} points "
                    f"off its target; we flag it at {worst['band'] * 100:.1f}.")
        else:
            text = "There's nothing in the fund yet to check."
        recs.append(_rec("hold", "Stay the course",
                         [text, f"Keep investing {pct:g}% of each paycheck; steady buying does the heavy lifting."],
                         trigger=[{"what": "drift", "text": text,
                                   "value_text": f"{abs(worst['drift']) * 100:.1f}" if worst else "0",
                                   "threshold_text": f"{worst['band'] * 100:.1f}" if worst else "0"}]))
    return recs


def _stock_sleeve(holdings: list[dict], today: date) -> dict | None:
    """The stock part of this fund (everything but bonds and gold) as one index at today's weights: how far it
    moved this month and how far below its one-year high it sits. None if the fund holds no stocks."""
    stocks = {h["ticker"]: h["weight"] for h in holdings if h["ticker"] not in NOT_STOCKS and h["weight"] > 0}
    share = sum(stocks.values())
    if not share:
        return None
    w = {t: x / share for t, x in stocks.items()}
    days = [today - timedelta(days=n) for n in range(365, -1, -1)]
    px = {t: [market.raw_price(t, d) for d in days] for t in w}     # each day priced once
    index = [1.0]
    for i in range(1, len(days)):
        index.append(index[-1] * (1 + sum(x * (px[t][i] / px[t][i - 1] - 1) for t, x in w.items())))
    return {"share": round(share, 4), "m1": round((index[-1] / index[-31] - 1) * 100, 2),
            "below_high": round((index[-1] / max(index) - 1) * 100, 2)}


def _personal_share(p) -> float:
    """The customer's own picks: where they spend, their interests, companies they added (exact parts for a
    custom index; otherwise every holding outside the core funds)."""
    if getattr(p, "parts", None):
        return round(sum(sum(p.parts.get(k, {}).values()) for k in ("spend", "interest", "added")), 4)
    return round(sum(h.weight for h in p.holdings if h.role != "core"), 4)


def _interest_parts(p, holdings: list[dict]) -> dict[str, float]:
    """{ticker: target share} of the interest slice. The custom-index builder records it exactly (a company held
    for an interest and in the US index counts only its interest part); otherwise it's the tilt holdings."""
    if getattr(p, "parts", None):
        return dict(p.parts.get("interest", {}))
    return {h["ticker"]: h["target"] for h in holdings if h["role"] == "tilt"}


def _theme_of(ticker: str) -> str:
    """An interest's theme: a theme fund's own, or a company's home fund's."""
    if ticker in ETFS:
        return ETFS[ticker]["theme"]
    home = market.COMPANIES.get(ticker, {}).get("etf")
    return ETFS[home]["theme"] if home in ETFS else ""


def _sector_exposure(vals: dict[str, float], total: float, own_sector: dict[str, str] | None = None) -> dict[str, float]:
    """Share of the whole fund per sector. The broad funds are looked through with data/sectors.csv (approximate);
    a company held directly counts in its own sector, taken from the fund's holdings (`own_sector`)."""
    own_sector = own_sector or {}
    out: dict[str, float] = {}
    if not total:
        return out
    for t, v in vals.items():
        if t in own_sector:
            mix = {own_sector[t]: 1.0}
        elif t in CORE_SECTORS:
            mix = CORE_SECTORS[t]
        elif t in ETFS:
            sector = THEME_SECTOR.get(ETFS[t]["theme"])
            mix = {sector: 1.0} if sector else {}
        elif t in market.COMPANIES:
            sector = THEME_SECTOR.get(ETFS[market.COMPANIES[t]["etf"]]["theme"])
            mix = {sector: 1.0} if sector else {}
        else:
            mix = {}
        for sector, w in mix.items():
            out[sector] = out.get(sector, 0.0) + v / total * w
    return out


def _own_sectors(p) -> dict[str, str]:
    """{ticker: sector} for the companies the fund holds directly (a fund has no sector of its own)."""
    return {h.ticker: h.sector for h in p.holdings if h.sector}


def _pct(x: float, digits: int = 1) -> str:
    """Signed percent without a "-0.0%" for tiny losses."""
    v = round(x, digits) + 0.0
    return f"{v:+.{digits}f}%"


def _rec(action: str, title: str, facts: list[str], trades: list[dict] | None = None,
         set_pct: float | None = None, add_interest: list[str] | None = None,
         remove_interest: list[str] | None = None, remove_stock: list[str] | None = None,
         trigger: list[dict] | None = None) -> dict:
    return {"action": action, "title": title, "facts": facts, "trigger": trigger or [], "trades": trades or [],
            "set_pct": set_pct,
            "add_interest": add_interest or [], "remove_interest": remove_interest or [],
            "remove_stock": remove_stock or []}


def value_history(acct: FundAccount, points: int = 30) -> dict | None:
    """Fund value and money put in over time, replayed from the broker's orders."""
    orders = get_broker().orders(acct.customer_id)
    if not orders:
        return None
    first, today = orders[0]["on"], acct.today
    days = (today - first).days
    step = max(1, days // points)
    dates = sorted({first + timedelta(days=d) for d in range(0, days + 1, step)} | {today})
    out = {"labels": [], "value": [], "contributed": []}
    for d in dates:
        shares: dict[str, float] = {}
        put_in = 0.0
        for o in orders:
            if o["on"] > d:
                break
            sign = 1 if o["side"] == "buy" else -1
            shares[o["symbol"]] = shares.get(o["symbol"], 0.0) + sign * o["shares"]
            put_in += sign * o["notional"]
        out["labels"].append(d.isoformat())
        out["value"].append(round(sum(s * market.raw_price(t, d) for t, s in shares.items()), 2))
        out["contributed"].append(round(put_in, 2))
    return out


def _facts(rep: dict) -> list[str]:
    """Short, finished sentences, every number from the report above, most relevant first: the customer's own fund,
    then their money, then the market. Claude explains these and may not add numbers."""
    s, m, r, ret = rep["summary"], rep["market"], rep["relation"], rep["returns"]
    p, since = ret["period"], ret["since_start"]
    facts = [f"Fund value ${s['value']:,.2f}; you've put in ${s['contributed']:,.2f}; gain {_usd(s['gain'])} ({_pct(s['gain_pct'], 2)} on your money, prices only)."]
    if p and p["twr_pct"] is not None and p["days"] > 0:
        span = f"since you started on {_day(p['start'])}" if p["from_start"] else f"from {_day(p['start'])} to {_day(p['end'])}"
        facts.append(f"{span[0].upper()}{span[1:]} ({p['days']} days), your fund returned {_pct(p['twr_pct'], 2)} including estimated income "
                     f"({_pct(p['twr_price_pct'], 2)} from prices alone).")
        facts.append(f"Its benchmark, your plain mix (the same stock / bond split, {1 - ret['benchmark']['BND']:.0%} / "
                     f"{ret['benchmark']['BND']:.0%}, with nothing personal: your US index at its normal weights, VXUS and BND) "
                     f"with the same deposits, returned {_pct(p['benchmark_pct'], 2)}, so your choices added {_pct(p['vs_benchmark_pts'], 2)[:-1]} points. "
                     f"The S&P 500 returned {_pct(p['spy_pct'], 2)}.")
        facts.append(f"In those {p['days']} days the fund started at ${p['start_value']:,.2f}, you added ${p['deposits']:,.2f}, "
                     f"the market moved it {_usd(p['market_gain'], True)}, and it ended at ${p['end_value']:,.2f}.")
        moved = p["attribution"]
        if moved:
            up, down = max(moved, key=lambda x: x["dollars"]), min(moved, key=lambda x: x["dollars"])
            facts.append(f"Biggest gain: {up['ticker']} ({up['label']}) {_usd(up['dollars'], True)}."
                         + (f" Biggest drag: {down['ticker']} ({down['label']}) {_usd(down['dollars'], True)}." if down["dollars"] < 0 else ""))
    else:
        facts.append("The fund has just started, so there is no return to report yet.")
    if since and since["mwr_pct"] is not None and since["days"] > 0:
        facts.append(f"Since you started on {_day(since['start'])} you've put in ${since['deposits']:,.2f}; the fund is worth "
                     f"${since['end_value']:,.2f}. Your own money returned {_pct(since['mwr_pct'], 2)} (it counts when each deposit landed); "
                     f"the fund itself returned {_pct(since['twr_pct'], 2)}.")
    for x in (p["picks"] if p else []):
        facts.append(f"Your pick {x['label']} ({x['ticker']}, {x['weight']:.1%} of the fund) moved {_pct(x['move_pct'], 1)}, "
                     f"{_usd(x['dollars'], True)}; the core stock funds it replaced moved {_pct(x['core_pct'], 1)}.")
    facts.append(f"{r['stock_share']:.0%} of the fund is in stocks; it typically moves about {r['move_on_10']:g}% when the market moves 10%.")
    if rep["companies"]:
        top = rep["companies"][0]
        facts.append(f"Biggest company inside the fund: {top['name']}, about {top['share']:.1%} (approximate look-through).")
    for x in rep["recommendations"]:
        facts.append(f"Recommendation: {x['title']}. " + " ".join(t["text"] for t in x["trigger"][:2]))
    proj, pin = rep["projection"], rep["projection_inputs"]
    facts.append(f"Looking ahead, with ${pin['monthly']:,.0f} a month going in and an assumed {pin['return_pct']:g}% a year "
                 f"(an assumption, not a forecast), the typical 10-year result is ${proj.p50[-1]:,.0f}, within ${proj.p10[-1]:,.0f} to ${proj.p90[-1]:,.0f}.")
    facts += rep["finances"]["facts"]
    facts.append(f"The market is {m['direction']} and {m['vol_word']}: the S&P 500 is {_pct(m['m3'], 1)} over 3 months, {_pct(m['ytd'], 1)} this year.")
    facts.append(rep["data_notes"]["price"])
    return facts


def _usd(x: float, signed: bool = False) -> str:
    """Dollars with the sign in front of the $: -$192.00, +$1,843.71 (never "$-192.00" or "$+1,843.71")."""
    return f"{'-' if x < 0 else '+' if signed else ''}${abs(x):,.2f}"


def _day(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _narrative(rep: dict) -> str:
    """Two short paragraphs from the same facts, for when Claude isn't available. Plain, and only the report's numbers."""
    ret, s = rep["returns"], rep["summary"]
    p, since, fin = ret["period"], ret["since_start"], rep["finances"]
    if p and p["twr_pct"] is not None and p["days"] > 0:
        first = (f"Your fund is worth ${s['value']:,.2f}. Over the last {p['days']} days it returned {_pct(p['twr_pct'], 2)}, against "
                 f"{_pct(p['benchmark_pct'], 2)} for a mix of the same core funds at your stock/bond split and {_pct(p['spy_pct'], 2)} "
                 f"for the S&P 500. The gap to that mix comes from your own choices: {'your interest picks and company holdings' if p['picks'] else 'how far the funds have drifted from your targets'}.")
        if p["attribution"]:
            top = max(p["attribution"], key=lambda x: x["dollars"])
            first += f" {top['ticker']} added the most, {_usd(top['dollars'], True)}."
    else:
        first = f"Your fund is worth ${s['value']:,.2f} and has only just started, so there's no return to compare yet."
    if since and since["days"] > 0 and since["mwr_pct"] is not None:
        first += f" Counting every deposit, your own money has returned {_pct(since['mwr_pct'], 2)} since {_day(since['start'])}."
    b = fin["budget"]
    second = (f"Each paycheck puts ${b['per_paycheck']:,.0f} in, about ${b['monthly_invest']:,.0f} in a typical month, and your cash cushion "
              f"covers {fin['safety']['months']:.1f} months of expenses. ")
    second += ("A card at {apr:g}% costs more than the fund is assumed to earn, so paying it down is also a good use of money; the choice is yours. "
               .format(apr=fin["debt"]["apr"]) if fin["debt"]["beats_fund"] else "")
    second += f"The recommendations below say what, if anything, is worth doing now. {rep['data_notes']['price']}"
    return first + "\n\n" + second


