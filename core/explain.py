""""How your fund was built": the page's content, from the builder's reasons plus fixed templates.  OWNER: Evelyn.

Every number here is computed in Python from the Portfolio and the answers; the templates only lay it
out, so the explanation can't drift from what the engine did. Claude may add a short personal intro
tied to the goal (agent/claude_agent.fund_intro), from facts this module computes.
"""
from __future__ import annotations

from core import intake, market
from core.fund import FundAccount, value
from core.market import COMPANIES, ETFS
from core.models import SLICES, Portfolio
from core.policy import tier_for
from core.portfolio import (BOND_MAX, BOND_MIN, BOND_STEP, CONCENTRATION_NOTE, CORE_NAME_CAP, INTL_SHARE, LEVEL_BAND,
                            MIN_PICK, MIN_TILT, PER_INTEREST_CAP, RISK_LABELS, SPEND_CAP_BY_RISK, SPEND_PICKS,
                            STOCK_CAP_BY_RISK, STOCK_EACH, SWING_REF, THEME_LABELS, THEME_PICKS, THEMES,
                            TILT_CAP_BY_RISK, US_SHARE, lookthrough, share, swing)
from core.universe import core_companies
from core.profile import get_profile
from core.rebalance import DRIFT_ABS, DRIFT_REL
from core.report import CONCENTRATION_TRIGGER
from core.sim import simulate

EXAMPLE_BALANCE = 1_000.0   # dollars shown when the fund is still empty
# Reasons listed under "What we adjusted, and why" (the rest explain a holding or the score)
ADJUST_KINDS = ("contradiction", "horizon", "drop", "override", "swing", "cap", "dropped", "overlap",
                "concentration", "sector")
ANSWER_KEYS = ("goal", "risk_priority", "risk_range", "max_drop", "horizon")
DATA_DATE = "2026-10-24"   # when company sizes were read (docs/data_sources.md)


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
        "slices": _slices(p),
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


# Which reasons explain a holding's part in each slice ("combined" goes with every part of a shared company)
SLICE_REASONS = {"bonds": ("bonds",), "intl": ("core",), "us_core": ("core",), "spend": ("spend",),
                 "interest": ("tilt",), "added": ("stock",)}


def _slices(p: Portfolio) -> list[dict]:
    """The fund's parts in SLICES order. Each lists every company in it with that slice's own part (a company held
    for two reasons appears under both), so a slice's holdings add up to its share. "position" is the whole holding."""
    weights = p.slice_weights()
    position = {h.ticker: h for h in p.holdings}
    out = []
    for k, label in SLICES:
        part = p.parts.get(k) if p.parts else {h.ticker: h.weight for h in p.by_slice()[k]}
        order = sorted(part, key=lambda t: -part[t])
        out.append({"key": k, "label": label, "weight": weights[k], "holdings": [
            {"ticker": t, "name": position[t].name, "weight": part[t], "position": position[t].weight,
             "sector": position[t].sector,
             "texts": [r.text for r in p.reasons if r.ticker == t and r.kind in SLICE_REASONS[k] + ("combined",)]}
            for t in order if t in position]})
    return out


def slice_facts(p: Portfolio) -> list[str]:
    """The fund in one line per slice (Claude's facts and the offline message): exact shares, names not 60 rows."""
    w, parts = p.slice_weights(), p.parts
    def names(k, n=4):
        top = sorted(parts.get(k, {}).items(), key=lambda tw: -tw[1])[:n]
        return ", ".join(f"{market.name(t)} ({t})" for t, _ in top)
    if not parts:   # a portfolio built outside the custom-index builder
        return [f"Holdings: {', '.join(f'{h.ticker} {share(h.weight)}' for h in p.holdings)}."]
    out = [f"Bonds: {share(w['bonds'])} (BND, a fund).", f"International stocks: {share(w['intl'])} (VXUS, a fund)."]
    if w["us_core"]:
        out.append(f"Your US index: {share(w['us_core'])}, {len(parts.get('us_core', {}))} companies weighted by size; "
                   f"the biggest are {names('us_core', 3)}.")
    if w["spend"]:
        out.append(f"Where you spend: {share(w['spend'])}, in {names('spend')}, from the customer's purchases in the "
                   f"last 90 days. This personalizes the fund; it isn't a prediction those companies will do well.")
    elif any(r.kind == "spend" and r.facts.get("empty") for r in p.reasons):
        out.append("Where you spend: no purchases found in the last 90 days, so there's no such slice.")
    if w["interest"]:
        out.append(f"Your interests: {share(w['interest'])}, in {names('interest')}.")
    if w["added"]:
        out.append(f"Companies you added: {share(w['added'])}, {names('added')}.")
    return out


def intro_facts(acct: FundAccount) -> list[str]:
    """What Claude may use for the 2-3 sentence intro: no number it would have to compute."""
    p, a = acct.portfolio, acct.answers
    bonds = next((h.weight for h in p.holdings if h.ticker == "BND"), 0.0)
    labels = dict(intake.BY_KEY["interests"].options)
    themes = [labels.get(t, t) for t in intake.interests(a)]
    return [f"Goal: {a.get('goal', '')}.",
            f"Mix: {p.risk_label}, {bonds:.0%} bonds and {1 - bonds:.0%} stocks.",
            f"Interests: {', '.join(themes)}." if themes else "Interests: none, the fund stays broad.",
            f"When they need the money: {intake.answer_label('horizon', a) or 'not answered'}."]


def fallback_intro(acct: FundAccount) -> str:
    goal = (acct.answers.get("goal") or "").strip()
    lead = f"You're investing for “{goal}”." if goal else "Here's your fund."
    return f"{lead} Below is how we turned your answers into this mix, and why each piece is there."


def explain_for_chat(p: Portfolio, ticker: str | None = None) -> dict:
    """The get_fund_explanation tool: the same reasons the page shows, optionally for one holding."""
    reasons = [r for r in p.reasons if ticker is None or r.ticker == ticker.upper()]
    held = {h.ticker: h for h in p.holdings}
    out = {"risk_label": p.risk_label, "score_used": p.score,
           "reasons": [{"kind": r.kind, "ticker": r.ticker, "text": r.text} for r in reasons],
            "note": "Look-through and the worst-year figures for single companies and interest funds are approximate."}
    if ticker and ticker.upper() in held:
        h = held[ticker.upper()]
        out.update(slice=h.slice, sector=h.sector, weight=h.weight)
    elif ticker:
        out["note"] = f"{ticker.upper()} isn't in the fund. " + out["note"]
    return out


def _theme_share(p: Portfolio, theme: str) -> float:
    """One interest's exact share of the fund: its part of each company (or its theme fund)."""
    def theme_of(t):
        return ETFS[t]["theme"] if t in ETFS else ETFS[COMPANIES[t]["etf"]]["theme"]
    return sum(w for t, w in p.parts.get("interest", {}).items() if theme_of(t) == theme)


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
    return [
        {"title": "Why your own index", "paras": [
            f"Instead of one fund that holds thousands of US companies, your US stocks are {len(core_companies())} "
            "companies you own directly: the biggest in every sector, weighted by size the way an index fund is, "
            f"none over {CORE_NAME_CAP:.0%} of that part. You get most of the market's spread, you can see every "
            "company you own, and the fund can lean toward your life.",
            "The tradeoff: it skips thousands of smaller companies, so it follows the US market closely but not "
            "exactly."]},
        {"title": "Why some parts stay funds", "paras": [
            "Bonds (BND) stay a fund: thousands of small loans are how a bond fund steadies things, and you can't "
            "spread money that thin buying bonds one at a time.",
            f"International stocks (VXUS, {INTL_SHARE:.0%} of your stock part) stay a fund too: only a handful of "
            "foreign companies trade on US exchanges, so a fund is the only way to own thousands of them.",
            *(f"{h.name} ({h.ticker}) stays a fund because we don't hold single companies for that interest."
              for h in p.by_slice()["interest"] if h.ticker in ETFS)]},
        {"title": "Where you spend", "paras": [
            f"Up to {SPEND_CAP_BY_RISK[level]:.0%} of a {p.risk_label.lower()} fund goes to companies you spend money "
            f"with in the last 90 days, by dollars: the top {SPEND_PICKS}, none under {MIN_PICK:.1%}. A local shop or "
            "a private chain counts toward the biggest public companies in its category. Bills and one-off "
            "purchases don't count.",
            *(r.text for r in p.reasons if r.kind == "spend"),
            "This personalizes your fund; it isn't a prediction that those companies will do well."]},
        {"title": "US and international stocks", "paras": [
            f"The stock part is split about {US_SHARE:.0%} US (your own index and your picks) and "
            f"{INTL_SHARE:.0%} international (VXUS), roughly how the world's stock markets divide. Owning both "
            "means one country's slump isn't all of your fund."]},
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
            f"Each interest becomes its {THEME_PICKS} biggest companies we hold, by size, or its theme fund if we "
            "hold none. How much it gets follows how much that theme's fund swings:",
            *(f"{THEME_LABELS[th]}: its fund ({THEMES[th]}) moves about {swing(THEMES[th]):.0%} in a typical year; "
              f"the interest is {_theme_share(p, th):.1%} of your fund."
              for th in intake.interests(a) if th in THEMES and _theme_share(p, th) > 0),
            f"Single companies get {STOCK_EACH:.0%} each, {STOCK_CAP_BY_RISK[level]:.0%} in total at this level: "
            "one company can sink on its own news, so each stays small."]},
        {"title": "What you really own (look-through)", "paras": [
            "Counting the companies you own directly and what's inside your funds, the biggest are:",
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
            "Worst years for VXUS and BND are their real 2008 and 2022 returns (VXUS's 2008 is its index twin, "
            "VGTSX). For single companies and interest funds we estimate them from how much each moves with the "
            "market (its beta) times the US market's real return.",
            f"Company sizes (market caps) are approximate, from one day ({DATA_DATE}); see docs/data_sources.md.",
            "The typical one-year range comes from FirstFund's simulator, which uses assumed returns, not real "
            "price history.",
            "Look-through counts only each fund's biggest holdings, and prices after the last real close are "
            "simulated."]},
    ]
