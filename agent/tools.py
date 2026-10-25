"""Claude tools. Each wraps a Python function; Claude never computes numbers.  OWNER: Ethan."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta

from app.state import Session
from core import feed, fund, intake, market, nessie, spending
from core.broker import values
from core.policy import tier_for
from core.portfolio import build_custom_portfolio, custom_fund_preview
from core.market import ETFS
from core.portfolio import THEME_LABELS, THEMES
from core.explain import explain_for_chat
from core.profile import get_profile
from core.report import build_report

INDEXES = ["SPY", "QQQ", "DIA"]

THEME_ENUM = sorted(THEMES)

TOOLS = [
    {
        "name": "get_fund",
        "description": "The customer's fund: each holding with its target weight and what it is, the risk level "
                       "and score, their interests, any overlap notes, the yearly fee, and their paycheck percent.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_fund_explanation",
        "description": "Why the fund is built the way it is: the builder's own reasons for the risk score, the "
                       "bond share, each holding, and anything it capped, shrank, dropped or flagged. Pass a ticker "
                       "to answer \"why do I own X?\". Same text as the How your fund was built page.",
        "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}},
    },
    {
        "name": "get_report",
        "description": "The latest report, all computed by the app: value and money put in; the fund's return over "
                       "the review period and since the start (time-weighted for the fund, money-weighted for the "
                       "customer's own dollars) against a benchmark built from the fund's own core mix and against "
                       "the S&P 500; what moved the fund in dollars per holding; each pick against the core it "
                       "replaced; holdings, the biggest companies and sectors inside the fund; recommendations with "
                       "the numbers that triggered them; the 10-year range with its assumptions; the customer's "
                       "finances (budget, cash cushion, card debt, where the fund fits); the market; and notes on "
                       "which prices are simulated and what is approximate.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_interests",
        "description": "Add or remove industries from the customer's fund. Rebuilds the target mix; nothing is "
                       "sold until the customer confirms a rebalance. Only call when the customer asks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "add": {"type": "array", "items": {"type": "string", "enum": THEME_ENUM}},
                "remove": {"type": "array", "items": {"type": "string", "enum": THEME_ENUM}},
            },
        },
    },
    {
        "name": "get_market_today",
        "description": "Today's price and day change for the S&P 500 (SPY), Nasdaq 100 (QQQ), Dow (DIA), every "
                       "holding in the customer's fund, and their watchlist. Each has a source: live or simulated.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_news",
        "description": "Recent headlines with links, for the given tickers (default: the customer's holdings "
                       "and the biggest companies inside them), or the general market feed if market=true.",
        "input_schema": {"type": "object", "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}},
            "market": {"type": "boolean"}}},
    },
    {
        "name": "add_stock",
        "description": "Add a single company to the customer's fund (capped at 5% each). Only when they ask.",
        "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
    },
    {
        "name": "remove_stock",
        "description": "Remove a single company from the customer's fund targets. Only when they ask.",
        "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
    },
    {
        "name": "set_paycheck_percent",
        "description": "Change the percent of each paycheck that's invested. Only call when the customer asks.",
        "input_schema": {"type": "object", "properties": {"percent": {"type": "number", "minimum": 0, "maximum": 100}},
                         "required": ["percent"]},
    },
    {
        "name": "get_custom_fund_preview",
        "description": "Show what a custom fund would look like: the exact individual stocks that would replace "
                       "the sector ETFs for the customer's current interests, plus company suggestions from their "
                       "spending history. Does not change anything. Call this before switch_to_custom_fund so the "
                       "customer can see the holdings first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "switch_to_custom_fund",
        "description": "Rebuild the fund using individual stocks instead of sector ETFs. Each interest's slice "
                       "becomes its top 3 companies (e.g. tech → NVDA + MSFT + AAPL). Optionally include "
                       "spending-derived stocks. Only call when the customer confirms they want this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spending_stocks": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Company tickers to add from spending history (e.g. ['COST', 'WMT']). "
                                   "Leave empty to use only interest-derived stocks.",
                },
            },
        },
    },
    {
        "name": "switch_to_etf_fund",
        "description": "Switch to sector ETF mode (VTI/VXUS/BND + sector tilts) instead of individual stocks. "
                       "Only call when the customer explicitly asks for ETFs over individual stocks.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_spending_insights",
        "description": "Spending breakdown from the customer's C1/Nessie bank data: what categories they spend on, "
                       "up to 5 fund themes that match their real spending habits (e.g. dining → consumer sector), "
                       "a reinvestment signal if actual spending came in under their monthly baseline, "
                       "and a risk-level sanity-check driven by cushion size, card debt and monthly surplus. "
                       "Use this to suggest fund themes or flag a mismatch between intake risk and real finances.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "toggle_auto_trade",
        "description": "Turn automatic trade execution on or off. Only call when the customer asks. "
                       "When on and the customer is Tier 4+ ($50K+ AUM), rebalancing trades execute automatically "
                       "on each scheduled review without a confirmation click. Lower tiers still queue trades for "
                       "the customer to confirm.",
        "input_schema": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
    },
]


def fund_summary(session: Session) -> dict:
    a = session.fund
    p = a.portfolio
    return {
        "risk_label": p.risk_label, "risk_level": p.risk_level, "risk_score_out_of_100": a.risk_score,
        "risk_score_used": p.score,
        "limits": [r.text for r in p.reasons if r.kind in ("contradiction", "horizon", "drop", "override")],
        "paycheck_percent": a.invest_pct, "goal": a.answers.get("goal"),
        "interests": [THEMES[t] + " " + ETFS[THEMES[t]]["label"] for t in intake.interests(a.answers)],
        "interests_without_a_fund": a.answers.get("interests", {}).get("unmatched", []),
        "single_companies": intake.stocks(a.answers),
        "fund_mode": ("custom" if a.answers.get("custom_mode") else
                      "spending" if a.answers.get("spending_mode") else "etf"),
        "holdings": [{"ticker": h.ticker, "what": market.info(h.ticker)["about"],
                      "target_pct": round(h.weight * 100, 1), "role": h.role,
                      "why": p.holding_reasons.get(h.ticker, "")} for h in p.holdings],
        "yearly_fee_pct": p.annual_fee_pct, "overlap_notes": p.overlap_warnings,
    }


def _plain(x):
    """Dates and dataclasses to JSON-safe values for a tool result."""
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if is_dataclass(x) and not isinstance(x, type):
        return _plain(asdict(x))
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def report_summary(session: Session) -> dict:
    rep = build_report(session.fund)
    ret, proj = rep["returns"], rep["projection"]

    def window(w):
        if not w:
            return None
        keep = {k: v for k, v in w.items() if k not in ("attribution", "picks")}
        return {**keep, "what_moved_the_fund": w["attribution"][:8], "picks": w["picks"]}

    return _plain({
        "as_of": rep["as_of"], "summary": rep["summary"],
        "period": window(ret["period"]), "since_start": window(ret["since_start"]),
        "benchmark_weights": ret["benchmark"],
        "benchmark_note": "The benchmark is VTI/VXUS/BND at this fund's own stock/bond split, with the same deposits.",
        "holdings": [{k: h[k] for k in ("ticker", "label", "value", "weight", "target", "off_target", "day_pct",
                                        "period_pct", "gain_pct", "simulated")} for h in rep["holdings"]],
        "top_companies": [{k: c[k] for k in ("name", "ticker", "share", "via", "period_pct")} for c in rep["companies"][:5]],
        "sectors": rep["sectors"], "interests": rep["interests"], "market": rep["market"], "fund_vs_market": rep["relation"],
        "recommendations": [{"title": r["title"], "facts": r["facts"], "trades": r["trades"]} for r in rep["recommendations"]],
        "projection": {"typical_10y": proj.p50[-1], "low_10y": proj.p10[-1], "high_10y": proj.p90[-1],
                       "assumptions": {k: v for k, v in rep["projection_inputs"].items() if k != "rows"},
                       "note": "A range built on assumptions, not a forecast."},
        "finances": {k: v for k, v in rep["finances"].items() if k != "facts"},
        "data_notes": rep["data_notes"], "facts": rep["facts"],
    })


def market_today(session: Session) -> dict:
    a = session.fund
    held = [h.ticker for h in a.portfolio.holdings] if a.portfolio else []
    q = market.quotes(INDEXES + held + session.watchlist, a.today)
    pick = lambda ts: [{k: q[t][k] for k in ("symbol", "name", "price", "change", "change_pct", "source")} for t in ts if t in q]
    return {"as_of": a.today.isoformat(), "indexes": pick(INDEXES), "holdings": pick(held), "watchlist": pick(session.watchlist)}


# ---------- Account snapshot: what Claude sees about this customer on every chat turn ----------
# Python computes every figure (account_facts); account_snapshot only formats them, so each number in the
# text is a value in the facts. List lengths are capped to keep it near 1,000-1,500 tokens; anything more
# detailed is a tool call (get_fund, get_report, get_fund_explanation, get_spending_insights).

SNAPSHOT_HOLDINGS = 10
SNAPSHOT_PAYCHECKS = 3
SNAPSHOT_ACTIVITY = 8
SNAPSHOT_EVENTS = 4
SNAPSHOT_WATCHLIST = 6
RISK_LEVEL_MAX, RISK_SCORE_MAX = 4, 100   # the scales core/intake.py scores on
SNAPSHOT_MAX_CHARS = 6000    # ~1,500 tokens; the text is cut at a line break past this


def _r2(v) -> float:
    return round(float(v), 2)


def account_facts(session: Session) -> dict:
    """The customer's account as plain data, all computed here from the fund, broker, bank and profile."""
    from core.followups import HORIZON_LABELS   # lazily: keeps this module's imports light
    a = session.fund
    p = a.portfolio
    profile = get_profile(a.customer_id)
    first = (profile.name or "").split(" ")[0]
    facts: dict = {"as_of": a.today.isoformat(), "prices": market.price_label(a.today), "name": first,
                   "goal": intake.answer_label("goal", a.answers) if a.answers.get("goal") else None,
                   "horizon": HORIZON_LABELS.get(a.answers.get("horizon")) if a.answers.get("horizon") else None}
    facts["preferences"] = {"weekly_report": bool(session.prefs.get("weekly_report")),
                            "trade_confirmations": bool(session.prefs.get("trade_confirmations")),
                            "big_move_alerts": bool(session.prefs.get("big_moves")),
                            "big_move_pct": session.prefs.get("big_move_pct"),
                            "watchlist": list(session.watchlist[:SNAPSHOT_WATCHLIST])}
    nxt = fund.next_paycheck(a)
    facts["paycheck"] = {"amount": _r2(profile.paycheck_amount), "invest_pct": float(a.invest_pct),
                         "next_on": nxt["on"].isoformat(), "next_invests": _r2(nxt["invested"]),
                         "recent": [{"on": d["on"].isoformat(), "paycheck": _r2(d["paycheck"]), "pct": float(d["pct"]),
                                     "invested": _r2(d["invested"])} for d in a.deposits[:SNAPSHOT_PAYCHECKS]]}
    try:
        activity = nessie.recent_activity(a.customer_id, limit=SNAPSHOT_ACTIVITY)
    except Exception:  # noqa: BLE001  the bank being down must not break chat
        activity = []
    facts["bank_activity"] = [{"on": x["on"], "text": x["text"], "amount": _r2(x["amount"]), "flow": x["flow"],
                               "account": x["account"]} for x in activity]
    facts["bank"] = {"checking": _r2(profile.checking_balance), "savings": _r2(profile.cash_cushion),
                     "card_owed": _r2(profile.card_debt)}
    if not p:
        facts["fund"] = None
        return facts
    vals = values(a.customer_id, a.today)
    total = sum(vals.values())
    targets = {h.ticker: h.weight for h in p.holdings}
    tickers = sorted(set(vals) | set(targets), key=lambda t: (-vals.get(t, 0.0), -targets.get(t, 0.0), t))
    rows = [{"ticker": t, "label": market.info(t)["label"], "dollars": _r2(vals.get(t, 0.0)),
             "pct": round(vals.get(t, 0.0) / total * 100, 1) if total else 0.0,
             "target_pct": round(targets.get(t, 0.0) * 100, 1)} for t in tickers]
    tier = tier_for(total)
    every = tier.review_every_days
    preps = bool(a.auto_trade and tier.auto_trades)
    from core.fund import AUTO_EXECUTE_TIER
    facts["fund"] = {
        "value": _r2(total), "put_in": _r2(a.contributed), "gain": _r2(total - a.contributed),
        "risk_label": p.risk_label, "risk_level": p.risk_level, "risk_level_max": RISK_LEVEL_MAX,
        "risk_score": p.score if p.score is not None else a.risk_score, "risk_score_max": RISK_SCORE_MAX,
        "yearly_fee_pct": round(p.annual_fee_pct, 2),
        "mode": "custom" if a.answers.get("custom_mode") else "spending" if a.answers.get("spending_mode") else "standard",
        "interests": [THEME_LABELS.get(t, t) for t in intake.interests(a.answers)],
        "single_companies": intake.stocks(a.answers),
        "holdings": rows[:SNAPSHOT_HOLDINGS], "more_holdings": max(0, len(rows) - SNAPSHOT_HOLDINGS),
    }
    facts["review"] = {
        "tier": tier.tier, "tier_range": tier.balance_range, "frequency": tier.check_frequency, "every_days": every,
        "last": a.last_review.isoformat() if a.last_review else None,
        "next": (a.last_review + timedelta(days=every)).isoformat() if a.last_review and preps else None,
        "auto_trade": bool(a.auto_trade), "auto_prep_unlocked": bool(tier.auto_trades),
        "auto_executes": bool(preps and tier.tier >= AUTO_EXECUTE_TIER), "pending_trades": len(a.pending),
    }
    facts["recent_events"] = list(a.log[:SNAPSHOT_EVENTS])
    return facts


def _m(v: float) -> str:
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def account_snapshot(session: Session, facts: dict | None = None) -> str:
    """account_facts as compact text for the system prompt. Formatting only; no arithmetic here."""
    f = facts or account_facts(session)
    L = [f"Customer: {f['name']}. Demo date {f['as_of']}. Prices: {f['prices']}."]
    if f["goal"]:
        L.append(f"Goal (their words): \"{f['goal']}\".")
    if f["horizon"]:
        L.append(f"When they'll need the money: {f['horizon']}.")
    fd = f.get("fund")
    if fd:
        L.append(f"Fund value {_m(fd['value'])}; they put in {_m(fd['put_in'])}; gain {_m(fd['gain'])}. "
                 f"{fd['risk_label']} (level {fd['risk_level']} of {fd['risk_level_max']}, risk score {fd['risk_score']} of "
                 f"{fd['risk_score_max']}). "
                 f"Yearly fee {fd['yearly_fee_pct']:.2f}%. Fund type: {fd['mode']}.")
        L.append("Interests: " + (", ".join(fd["interests"]) or "none (broad fund)") + ". Single companies: "
                 + (", ".join(fd["single_companies"]) or "none") + ".")
        L.append("Holdings (now % / target %, dollars):")
        L += [f"- {h['ticker']} {h['label']}: {h['pct']:.1f}% / {h['target_pct']:.1f}%, {_m(h['dollars'])}"
              for h in fd["holdings"]]
        if fd["more_holdings"]:
            L.append(f"- and {fd['more_holdings']} more (get_fund has all).")
    else:
        L.append("No fund yet: the intake isn't finished.")
    pc = f["paycheck"]
    L.append(f"Paycheck {_m(pc['amount'])}; invests {pc['invest_pct']:g}% of each one. Next paycheck "
             f"{pc['next_on']}, investing {_m(pc['next_invests'])}.")
    if pc["recent"]:
        L.append("Recent paychecks: " + "; ".join(f"{d['on']} {_m(d['paycheck'])}, invested {_m(d['invested'])} "
                                                   f"({d['pct']:g}%)" for d in pc["recent"]) + ".")
    b = f["bank"]
    L.append(f"Bank (mock Capital One / Nessie): checking {_m(b['checking'])}, savings {_m(b['savings'])}, "
             f"card owed {_m(b['card_owed'])}.")
    if f["bank_activity"]:
        L.append("Recent bank activity (newest first):")
        L += [f"- {x['on']} {x['account']} {x['flow']} {_m(x['amount'])}: {x['text']}" for x in f["bank_activity"]]
    rv = f.get("review")
    if rv:
        auto = ("on; review trades run without a Confirm click" if rv["auto_executes"] else
                "on; reviews line up trades for them to confirm" if rv["auto_trade"] and rv["auto_prep_unlocked"] else
                "on, but trade prep unlocks at a higher balance" if rv["auto_trade"] else "off")
        nxt = rv["next"] or ("with the next paycheck" if rv["auto_trade"] and rv["auto_prep_unlocked"] else
                             "none scheduled (automatic trade prep is off; the report is always available)")
        L.append(f"Tier {rv['tier']} ({rv['tier_range']}): reviews {rv['frequency'].lower()} (every {rv['every_days']} "
                 f"days). Last review: {rv['last'] or 'none yet'}. Next review: {nxt}. "
                 f"Automatic trades: {auto}. Trades waiting for Confirm: {rv['pending_trades']}.")
    if f.get("recent_events"):
        L.append("Recent events: " + " | ".join(f["recent_events"]))
    pr = f["preferences"]
    L.append(f"Notifications: weekly report {'on' if pr['weekly_report'] else 'off'}, trade confirmations "
             f"{'on' if pr['trade_confirmations'] else 'off'}, big-move alerts "
             f"{'on at ' + str(pr['big_move_pct']) + '%' if pr['big_move_alerts'] else 'off'}. Watchlist: "
             + (", ".join(pr["watchlist"]) or "empty") + ".")
    text = "\n".join(L)
    if len(text) > SNAPSHOT_MAX_CHARS:
        text = text[:text.rfind("\n", 0, SNAPSHOT_MAX_CHARS)] + "\n(snapshot cut; use the tools for the rest)"
    return text


def news_tickers(session: Session) -> list[str]:
    """The customer's single companies and holdings, then the biggest companies inside their funds."""
    a = session.fund
    if not a.portfolio:
        return ["SPY"]
    rep = build_report(a)
    return list(dict.fromkeys(intake.stocks(a.answers) + [c["ticker"] for c in rep["companies"][:4]]
                              + [h.ticker for h in a.portfolio.holdings if h.role != "core"]))[:6]


def headlines(tickers: list[str] | None, limit: int = 12) -> list[dict]:
    return [{"title": n["title"], "link": n["link"], "source": n["source"], "tickers": n["tickers"],
             "published": n["published"].isoformat() if n["published"] else None} for n in feed.news(tickers, limit)]


def run_tool(session: Session, name: str, args: dict) -> tuple[dict, list[str]]:
    """Returns (result for Claude, UI panels to refresh)."""
    if not session.fund.portfolio:
        return {"error": "No fund yet; the customer is still answering the intake questions."}, []
    if name == "get_fund":
        return fund_summary(session), []
    if name == "get_fund_explanation":
        return explain_for_chat(session.fund.portfolio, args.get("ticker") or None), []
    if name == "get_report":
        return report_summary(session), []
    if name == "update_interests":
        current = intake.interests(session.fund.answers)
        themes = [t for t in current if t not in args.get("remove", [])] + \
                 [t for t in args.get("add", []) if t in THEMES and t not in current]
        fund.set_interests(session.fund, themes)
        return fund_summary(session), ["fund", "account"]
    if name == "get_market_today":
        return market_today(session), []
    if name == "get_news":
        items = headlines(None if args.get("market") else (args.get("tickers") or news_tickers(session)))
        return {"headlines": items} if items else {"headlines": [], "note": "News isn't reachable right now."}, []
    if name in ("add_stock", "remove_stock"):
        t = str(args.get("ticker", "")).upper().strip()
        current = intake.stocks(session.fund.answers)
        if name == "add_stock":
            if t in ETFS or not market.known(t):
                return {"error": f"Can't add {t}: it's a FirstFund fund already or we have no prices for it."}, []
            fund.set_stocks(session.fund, current + [t])
        else:
            fund.set_stocks(session.fund, [x for x in current if x != t])
        return fund_summary(session), ["fund", "account"]
    if name == "set_paycheck_percent":
        fund.set_invest_pct(session.fund, args["percent"])
        return {"paycheck_percent": session.fund.invest_pct}, ["account"]
    if name == "get_custom_fund_preview":
        from core.intake import risk_level as _rl
        level = _rl(session.fund.risk_score or 50) if session.fund.risk_score is not None else 2
        ints = intake.interests(session.fund.answers)
        profile = get_profile(session.fund.customer_id)
        spend_suggestions = spending.spending_stock_suggestions(session.fund.customer_id, profile)
        preview = custom_fund_preview(level, ints, spending_stocks=None)
        current_mode = "custom" if session.fund.answers.get("custom_mode") else "etf"
        return _plain({
            "current_mode": current_mode,
            "interest_stocks": preview,
            "spending_suggestions": spend_suggestions,
            "note": (f"Your {len(ints)} interest(s) would become {len(preview)} individual stocks. "
                     "Call switch_to_custom_fund to apply, or add spending_stocks tickers from the suggestions."),
        }), []
    if name == "switch_to_custom_fund":
        tickers = [t.upper().strip() for t in args.get("spending_stocks", []) if t.strip()]
        session.fund.answers["custom_mode"] = True
        session.fund.answers["spending_stocks"] = tickers
        fund.build(session.fund)
        n = len(session.fund.portfolio.holdings)
        session.fund.log.insert(0, f"Switched to custom fund: {n} individual stocks.")
        return {**fund_summary(session), "mode": "custom", "holdings_count": n}, ["fund", "account"]
    if name == "switch_to_etf_fund":
        session.fund.answers.pop("custom_mode", None)
        session.fund.answers.pop("spending_stocks", None)
        session.fund.answers.pop("spending_mode", None)
        fund.build(session.fund)   # back to the answer-driven fund (core/builder.py)
        session.fund.log.insert(0, "Rebuilt your fund from your answers.")
        return {**fund_summary(session), "mode": "etf"}, ["fund", "account"]
    if name == "get_spending_insights":
        profile = get_profile(session.fund.customer_id)
        breakdown = spending.spending_breakdown(session.fund.customer_id, profile)
        risk_adj = spending.banking_risk_adjustment(profile, session.fund.risk_score or 50)
        # Convert raw risk_score to level for context
        from core.intake import risk_level as _rl
        current_level = _rl(session.fund.risk_score or 50) if session.fund.risk_score is not None else None
        return _plain({
            "spending": breakdown,
            "risk_adjustment": {**risk_adj, "intake_level": current_level},
        }), []
    if name == "toggle_auto_trade":
        from core.policy import tier_for
        from core.fund import AUTO_EXECUTE_TIER
        enabled = bool(args["enabled"])
        bal = fund.value(session.fund)
        tier = tier_for(bal)
        if enabled and not tier.auto_trades:   # same rule as the Portfolio tab's switch (POST /p/{cid}/auto)
            return {"auto_trade": session.fund.auto_trade, "tier": tier.tier, "balance_range": tier.balance_range,
                    "error": "Automatic trade prep unlocks at a higher balance (Tier 2, $5K+). Nothing changed."}, []
        session.fund.auto_trade = enabled
        auto_executes = enabled and tier.tier >= AUTO_EXECUTE_TIER
        return {
            "auto_trade": enabled,
            "tier": tier.tier,
            "balance_range": tier.balance_range,
            "auto_executes_immediately": auto_executes,
            "note": (
                f"Trades will execute automatically on each {tier.check_frequency.lower()} review."
                if auto_executes else
                f"Trades will be queued for your confirmation (auto-execute kicks in at Tier {AUTO_EXECUTE_TIER}, "
                f"${50_000:,}+ AUM; you're currently Tier {tier.tier})."
            ),
        }, ["account"]
    return {"error": f"unknown tool {name}"}, []
