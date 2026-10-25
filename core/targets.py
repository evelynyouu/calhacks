"""Targets that follow the customer, not the market.  OWNER: Lance.

A fund's targets (its mix, and the percent of each paycheck that goes in) change only when the customer's
situation changes, never because prices moved:

- Income or spending shift (from the bank): a paycheck raise or cut, or spending well above or below its usual
  level for two months in a row -> a new paycheck percent, keeping the share of spare money they chose.
- The goal getting closer (glide path): inside the last GLIDE_WINDOW_YEARS before the goal date, the bond
  share steps up on a published schedule (GLIDE_PATH), by at most GLIDE_MAX_STEP per GLIDE_STEP_DAYS.
- Risk answers changed (Account tab): the targets are rebuilt with the fund builder (core/builder.build).

Every change is a proposal ("Your mix moves from X to Y because Z", before/after weights, Accept / Not now).
Nothing changes until the customer accepts; an accepted change is recorded as a Reason (core/models.py) and
shown on "How your fund was built" and in the report. Drift from market moves is NOT handled here: new
paychecks steer to underweight holdings and the report's rebalance trades still need Confirm (core/rebalance.py,
core/report.py, core/policy.py).

Nothing here reads a price. The inputs are the answers, the dates, the accepted glide step and the bank
profile/feed, so a market move can't create a proposal (tests/test_targets.py checks that).

Glide path, custom and spending-mode funds: the glide floor is applied only to the default, answer-built fund
(core/builder.py).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from core import intake, nessie
from core.models import Portfolio, Reason
from core.portfolio import BOND_MAX, BOND_MIN, build_portfolio, level_for_score, share, warnings_from
from core.profile import PAY_PERIOD_DAYS, get_profile

# ---------- Goal date ----------

# The intake stores when the money is needed as a bucket, not a date. We assume the middle of each bucket,
# counted from the day the fund starts (or the day the customer changes the answer). "15 years or more" has
# no top, so we assume 20 years: far enough out that the glide path starts no sooner than 13 years in.
# "Not sure yet" has no date and no glide path.
HORIZON_YEARS = {"lt3": 1.5, "3to7": 5.0, "7to15": 11.0, "15plus": 20.0}
DAYS_PER_YEAR = 365.25

# ---------- Glide path ----------

# The bond share starts rising only in the last 7 years: the horizon limits in core/builder.py already keep
# money needed within 7 years at Moderate or steadier, and US stocks took about 5 years to recover from 2008.
GLIDE_WINDOW_YEARS = 7
# The published schedule: the least bonds the fund should hold with this many whole years left (rounded up).
# 30% seven years out, rising 5 points a year to 65% (the builder's most careful mix, BOND_MAX) at the goal.
GLIDE_PATH = {7: 0.30, 6: 0.35, 5: 0.40, 4: 0.45, 3: 0.50, 2: 0.55, 1: 0.60, 0: 0.65}
# One step is the bond share's own grid (core/portfolio.BOND_STEP), at most once a year, so the mix never
# lurches: a fund far below the schedule (an override, say) catches up gradually.
GLIDE_MAX_STEP = 0.05
GLIDE_STEP_DAYS = 365

# ---------- Income and spending ----------

# A paycheck that moved this much (up or down) from the one the percent was chosen against is a real change
# in pay, not rounding or a one-off; the demo's "Got a raise" button is 10%.
INCOME_SHIFT = 0.05
# Spending this far above or below its usual monthly level, in each of the last SPENDING_MONTHS months, is a
# new normal rather than one big purchase (a single $600 repair moves one month, not two).
SPENDING_SHIFT = 0.20
SPENDING_MONTHS = 2
MONTH_DAYS = 30
# Suggestions smaller than this many points aren't worth a card.
PCT_MIN_CHANGE = 1.0

KIND_TITLES = {"glide": "Your goal is getting closer", "income": "Your pay changed",
               "spending": "Your spending changed", "answers": "You changed your answers"}
_ANSWER_KEYS = ("risk_priority", "risk_range", "max_drop", "horizon")


# ---------- Setup and the glide floor (hooks from core/fund.py) ----------

def begin(acct, on: date) -> None:
    """Called when the fund starts: the goal date and the bank situation the paycheck percent was chosen in."""
    acct.started = on
    acct.target_date = goal_date(acct.answers.get("horizon"), on)
    acct.baseline = situation(acct)


def goal_date(horizon: str | None, start: date) -> date | None:
    years = HORIZON_YEARS.get(horizon)
    return start + timedelta(days=round(years * DAYS_PER_YEAR)) if years is not None else None


def years_left(acct, on: date | None = None) -> float | None:
    if not getattr(acct, "target_date", None):
        return None
    return (acct.target_date - (on or acct.today)).days / DAYS_PER_YEAR


def glide_floor(years: float | None) -> float | None:
    """The schedule's bond share for this many years left; None outside the window (or with no goal date)."""
    if years is None or years > GLIDE_WINDOW_YEARS:
        return None
    return GLIDE_PATH[max(0, math.ceil(years))]


def bond_share(p: Portfolio | None) -> float:
    return next((h.weight for h in p.holdings if h.ticker == "BND"), 0.0) if p else 0.0


def with_glide(p: Portfolio, answers: dict, floor: float | None, years: float | None = None) -> Portfolio:
    """The fund rebuilt with at least `floor` in bonds (the accepted glide step). The level used for the interest
    and company caps is the one that bond share belongs to, so the slices always fit in the smaller stock part."""
    before = bond_share(p)
    if floor is None or floor <= before + 1e-9:
        return p
    score = round((BOND_MAX - floor) / (BOND_MAX - BOND_MIN) * 100)
    level = min(p.risk_level, level_for_score(score))
    q = build_portfolio(level, intake.interests(answers), intake.stocks(answers), bond_share=floor, score=score)
    keep = [r for r in p.reasons if r.kind in ("score", "contradiction", "horizon", "drop", "override")]
    when = f"about {years:.0f} year{'s' if round(years) != 1 else ''} away" if years is not None else "getting closer"
    glide = Reason("glide", f"Your goal date is {when}, so the glide path you accepted raised bonds from "
                            f"{share(before)} to {share(floor)}: money needed sooner has less time to recover "
                            f"from a drop.", "BND", {"before": before, "after": floor, "years_left": years})
    q.reasons = keep + q.reasons + [glide]
    q.overlap_warnings = warnings_from(q.reasons)
    q.pointed_level = p.pointed_level
    return q


def apply_glide(acct, p: Portfolio) -> Portfolio:
    """core/fund.build calls this on the default fund, so a rebuild (new interests, an override) keeps the
    accepted glide step."""
    return with_glide(p, acct.answers, getattr(acct, "glide_bonds", None), years_left(acct))


# ---------- The bank situation ----------

def situation(acct) -> dict:
    """What the paycheck percent was chosen against: pay, how often it comes, bills, usual spending, the percent."""
    prof = get_profile(acct.customer_id)
    per_month = prof.monthly_income / prof.paycheck_amount if prof.paycheck_amount else 2.0
    return {"paycheck": prof.paycheck_amount, "per_month": round(per_month, 4), "bills": prof.monthly_bills,
            "spending": prof.monthly_spending, "pct": acct.invest_pct}


def recent_spending(cid: str, months: int = SPENDING_MONTHS) -> list[float]:
    """Day-to-day spending (and one-off purchases) per month in each of the last `months` "months", newest first,
    from the bank feed. Bills and moves to investing aren't spending. The demo clock books spending in one lump
    per pay period, so each "month" is a whole number of pay periods (28 days for every-two-weeks pay), ending at
    the bank's own latest date, and is scaled to 30 days."""
    feed = nessie.recent_activity(cid, limit=None)
    if not feed:
        return []
    period = int(nessie.persona_seed(cid).get("pay_period_days", PAY_PERIOD_DAYS))
    window = period * max(1, round(MONTH_DAYS / period))
    end = max(date.fromisoformat(x["on"]) for x in feed)
    out = []
    for m in range(months):
        hi, lo = end - timedelta(days=m * window), end - timedelta(days=(m + 1) * window)
        spent = sum(x["amount"] for x in feed if x["tag"] in ("spend", "event") and x["flow"] == "out"
                    and x["account"] != "Investing" and lo < date.fromisoformat(x["on"]) <= hi)
        out.append(round(spent * MONTH_DAYS / window, 2))
    return out


def spare(sit: dict) -> float:
    """Money left from one paycheck after its share of the month's bills and spending."""
    return sit["paycheck"] - (sit["bills"] + sit["spending"]) / (sit["per_month"] or 2.0)


def suggest_pct(base: dict, now: dict) -> float | None:
    """The paycheck percent that keeps the share of spare money the customer invested in `base`, in the `now`
    situation. None when the customer was investing with nothing spare to begin with (a share of spare money
    means nothing then); 0 when nothing is spare now (checking would run short; the customer can still say no)."""
    if spare(base) <= 0 or now["paycheck"] <= 0:
        return None
    if spare(now) <= 0:
        return 0.0
    of_spare = base["paycheck"] * base["pct"] / 100 / spare(base)
    return float(max(0, min(100, round(100 * of_spare * spare(now) / now["paycheck"]))))


# ---------- Proposals ----------

def review(acct) -> dict | None:
    """Re-evaluate the targets (each paycheck, and on demand). Leaves a pending proposal alone; at most one card
    at a time, in this order: the glide path, then pay, then spending."""
    if not acct.portfolio or acct.proposal:
        return acct.proposal
    if not acct.baseline or acct.baseline.get("pct") != acct.invest_pct:
        acct.baseline = situation(acct)   # the customer moved the percent themselves: that's the new starting point
    for make in (_glide_proposal, _income_proposal, _spending_proposal):
        prop = make(acct)
        if prop and acct.snoozed.get(prop["kind"]) != prop["signature"]:
            acct.proposal = prop
            acct.log.insert(0, f"Suggested a change: {prop['title']}.")
            return prop
    return None


def propose_answers(acct, changes: dict) -> dict | None:
    """The customer changed risk answers (or the horizon): rebuild with the fund builder and propose it.
    None when nothing changed. Replaces any pending proposal (the customer just asked for this one)."""
    from core.builder import build as build_fund
    changes = {k: v for k, v in changes.items() if k in _ANSWER_KEYS and acct.answers.get(k) != v}
    if not changes:
        return None
    answers = {**acct.answers, **changes}
    target = goal_date(answers.get("horizon"), acct.today) if "horizon" in changes else acct.target_date
    years = (target - acct.today).days / DAYS_PER_YEAR if target else None
    glide = acct.glide_bonds if "horizon" not in changes else None   # a new goal date starts a new glide path
    from core.fund import _spending
    after = with_glide(build_fund(answers, spending=_spending(acct.customer_id)), answers, glide, years)
    names = ", ".join(f"{_ANSWER_NAMES[k]} to “{intake.answer_label(k, answers)}”" for k in changes)
    because = f"you changed {names}"
    if "horizon" in changes:
        because += (f" (we count your goal date as {target:%B %Y}, the middle of that range)" if target
                    else " (with no date, there's no glide path)")
    acct.proposal = _mix_proposal(acct, "answers", after, because,
                                  {"answers": changes, "target_date": target, "glide_bonds": glide},
                                  signature=sorted(changes.items()))
    acct.log.insert(0, f"Suggested a change: {acct.proposal['title']}.")
    return acct.proposal


_ANSWER_NAMES = {"risk_priority": "what matters most", "risk_range": "the $1,000 range",
                 "max_drop": "the drop you could live with", "horizon": "when you'll need the money"}


def _glide_proposal(acct) -> dict | None:
    years = years_left(acct)
    floor = glide_floor(years)
    now = bond_share(acct.portfolio)
    if floor is None or floor <= now + 1e-9 or acct.answers.get("custom_mode") or acct.answers.get("spending_mode"):
        return None
    since = (acct.today - (acct.glide_on or acct.started or acct.today)).days
    if since < GLIDE_STEP_DAYS:
        return None   # at most one step a year
    new = round(min(floor, now + GLIDE_MAX_STEP), 2)
    after = with_glide(acct.portfolio, acct.answers, new, years)
    because = (f"your goal date ({acct.target_date:%B %Y}) is about {max(0, years):.0f} year"
               f"{'s' if round(max(0, years)) != 1 else ''} away. Inside the last {GLIDE_WINDOW_YEARS} years the "
               f"schedule calls for at least {share(floor)} in bonds; we move {GLIDE_MAX_STEP * 100:.0f} points a year at most, "
               f"so this step is {share(now)} to {share(new)}")
    return _mix_proposal(acct, "glide", after, because, {"glide_bonds": new},
                         signature=[new, since // GLIDE_STEP_DAYS, str(acct.target_date)])


def _income_proposal(acct) -> dict | None:
    base, now = acct.baseline, situation(acct)
    if not base["paycheck"] or abs(now["paycheck"] / base["paycheck"] - 1) < INCOME_SHIFT:
        return None
    change = now["paycheck"] / base["paycheck"] - 1
    because = (f"your paychecks went {'up' if change > 0 else 'down'} {abs(change):.0%}, from "
               f"{_money(base['paycheck'])} to {_money(now['paycheck'])}")
    return _pct_proposal(acct, "income", base, now, because, signature=now["paycheck"])


def _spending_proposal(acct) -> dict | None:
    base = acct.baseline
    usual = base["spending"]
    months = recent_spending(acct.customer_id)
    if usual <= 0 or len(months) < SPENDING_MONTHS:
        return None
    up = all(m >= usual * (1 + SPENDING_SHIFT) for m in months)
    down = all(m <= usual * (1 - SPENDING_SHIFT) for m in months)
    if not (up or down):
        return None
    avg = round(sum(months) / len(months), 2)
    now = {**situation(acct), "paycheck": base["paycheck"], "per_month": base["per_month"], "spending": avg}
    because = (f"your spending has been {'above' if up else 'below'} its usual {_money(usual)} a month for "
               f"{SPENDING_MONTHS} months (about {_money(avg)} a month lately)")
    return _pct_proposal(acct, "spending", base, now, because, signature=round(avg, -1))


def _pct_proposal(acct, kind: str, base: dict, now: dict, because: str, signature) -> dict | None:
    pct = suggest_pct(base, now)
    if pct is None or abs(pct - acct.invest_pct) < PCT_MIN_CHANGE:
        return None
    of_spare = base["paycheck"] * base["pct"] / 100 / spare(base)
    per_now, per_new = math.floor(now["paycheck"] * acct.invest_pct / 100), math.floor(now["paycheck"] * pct / 100)
    if spare(now) <= 0:
        how = (f"At that rate nothing is left of a paycheck after bills and spending, so pausing keeps checking from "
               f"running short. It's your call: keeping {acct.invest_pct:g}% invests {_money(per_now)} a paycheck.")
    else:
        how = (f"That keeps {of_spare:.0%} of what's left after bills and spending going into your fund: "
               f"{_money(per_new)} of each {_money(now['paycheck'])} paycheck instead of {_money(per_now)}.")
    text = f"Your paycheck share moves from {acct.invest_pct:g}% to {pct:g}% because {because}. {how}"
    return {"kind": kind, "heading": KIND_TITLES[kind], "title": f"Your paycheck share moves from "
            f"{acct.invest_pct:g}% to {pct:g}%", "because": because, "detail": how, "text": text, "rows": [],
            "pct": {"before": acct.invest_pct, "after": pct, "per_check_before": float(per_now),
                    "per_check_after": float(per_new), "paycheck": now["paycheck"]},
            "apply": {"pct": pct, "baseline": {**now, "pct": pct}}, "on": acct.today, "signature": signature}


def _mix_proposal(acct, kind: str, after: Portfolio, because: str, apply: dict, signature) -> dict:
    p = acct.portfolio
    rows = weight_rows(p, after)
    if p.risk_label != after.risk_label:
        title = f"Your mix moves from {p.risk_label} to {after.risk_label}"
    else:
        title = f"Your mix moves from {share(bond_share(p))} to {share(bond_share(after))} bonds"
    if not any(abs(r["before"] - r["after"]) > 1e-9 for r in rows):
        title = f"Your mix stays {p.risk_label}"
    text = f"{title} because {because}."
    return {"kind": kind, "heading": KIND_TITLES[kind], "title": title, "because": because, "detail": "",
            "text": text, "rows": rows, "pct": None, "bonds": {"before": bond_share(p), "after": bond_share(after)},
            "labels": {"before": p.risk_label, "after": after.risk_label},
            "apply": apply, "on": acct.today, "signature": signature}


def weight_rows(before: Portfolio, after: Portfolio) -> list[dict]:
    """Every holding in either mix, with its share before and after (0 when it isn't held)."""
    b = {h.ticker: h for h in before.holdings}
    a = {h.ticker: h for h in after.holdings}
    order = list(b) + [t for t in a if t not in b]
    return [{"ticker": t, "name": (a.get(t) or b.get(t)).name, "before": b[t].weight if t in b else 0.0,
             "after": a[t].weight if t in a else 0.0} for t in order]


# ---------- Accept / Not now ----------

def accept(acct) -> dict | None:
    """Apply the pending proposal and record why. New paychecks steer toward the new mix; nothing is sold
    unless the customer confirms a rebalance."""
    from core import fund
    prop = acct.proposal
    if not prop:
        return None
    before = acct.portfolio
    apply = prop["apply"]
    if prop["kind"] == "glide":
        acct.glide_bonds, acct.glide_on = apply["glide_bonds"], acct.today
        fund.build(acct)
    elif prop["kind"] == "answers":
        acct.answers.update(apply["answers"])
        acct.target_date, acct.glide_bonds = apply["target_date"], apply["glide_bonds"]
        if "horizon" in apply["answers"]:
            acct.glide_on = acct.today
        fund.build(acct)
    else:
        fund.set_invest_pct(acct, apply["pct"])
        acct.baseline = dict(apply["baseline"])
    entry = record(acct, prop["kind"], prop["text"], before, acct.portfolio, prop.get("pct"))
    acct.proposal = None
    acct.snoozed.pop(prop["kind"], None)
    acct.log.insert(0, f"You accepted: {prop['title']}.")
    return entry


def dismiss(acct) -> None:
    """Not now: the fund, the percent and the answers stay exactly as they are. The same suggestion isn't shown
    again until the situation changes further (a new pay amount, a new glide year, other answers)."""
    prop = acct.proposal
    if not prop:
        return
    acct.snoozed[prop["kind"]] = prop["signature"]
    acct.proposal = None
    acct.log.insert(0, f"Not now: {prop['title']}. Nothing changed.")


def record(acct, kind: str, text: str, before: Portfolio | None, after: Portfolio | None,
           pct: dict | None = None) -> dict:
    """Add an accepted change to the history (newest first). Also used for the fund card's override switch,
    which the customer clicks directly (that click is the acceptance)."""
    facts = {"rows": weight_rows(before, after) if before and after else [], "pct": pct,
             "bonds_before": bond_share(before), "bonds_after": bond_share(after)}
    entry = {"on": acct.today, "reason": Reason(kind, text, facts=facts)}
    acct.target_history.insert(0, entry)
    return entry


def status(acct) -> dict:
    """The quiet line under the card when nothing is proposed: the goal date and where the glide path stands."""
    years = years_left(acct)
    floor = glide_floor(years)
    starts = (acct.target_date - timedelta(days=round(GLIDE_WINDOW_YEARS * DAYS_PER_YEAR))
              if acct.target_date else None)
    return {"target_date": acct.target_date, "years_left": years, "glide_floor": floor, "glide_starts": starts,
            "window": GLIDE_WINDOW_YEARS,
            "bonds": bond_share(acct.portfolio), "assumed": acct.answers.get("horizon") in HORIZON_YEARS,
            "horizon_label": intake.answer_label("horizon", acct.answers) if acct.answers.get("horizon") else ""}


def history(acct) -> list[dict]:
    """Accepted changes for the "How your fund was built" page and the report, newest first."""
    out = []
    for e in acct.target_history:
        r = e["reason"]
        out.append({"on": e["on"], "kind": r.kind, "text": r.text,
                    "rows": [x for x in r.facts["rows"] if abs(x["before"] - x["after"]) > 1e-9],
                    "pct": r.facts["pct"]})
    return out


def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"
