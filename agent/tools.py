"""Claude tools. Each wraps a Python function; Claude never computes numbers.  OWNER: Ethan."""
from __future__ import annotations

from app.state import Session
from core.models import to_dict
from core.policy import tier_for
from core.portfolio import build_portfolio, risk_from_answers
from core.profile import get_profile
from core.readiness import check_readiness
from core.sim import simulate

TOOLS = [
    {
        "name": "get_financial_snapshot",
        "description": "The user's income, bills, spending, cash cushion, debt, and invested balance from their bank.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_readiness",
        "description": "Whether the user should invest yet ('not_yet', 'start_small', 'ready'), a safe monthly amount, and reasons.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "build_fund",
        "description": "Build the user's personalized fund from their intake answers. Call once all five answers are known.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "timeline_years": {"type": "number"},
                "age": {"type": "integer"},
                "interests": {"type": "array", "items": {"type": "string", "enum": ["tech", "healthcare", "clean_energy"]}},
                "loss_reaction": {"type": "string", "enum": ["sell", "wait", "buy_more"]},
            },
            "required": ["goal", "timeline_years", "age", "interests", "loss_reaction"],
        },
    },
    {
        "name": "run_projection",
        "description": "10-year outcome range in dollars for the current fund at the safe monthly amount.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_tier",
        "description": "The user's account tier by balance, how often FirstFund checks, and what it does at that tier.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def run_tool(session: Session, name: str, args: dict) -> tuple[dict, list[str]]:
    """Returns (result for Claude, UI events to refresh)."""
    cid = session.customer_id
    if name == "get_financial_snapshot":
        p = get_profile(cid)
        d = to_dict(p)
        d.update(monthly_surplus=p.monthly_surplus, cushion_months=round(p.cushion_months, 1))
        return d, []
    if name == "check_readiness":
        return to_dict(check_readiness(get_profile(cid))), ["readiness"]
    if name == "build_fund":
        if check_readiness(get_profile(cid)).status == "not_yet":
            return {"error": "User is not ready to invest. Explain the readiness result instead."}, []
        session.intake = args
        level = risk_from_answers(args["timeline_years"], args["age"], args["loss_reaction"])
        session.portfolio = build_portfolio(level, args.get("interests", []))
        return to_dict(session.portfolio), ["fund"]
    if name == "run_projection":
        if not session.portfolio:
            return {"error": "No fund yet. Call build_fund first."}, []
        p = get_profile(cid)
        r = check_readiness(p)
        s = simulate(session.portfolio, p.invested_balance, r.safe_monthly_amount)
        return {"year_10": {"low": s.p10[-1], "typical": s.p50[-1], "high": s.p90[-1]},
                "total_contributed": s.total_contributed, "monthly": s.monthly_contribution}, ["fund"]
    if name == "get_tier":
        return to_dict(tier_for(get_profile(cid).invested_balance)), []
    return {"error": f"unknown tool {name}"}, []
