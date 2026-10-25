"""Intake answers -> the customer's fund, with a reason for every choice.  OWNER: Evelyn.

The two preference answers set a risk score (core/intake.py). Two limits can lower it: when the money
is needed (HORIZON_MAX_LEVEL) and the biggest one-year drop the customer can live with (we use the
riskiest mix whose real worst year, 2008 or 2022, stayed within it). If the answers disagree a lot we
say so and lean careful. The customer can override the limits (answers["override"]); we advise, they
decide. core/portfolio.py turns the score into weights.
"""
from __future__ import annotations

from core import intake
from core.models import Portfolio, Reason
from core.portfolio import RISK_LABELS, bond_share_for, build_portfolio, level_for_score, warnings_from

# The riskiest level for each horizon. Money needed in under 3 years can't wait out a crash
# (US stocks took about 5 years to recover from 2008), so it stays conservative.
HORIZON_MAX_LEVEL = {"lt3": 1, "3to7": 2, "7to15": 3, "15plus": 4, "unsure": None}
CONTRADICTION_SPREAD = 0.6    # answers this far apart on the 0..1 scale get a note
_PART_NAMES = {"risk_priority": "what matters most", "risk_range": "the $1,000 range you'd pick",
               "max_drop": "the drop you could live with"}


def build(answers: dict) -> Portfolio:
    themes, stocks = intake.interests(answers), intake.stocks(answers)
    reasons: list[Reason] = []
    score = intake.risk_score(answers)
    reasons.append(_score_reason(answers, score))
    score = _lean_careful(answers, score, reasons)
    pointed = level_for_score(score)

    ceiling, why = score, []
    cap = HORIZON_MAX_LEVEL.get(answers.get("horizon"))
    if cap is not None and level_for_score(ceiling) > cap:
        ceiling = cap * 25 - 1 if cap < 4 else 100
        why.append("horizon")
    max_drop = answers.get("max_drop")
    if max_drop is not None:
        fit = _fits_drop(ceiling, max_drop, themes, stocks)
        if fit < ceiling:
            ceiling = fit
            why.append("drop")

    override = bool(answers.get("override")) and ceiling < score
    used = score if override else ceiling
    p = build_portfolio(level_for_score(used), themes, stocks, bond_share=bond_share_for(used), score=used)
    reasons += _horizon_reason(answers, cap, "horizon" in why and not override)
    reasons += _drop_reason(answers, p, score, "drop" in why and not override)
    if override:
        built = build_portfolio(level_for_score(ceiling), themes, stocks, bond_share=bond_share_for(ceiling))
        reasons.append(Reason("override", f"You chose the {RISK_LABELS[p.risk_level].lower()} mix your answers "
                                          f"pointed to, over our suggestion of {RISK_LABELS[built.risk_level].lower()} "
                                          f"({_limits(why)}). That's your call; you can switch back any time.",
                              facts={"chosen_level": p.risk_level, "suggested_level": built.risk_level}))
    elif level_for_score(ceiling) < pointed:
        p.pointed_level = pointed
    p.reasons = reasons + p.reasons
    p.overlap_warnings = warnings_from(p.reasons)
    return p


def _fits_drop(start: int, max_drop: float, themes, stocks) -> int:
    """The highest score <= start whose fund's real worst year is within max_drop; 0 if none is."""
    seen = {}
    for s in range(start, -1, -1):
        key = (bond_share_for(s), level_for_score(s))
        if key not in seen:
            seen[key] = build_portfolio(key[1], themes, stocks, bond_share=key[0]).worst_year_return
        if seen[key] >= -max_drop:
            return s
    return 0


def _score_reason(answers: dict, score: int) -> Reason:
    a, b = intake.answer_label("risk_priority", answers), intake.answer_label("risk_range", answers)
    return Reason("score", f"You said “{a}” matters most and picked the range “{b}”. Together that's a risk "
                           f"score of {score} out of 100 (0 is the most careful, 100 the most adventurous).",
                  facts={"score": score})


def _lean_careful(answers: dict, score: int, reasons: list[Reason]) -> int:
    """If two risk answers are far apart, name them and lean toward the careful one: a drop bigger than
    someone expected is the most common reason people sell at the bottom."""
    parts = intake.risk_parts(answers)
    if len(parts) < 2:
        return score
    lo, hi = min(parts, key=parts.get), max(parts, key=parts.get)
    if parts[hi] - parts[lo] < CONTRADICTION_SPREAD:
        return score
    prefs = {k: v for k, v in parts.items() if k != "max_drop"}
    if lo == "max_drop" or not prefs:
        leaned, how = score, "Your drop answer already works as a limit, so the fund stays within it."
    else:
        leaned = round(100 * (sum(prefs.values()) / len(prefs) + min(prefs.values())) / 2)
        how = f"We leaned toward the careful one, so your score is {leaned} instead of {score}."
    reasons.append(Reason("contradiction",
                          f"Two of your answers pull in different directions: for {_PART_NAMES[lo]} you said "
                          f"“{intake.answer_label(lo, answers)}”, but for {_PART_NAMES[hi]} you said "
                          f"“{intake.answer_label(hi, answers)}”. {how} A drop bigger than you expected is the most "
                          f"common reason people sell at the bottom. You can change either answer.",
                          facts={"careful": lo, "bold": hi, "score_before": score, "score_after": leaned}))
    return leaned


def _horizon_reason(answers: dict, cap: int | None, lowered: bool) -> list[Reason]:
    h = answers.get("horizon")
    if h is None:
        return []
    when = intake.answer_label("horizon", answers).lower()
    if cap is None:
        text = "You're not sure yet when you'll need the money, so it doesn't limit the mix."
    elif lowered:
        text = (f"You'll need most of this money {when.removeprefix('in ')}, so we kept the fund at "
                f"{RISK_LABELS[cap].lower()} or steadier: a big drop right before you need the money might not "
                f"have time to recover.")
    else:
        text = f"You'll need most of this money {when.removeprefix('in ')}, which leaves time to ride out drops."
    return [Reason("horizon", text, facts={"horizon": h, "max_level": cap, "lowered": lowered})]


def _drop_reason(answers: dict, p: Portfolio, score: int, lowered: bool) -> list[Reason]:
    d = answers.get("max_drop")
    if d is None:
        return []
    within = p.worst_year_return >= -d
    worst = f"{p.worst_year}, {abs(p.worst_year_return):g}%"
    if not within:
        text = (f"You said you could live with a {d:g}% drop in a year. No mix of these funds stayed within that in "
                f"both 2008 and 2022 (in 2022 bonds fell too), so we built the steadiest one. Its worst year was "
                f"{worst}.")
    elif lowered:
        text = (f"You said you could live with a {d:g}% drop in a year, so we used the riskiest mix that never fell "
                f"further than that in 2008 or 2022. Its worst year was {worst}.")
    else:
        text = f"This mix's worst real year was {worst}, within the {d:g}% drop you said you could live with."
    return [Reason("drop", text, facts={"max_drop": d, "worst_year": p.worst_year,
                                        "worst_return": p.worst_year_return, "lowered": lowered, "warn": not within})]


def _limits(why: list[str]) -> str:
    names = {"horizon": "when you'll need the money", "drop": "the drop you said you could live with"}
    return "because of " + " and ".join(names[w] for w in why)
