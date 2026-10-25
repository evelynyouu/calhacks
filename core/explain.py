""""How your fund was built": the page's content, from the builder's reasons plus fixed templates.  OWNER: Evelyn.

Every number here is computed in Python from the Portfolio and the answers; the templates only lay it
out, so the explanation can't drift from what the engine did. Claude may add a short personal intro
tied to the goal (agent/claude_agent.fund_intro), from facts this module computes.
"""
from __future__ import annotations

from core import intake, market
from core.fund import FundAccount, value
from core.models import Portfolio
from core.policy import tier_for
from core.portfolio import (BOND_MAX, BOND_MIN, BOND_STEP, CONCENTRATION_NOTE, LEVEL_BAND, MIN_TILT,
                            PER_INTEREST_CAP, RISK_LABELS, STOCK_CAP_BY_RISK, STOCK_EACH, SWING_REF,
                            TILT_CAP_BY_RISK, US_SHARE, lookthrough, swing)
from core.profile import get_profile
from core.rebalance import DRIFT_ABS, DRIFT_REL
from core.report import CONCENTRATION_TRIGGER
from core.sim import simulate

EXAMPLE_BALANCE = 1_000.0   # dollars shown when the fund is still empty
# Reasons listed under "What we adjusted, and why" (the rest explain a holding or the score)
ADJUST_KINDS = ("contradiction", "horizon", "drop", "override", "swing", "cap", "dropped", "overlap",
                "concentration", "sector")
ANSWER_KEYS = ("goal", "risk_priority", "risk_range", "max_drop", "horizon")


def build(acct: FundAccount) -> dict:
    p, a = acct.portfolio, acct.answers
    held = value(acct)
    balance = held or float(a.get("starting_amount") or 0) or EXAMPLE_BALANCE
    example = not (held or a.get("starting_amount"))
    bonds = next((h.weight for h in p.holdings if h.ticker == "BND"), 0.0)
    one_year = simulate(p, balance, 0, years=1)
    lowered = [r for r in p.reasons if r.kind in ("horizon", "drop") and r.facts.get("lowered")]
    return {
        "balance": balance, "example_balance": example,
        "answers": _answers(a),
        "score": {"answers": acct.risk_score, "used": p.score, "label": p.risk_label,
                  "texts": [r.text for r in p.reasons if r.kind in ("score", "contradiction")]},
        "mix": {"bonds": bonds, "stocks": 1 - bonds, "holdings": len(p.holdings)},
        "holdings": [{"ticker": h.ticker, "name": h.name, "weight": h.weight, "role": h.role,
                      "texts": [r.text for r in p.reasons if r.ticker == h.ticker and r.kind not in ADJUST_KINDS]}
                     for h in p.holdings],
        "expect": {"low": one_year.p10[1], "high": one_year.p90[1], "worst_year": p.worst_year,
                   "worst_pct": p.worst_year_return, "worst_dollars": round(balance * p.worst_year_return / 100, 2)},
        "adjusted": [r.text for r in p.reasons if r.kind in ADJUST_KINDS
                     and (r.kind not in ("horizon", "drop") or r.facts.get("lowered") or r.facts.get("warn"))],
        "limits_lowered": bool(lowered),
        "change": _changes(acct),
        "full": _full(acct, p, balance, bonds),
    }


def intro_facts(acct: FundAccount) -> list[str]:
    """What Claude may use for the 2-3 sentence intro: no number it would have to compute."""
    p, a = acct.portfolio, acct.answers
    bonds = next((h.weight for h in p.holdings if h.ticker == "BND"), 0.0)
    labels = dict(intake.BY_KEY["interests"].options)
    themes = [labels.get(t, t) for t in intake.interests(a)]
    return [f"Goal: {intake.goal_label(a)}.",
            f"Mix: {p.risk_label}, {bonds:.0%} bonds and {1 - bonds:.0%} stocks.",
            f"Interests: {', '.join(themes)}." if themes else "Interests: none, the fund stays broad.",
            f"When they need the money: {intake.answer_label('horizon', a) or 'not answered'}."]


def fallback_intro(acct: FundAccount) -> str:
    goal = intake.goal_label(acct.answers)
    lead = f"You're investing for “{goal}”." if goal else "Here's your fund."
    return f"{lead} Below is how we turned your answers into this mix, and why each piece is there."


def explain_for_chat(p: Portfolio, ticker: str | None = None) -> dict:
    """The get_fund_explanation tool: the same reasons the page shows, optionally for one holding."""
    reasons = [r for r in p.reasons if ticker is None or r.ticker == ticker.upper()]
    return {"risk_label": p.risk_label, "score_used": p.score,
            "reasons": [{"kind": r.kind, "ticker": r.ticker, "text": r.text} for r in reasons],
            "note": "Look-through and the worst-year figures for interest funds are approximate."}


def _answers(a: dict) -> list[dict]:
    rows = [{"q": intake.BY_KEY[k].prompt, "a": intake.answer_label(k, a)} for k in ANSWER_KEYS if k in a]
    themes = intake.interests(a)
    if "interests" in a:
        labels = dict(intake.BY_KEY["interests"].options)
        rows.append({"q": intake.BY_KEY["interests"].prompt,
                     "a": ", ".join(labels.get(t, t) for t in themes) or "None, keep it broad"})
    if intake.stocks(a):
        rows.append({"q": "Single companies you added", "a": ", ".join(intake.stocks(a))})
    return rows


def _changes(acct: FundAccount) -> list[str]:
    out = ["Add or drop industries (Account tab or chat), or single companies (Markets tab or chat). Targets "
           "change now; nothing is sold until you confirm a rebalance.",
           f"Change how much of each paycheck goes in, now {acct.invest_pct:g}% (Account tab or chat).",
           "Retaking the questions (Account tab) clears your simulated fund and chat and starts over; the new fund "
           "is built from your new answers."]
    if acct.portfolio.pointed_level or acct.answers.get("override"):
        out.append("Use the mix your answers pointed to instead of the one within your limits (fund card).")
    return out


def _full(acct: FundAccount, p: Portfolio, balance: float, bonds: float) -> list[dict]:
    a, level = acct.answers, p.risk_level
    parts = intake.risk_parts(a)
    tier = tier_for(balance)
    paycheck = get_profile(acct.customer_id).paycheck_amount
    per_check = paycheck * acct.invest_pct / 100
    lt = lookthrough(p.holdings)
    top = sorted(lt["companies"].items(), key=lambda cw: -cw[1])[:5]
    pref = [f"“{intake.answer_label(k, a)}” counts as {parts[k] * 100:.0f} out of 100"
            for k in ("risk_priority", "risk_range") if k in parts]
    score_text = (f"Your two preference answers are scored from 0 (most careful) to 100 (most adventurous): "
                  f"{' and '.join(pref)}. Their average is your risk score, {acct.risk_score}. "
                  if pref else "")
    tilts = [h for h in p.holdings if h.role == "tilt"]
    stocks = [h for h in p.holdings if h.role == "stock"]
    return [
        {"title": "Why index funds", "paras": [
            "An index fund buys every company in a market (or a slice of it) at once, so one company's bad news "
            "barely moves it, and the yearly fee stays low because nobody is paid to pick stocks.",
            f"Your fund is built from {len(p.holdings) - len(stocks)} of them"
            + (" plus the single companies you added." if stocks else ".")]},
        {"title": "US and international stocks", "paras": [
            f"The stock part is split about {US_SHARE:.0%} US (VTI) and {1 - US_SHARE:.0%} international (VXUS), "
            "roughly how the world's stock markets divide. Owning both means one country's slump isn't all of your fund."]},
        {"title": "What bonds do", "paras": [
            f"{bonds:.0%} of your fund is in bonds (BND): loans to governments and companies that pay interest. "
            "They usually hold steady when stocks fall, so they soften drops; the cost is slower growth.",
            "They don't always help: in 2022 bonds fell too, which is why even the steadiest mix lost money that year."]},
        {"title": "How your score sets the mix", "paras": [
            score_text + f"The bond share follows the score: {BOND_MAX:.0%} at 0 down to {BOND_MIN:.0%} at 100, "
            f"in {BOND_STEP * 100:.0f}-point steps. The level name changes every {LEVEL_BAND} points "
            f"({', '.join(RISK_LABELS.values())}).",
            *(r.text for r in p.reasons if r.kind in ("contradiction", "horizon", "drop", "override"))]},
        {"title": "Interests and single companies", "paras": [
            f"Each interest gets up to {PER_INTEREST_CAP:.0%} of the fund, less if its fund swings more than "
            f"{SWING_REF:.0%} a year, and never under {MIN_TILT:.0%}. A {p.risk_label.lower()} fund keeps all "
            f"interests together to {TILT_CAP_BY_RISK[level]:.0%}.",
            *(f"{h.ticker} moves about {swing(h.ticker):.0%} in a typical year; it's {h.weight:.1%} of your fund."
              for h in tilts),
            f"Single companies get {STOCK_EACH:.0%} each, {STOCK_CAP_BY_RISK[level]:.0%} in total at this level: "
            "one company can sink on its own news, so each stays small."]},
        {"title": "What you really own (look-through)", "paras": [
            "Your funds hold many of the same companies. Counting through each fund, the biggest are:",
            *(f"{market.name(c)} ({c}): about {w:.1%} of your fund" for c, w in top),
            f"We flag any company over {CONCENTRATION_NOTE:.0%}. This is approximate: we only see each fund's "
            "biggest holdings."]},
        {"title": "What it costs", "paras": [
            f"The funds charge {p.annual_fee_pct:.2f}% a year together, about ${p.annual_fee_dollars(balance):,.2f} "
            f"a year on ${balance:,.0f}. It comes out of the funds' value; you never get a bill."]},
        {"title": "How each paycheck invests", "paras": [
            f"{acct.invest_pct:g}% of each paycheck (about ${per_check:,.0f} of ${paycheck:,.0f}) is invested the "
            "moment it lands. It buys whatever is furthest below its target share, so new money nudges the fund back "
            "on target without selling anything."]},
        {"title": "When the fund is rebalanced", "paras": [
            f"Your report checks the fund every {tier.review_every_days} days at your balance. If a holding drifts "
            f"{DRIFT_ABS * 100:.0f} points from its target"
            + f" (or {DRIFT_REL:.0%} of its target, if that's smaller)"
            + f", or one company passes {CONCENTRATION_TRIGGER:.0%} "
            "of the fund, it suggests trades. Nothing happens until you confirm."]},
        {"title": "What's simulated or approximate", "paras": [
            "Worst years for VTI, VXUS and BND are their real 2008 and 2022 returns (VXUS's 2008 is its index "
            "twin, VGTSX). For interest funds and single companies we estimate them from how much each moves "
            "with the market.",
            "The typical one-year range comes from FirstFund's simulator, which uses assumed returns, not real "
            "price history.",
            "Look-through counts only each fund's biggest holdings, and prices after the last real close are "
            "simulated."]},
    ]
