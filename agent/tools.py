"""Claude tools. Each wraps a Python function; Claude never computes numbers.  OWNER: Ethan."""
from __future__ import annotations

from app.state import Session
from core import feed, fund, intake, market
from core.market import ETFS
from core.portfolio import THEMES
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
        "name": "get_report",
        "description": "The latest report: value, money put in, gain, 30-day performance vs the market, each "
                       "holding's performance, the biggest companies inside the fund, the customer's interests vs "
                       "the market, broad market trends, and recommendations.",
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
]


def fund_summary(session: Session) -> dict:
    a = session.fund
    p = a.portfolio
    return {
        "risk_label": p.risk_label, "risk_level": p.risk_level, "risk_score_out_of_100": a.risk_score,
        "paycheck_percent": a.invest_pct, "goal": a.answers.get("goal"),
        "interests": [THEMES[t] + " " + ETFS[THEMES[t]]["label"] for t in intake.interests(a.answers)],
        "interests_without_a_fund": a.answers.get("interests", {}).get("unmatched", []),
        "single_companies": intake.stocks(a.answers),
        "holdings": [{"ticker": h.ticker, "what": market.info(h.ticker)["about"], "target_pct": round(h.weight * 100, 1),
                      "role": h.role} for h in p.holdings],
        "yearly_fee_pct": p.annual_fee_pct, "overlap_notes": p.overlap_warnings,
    }


def report_summary(session: Session) -> dict:
    rep = build_report(session.fund)
    return {
        "as_of": rep["as_of"].isoformat(), "summary": rep["summary"],
        "holdings": [{k: h[k] for k in ("ticker", "label", "value", "weight", "target", "day_pct", "period_pct", "gain_pct")}
                     for h in rep["holdings"]],
        "top_companies": [{k: c[k] for k in ("name", "ticker", "share", "via", "period_pct")} for c in rep["companies"][:5]],
        "interests": rep["interests"], "market": {k: v for k, v in rep["market"].items()},
        "fund_vs_market": rep["relation"],
        "recommendations": [{"title": r["title"], "facts": r["facts"], "trades": r["trades"]} for r in rep["recommendations"]],
        "simulated_market_data": True,
    }


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
    return {"error": f"unknown tool {name}"}, []
