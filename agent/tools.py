"""Claude tools. Each wraps a Python function; Claude never computes numbers.  OWNER: Ethan."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime

from app.state import Session
from core import feed, fund, intake, market, spending
from core.portfolio import build_custom_portfolio, custom_fund_preview
from core.market import ETFS
from core.portfolio import THEMES
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
        session.fund.auto_trade = enabled
        bal = fund.value(session.fund)
        tier = tier_for(bal)
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
