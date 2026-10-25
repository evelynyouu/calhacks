"""The fund report.  OWNER: Lance.

Every number is computed here, in Python. Claude only writes the short narrative on top, from
report["facts"]. Sections: summary, holdings, companies (looking through each fund to the top
companies it holds), the customer's interests, the broad market and what it means for this fund,
recommendations (some with trades the customer can confirm), and the review schedule.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import intake, market, returns
from core.broker import get_broker, values
from core.fund import FundAccount
from core.market import ETFS, HOLDINGS
from core.policy import tier_for
from core.portfolio import THEME_LABELS, THEMES
from core.profile import get_profile
from core.rebalance import drift as drift_of, rebalance_trades
from core.sim import simulate

QUARTER_DAYS = 91
BENCHMARK = "SPY"               # "the market" line
US_SHARE_OF_STOCKS = 0.70       # VTI's share of VTI+VXUS when the fund has neither to read it from
NOT_STOCKS = {"BND", "GLD"}

DRIFT_TRIGGER = 0.05            # a fund 5 points off its target weight -> rebalance
DIP_TRIGGER = -7.0              # market down 7% in a month ...
BELOW_HIGH_TRIGGER = -10.0      # ... or 10% below its 1-year high -> consider investing more
DIP_PCT_STEP = 5                # suggested raise in paycheck percent during a dip
LAG_TRIGGER = -10.0             # an interest 10 points behind the market over 3 months -> hold, don't sell
CONCENTRATION_TRIGGER = 0.08    # one company 8% of the fund -> diversify
TECH_HEAVY_SHARE = 0.6          # 60% of the interest slice in tech-like themes -> look at other sectors
HIGH_PCT = 50                   # investing half of each paycheck or more -> check cash
LOWER_PCT_STEP = 10
TECH_THEMES = {"tech", "ai_chips", "software", "cyber", "robotics", "communications", "gaming"}
STEADY_THEMES = ["healthcare", "staples", "utilities", "dividends"]
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
        "gain_pct": since["mwr_pct"] if since and since["mwr_pct"] is not None
        else round(gain / contributed * 100, 2) if contributed else 0.0,
        "period_pct": period_pct, "market_period_pct": mkt["m1"],
        "vs_market_pts": round(period_pct - mkt["m1"], 2),
        "benchmark_period_pct": period["benchmark_pct"] if period else None,
        "vs_benchmark_pts": period["vs_benchmark_pts"] if period else None,
        "invest_pct": acct.invest_pct, "fee_pct": p.annual_fee_pct,
        "fee_dollars": round(total * p.annual_fee_pct / 100, 2),
    }
    companies = _companies(vals, total, month_ago, today)
    interests = _interests(acct, vals, total, month_ago, quarter_ago, today, mkt)
    relation = _relation(vals, total, period_pct, mkt)
    recs = _recommendations(acct, vals, total, holdings, companies, interests, mkt, tier)

    paycheck = get_profile(cid).paycheck_amount
    monthly = round(paycheck * acct.invest_pct / 100 * 2, 2)   # two paychecks a month
    sim = simulate(p, total, monthly)
    next_review = (acct.last_review or today) + timedelta(days=tier.review_every_days)
    rep = {
        "as_of": today, "period_days": period["days"] if period else tier.review_every_days, "summary": summary,
        "returns": rets, "holdings": holdings,
        "companies": companies, "interests": interests, "market": mkt, "relation": relation,
        "recommendations": recs, "projection": sim, "history": value_history(acct),
        "schedule": {"tier": tier, "review_every_days": tier.review_every_days, "auto_allowed": tier.auto_trades,
                     "auto_on": acct.auto_trade, "next_review": next_review},
        "broker_mode": get_broker().mode,
    }
    rep["facts"] = _facts(rep)
    return rep


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
    return {"available": True, "first_order": first, "benchmark": bench,
            "period": _window(orders, first, today, None if young else start, bench, acct.portfolio),
            "since_start": _window(orders, first, today, None, bench, acct.portfolio)}


def benchmark_weights(p) -> dict[str, float]:
    """The fund's own core mix: VTI/VXUS/BND at the fund's stock/bond split, with the interest and single-company
    slices put back into VTI/VXUS. Read from the target weights, so it holds for any bond share."""
    w = {h.ticker: h.weight for h in p.holdings}
    bonds = w.get("BND", 0.0)
    us, intl = w.get("VTI", 0.0), w.get("VXUS", 0.0)
    us_frac = us / (us + intl) if us + intl > 0 else US_SHARE_OF_STOCKS
    return {"VTI": round((1 - bonds) * us_frac, 6), "VXUS": round((1 - bonds) * (1 - us_frac), 6), "BND": round(bonds, 6)}


def _window(orders: list[dict], first: date, end: date, start: date | None, bench: dict[str, float], p) -> dict:
    """Dollars, returns, benchmark and per-holding gains for [start, end]. start=None means from the first
    deposit, which then counts as new money. The benchmark and the S&P 500 line get the same deposits on the
    same days, so "vs benchmark" is only the customer's choices (interests, companies, drift)."""
    px, from_start = market.raw_price, start is None
    begin = returns.day_before(first) if from_start else start
    r = returns.replay(orders, px, begin, end)
    flows = returns.net_flows(orders, px)
    blend = returns.replay(returns.shadow_orders(flows, bench, px), px, begin, end)
    spy = returns.replay(returns.shadow_orders(flows, {BENCHMARK: 1.0}, px), px, begin, end)
    twr, mwr, b_twr, s_twr = returns.twr(r), returns.money_weighted(r), returns.twr(blend), returns.twr(spy)
    pct = lambda x: None if x is None else round(x * 100, 2)   # noqa: E731
    gap = lambda a, b: None if a is None or b is None else round((a - b) * 100, 2)   # noqa: E731
    return {
        "start": first if from_start else start, "end": end, "days": (end - (first if from_start else start)).days,
        "from_start": from_start, "start_value": round(r.start_value, 2), "deposits": round(r.deposits, 2),
        "market_gain": round(r.market_gain, 2), "end_value": round(r.end_value, 2),
        "twr_pct": pct(twr), "mwr_pct": pct(mwr),
        "benchmark_pct": pct(b_twr), "benchmark_value": round(blend.end_value, 2), "vs_benchmark_pts": gap(twr, b_twr),
        "spy_pct": pct(s_twr), "vs_spy_pts": gap(twr, s_twr),
        "attribution": _attribution(r, p), "picks": _picks(r, p, bench, px),
    }


def _attribution(r: returns.Replay, p) -> list[dict]:
    """What moved the fund: dollars each holding added or cost, biggest first. Adds up to the market gain."""
    roles = {h.ticker: h.role for h in p.holdings}
    rows = [{"ticker": t, "label": market.info(t)["label"], "role": roles.get(t, "tilt"), "dollars": round(d, 2)}
            for t, d in returns.attribution(r).items()]
    return sorted(rows, key=lambda x: -abs(x["dollars"]))


def _picks(r: returns.Replay, p, bench: dict[str, float], px) -> list[dict]:
    """Each interest fund and single company: its price move, the dollars it added, and how it did against
    the core stock funds it replaced (VTI/VXUS at the fund's own US/international split)."""
    stocks = bench["VTI"] + bench["VXUS"]
    core = {"VTI": bench["VTI"] / stocks, "VXUS": bench["VXUS"] / stocks} if stocks else {"VTI": 1.0}
    gains = returns.attribution(r)
    core_pct = round(sum(w * market.change_pct(t, r.start, r.end) for t, w in core.items()), 2)
    rows = []
    for h in p.holdings:
        if h.role == "core":
            continue
        move = market.change_pct(h.ticker, r.start, r.end)
        rows.append({"ticker": h.ticker, "label": market.info(h.ticker)["label"], "role": h.role,
                     "weight": h.weight, "move_pct": move, "dollars": round(gains.get(h.ticker, 0.0), 2),
                     "core_pct": core_pct, "vs_core_pts": round(move - core_pct, 2),
                     "vs_core_dollars": round(returns.excess_over(r, h.ticker, core, px), 2)})
    return rows


def _holdings(acct, vals, total, month_ago) -> list[dict]:
    p, today = acct.portfolio, acct.today
    targets = {h.ticker: h.weight for h in p.holdings}
    shares, cost = get_broker().positions(acct.customer_id), get_broker().cost_basis(acct.customer_id)
    drifts = drift_of(p, vals) if total else {t: -w for t, w in targets.items()}
    tickers = list(targets) + [t for t in vals if t not in targets]
    day = market.quotes(tickers, today)
    rows = []
    for t in tickers:
        v = vals.get(t, 0.0)
        row = market.info(t)
        rows.append({
            "ticker": t, "name": row["name"], "label": row["label"], "about": row["about"],
            "role": row["role"] if row["role"] in ("core", "stock") else "tilt", "shares": round(shares.get(t, 0.0), 4),
            "day_pct": day[t]["change_pct"] if t in day else None,
            "price": market.price(t, today), "value": round(v, 2),
            "weight": round(v / total, 4) if total else 0.0, "target": targets.get(t, 0.0),
            "drift": drifts.get(t, 0.0), "period_pct": market.change_pct(t, month_ago, today),
            "gain_pct": round((v / cost[t] - 1) * 100, 2) if cost.get(t) else None,
        })
    return rows


def _companies(vals, total, month_ago, today, top: int = 10) -> list[dict]:
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
    recs: list[dict] = []
    pct = acct.invest_pct

    off = sorted((h for h in holdings if abs(h["drift"]) >= DRIFT_TRIGGER), key=lambda h: -abs(h["drift"]))
    if total and off:
        facts = [f"{h['ticker']} ({h['label']}) is {h['weight']:.0%} of your fund; its target is {h['target']:.0%}."
                 for h in off[:3]]
        if tier.auto_trades:
            trades = [{"symbol": t, "side": "buy" if d > 0 else "sell", "notional": abs(d)}
                      for t, d in sorted(rebalance_trades(acct.portfolio, vals).items(), key=lambda kv: kv[1])]
            recs.append(_rec("rebalance", "Rebalance back to your targets",
                             facts + ["Selling what ran ahead and buying what fell behind keeps your risk where you chose it."],
                             trades=trades))
        else:
            recs.append(_rec("steer", "Let your next paychecks fix the mix",
                             facts + ["New money goes to the funds furthest below target, so nothing needs selling."]))

    if pct < 100 and (mkt["m1"] <= DIP_TRIGGER or mkt["below_high"] <= BELOW_HIGH_TRIGGER):
        new = min(100, pct + DIP_PCT_STEP)
        recs.append(_rec("invest_more", "Prices are lower: consider investing a bit more for a while",
                         [f"The S&P 500 moved {_pct(mkt['m1'], 1)} this month and is {abs(mkt['below_high']):.1f}% below its high of the past year.",
                          f"Raising your paycheck share from {pct:g}% to {new:g}% buys more shares while they're cheaper.",
                          "Only do this if your checking account still covers your bills."], set_pct=new))

    for i in interests:
        if i["vs_market_m3"] <= LAG_TRIGGER:
            recs.append(_rec("hold", f"Hold {i['label']} through the slump",
                             [f"{i['ticker']} moved {_pct(i['m3'], 1)} over 3 months, {abs(i['vs_market_m3']):.1f} points behind the market.",
                              "Selling after a drop locks the loss in. Your paychecks are buying it at lower prices now."]))

    if companies and companies[0]["share"] >= CONCENTRATION_TRIGGER:
        c = companies[0]
        tilts = [t for t in c["via"] if t in ETFS and ETFS[t]["role"] == "tilt"]
        recs.append(_rec("diversify", f"{c['name']} is a big piece of your fund",
                         [f"{c['name']} is about {c['share']:.0%} of your fund, through {', '.join(c['via'])}.",
                          "If one company has a bad year, you'll feel it. Fewer overlapping picks spreads that out."],
                         remove_interest=[ETFS[t]["theme"] for t in tilts],
                         remove_stock=[c["ticker"]] if "direct" in c["via"] else []))

    tilt_total = sum(h["target"] for h in holdings if h["role"] == "tilt")
    tech = sum(h["target"] for h in holdings if h["role"] == "tilt" and ETFS[h["ticker"]]["theme"] in TECH_THEMES)
    held = set(intake.interests(acct.answers))
    if tilt_total and tech / tilt_total >= TECH_HEAVY_SHARE and not held & set(STEADY_THEMES):
        today, quarter_ago = acct.today, acct.today - timedelta(days=QUARTER_DAYS)
        options = sorted(STEADY_THEMES, key=lambda th: -market.change_pct(THEMES[th], quarter_ago, today))[:2]
        recs.append(_rec("explore", "Look into steadier sectors",
                         [f"{tech / tilt_total:.0%} of your interest picks are tech-related, and they tend to rise and fall together."]
                         + [f"{THEME_LABELS[th]} ({THEMES[th]}) moved {_pct(market.change_pct(THEMES[th], quarter_ago, today), 1)} "
                            f"over 3 months and usually swings less." for th in options],
                         add_interest=options))

    if pct >= HIGH_PCT:
        new = max(0, pct - LOWER_PCT_STEP)
        recs.append(_rec("invest_less", "Make sure you keep enough cash",
                         [f"You're investing {pct:g}% of each paycheck.",
                          "A common rule is to keep 3 to 6 months of expenses in savings before investing heavily.",
                          f"Lowering to {new:g}% leaves more in checking."], set_pct=new))

    if not recs:
        worst = max((abs(h["drift"]) for h in holdings), default=0.0)
        recs.append(_rec("hold", "Stay the course",
                         [f"Every fund is within {worst * 100:.1f} points of its target.",
                          f"Keep investing {pct:g}% of each paycheck; steady buying does the heavy lifting."]))
    return recs


def _pct(x: float, digits: int = 1) -> str:
    """Signed percent without a "-0.0%" for tiny losses."""
    v = round(x, digits) + 0.0
    return f"{v:+.{digits}f}%"


def _rec(action: str, title: str, facts: list[str], trades: list[dict] | None = None,
         set_pct: float | None = None, add_interest: list[str] | None = None,
         remove_interest: list[str] | None = None, remove_stock: list[str] | None = None) -> dict:
    return {"action": action, "title": title, "facts": facts, "trades": trades or [], "set_pct": set_pct,
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
    s, m, r = rep["summary"], rep["market"], rep["relation"]
    facts = [
        f"Fund value ${s['value']:,.2f}; you've put in ${s['contributed']:,.2f}; "
        f"gain ${s['gain']:,.2f} ({_pct(s['gain_pct'], 2)}).",
        f"Past {rep['period_days']} days: your fund {_pct(s['period_pct'], 2)}, the S&P 500 {_pct(s['market_period_pct'], 2)}.",
        f"The market is {m['direction']} and {m['vol_word']}: {_pct(m['m3'], 1)} over 3 months, {_pct(m['ytd'], 1)} this year.",
        f"Leading sectors this month: " + ", ".join(f"{x['label']} {_pct(x['m1'], 1)}" for x in m["leaders"]) + ".",
        f"Your fund typically moves about {r['move_on_10']:g}% when the market moves 10%; "
        f"{r['stock_share']:.0%} of it is in stocks.",
    ]
    for i in rep["interests"]:
        facts.append(f"Your interest {i['label']} ({i['ticker']}): {_pct(i['m1'], 1)} this month, {_pct(i['m3'], 1)} over 3 months.")
    if rep["companies"]:
        top = rep["companies"][0]
        facts.append(f"Biggest company in your fund: {top['name']}, about {top['share']:.1%}, {_pct(top['period_pct'], 1)} this month.")
    facts += [f"Recommendation: {x['title']}." for x in rep["recommendations"]]
    return facts
