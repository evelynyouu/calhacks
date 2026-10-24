"""Readiness check.  RULES: Lance.

Order matters: no surplus -> high-interest debt -> cushion size.
Every "not yet" or "start small" ends with what gets the user to ready, and roughly when.
"""
from __future__ import annotations

from core.models import FinancialProfile, Readiness

HIGH_INTEREST_APR = 10.0
# Share of what's left after bills and spending that goes to investing once ready.
# A quarter keeps most of the surplus free for months where spending runs over the
# estimate; the team settled on this so Sam's payday reads "$50 of $150".
SAFE_SHARE_OF_SURPLUS = 0.25
# High-interest debt is a guaranteed return, so paying it off gets a bigger share.
DEBT_SHARE_OF_SURPLUS = 0.5
HABIT_AMOUNT = 25.0
MIN_CUSHION_MONTHS = 1.0
TARGET_CUSHION_MONTHS = 3.0
_MAX_PLAN_MONTHS = 600   # stop projecting after 50 years


def check_readiness(p: FinancialProfile) -> Readiness:
    months = p.cushion_months
    surplus = max(0.0, p.monthly_surplus)
    full = round(surplus * SAFE_SHARE_OF_SURPLUS, 0)

    if surplus <= 0:
        gap = p.monthly_expenses - p.monthly_income
        next_step = (f"Freeing up ${gap:,.0f}/month just breaks even; anything past that can start working for you."
                     if gap > 0 else "Even a small cut in spending gives you room to start.")
        return Readiness(
            status="not_yet",
            safe_monthly_amount=0.0,
            headline="Not yet: your spending covers your whole paycheck",
            reasons=[
                f"You bring in ${p.monthly_income:,.0f} a month and spend about ${p.monthly_expenses:,.0f}.",
                "Investing only makes sense with money left over after bills and spending.",
                next_step,
            ],
        )

    if p.card_debt > 0 and p.card_apr >= HIGH_INTEREST_APR:
        payment = round(surplus * DEBT_SHARE_OF_SURPLUS, 0)
        payoff = _months_to_pay_off(p.card_debt, p.card_apr, payment)
        return Readiness(
            status="not_yet",
            safe_monthly_amount=0.0,
            headline=f"Not yet: pay off your ${p.card_debt:,.0f} card first",
            reasons=[
                f"Your card charges about {p.card_apr:.0f}% a year.",
                f"Paying it off is a guaranteed {p.card_apr:.0f}% return; no fund reliably beats that.",
                f"At ${payment:,.0f}/month it's gone in about {_duration(payoff)}, interest included. "
                "Then you can start investing.",
            ],
        )

    amount = safe_amount(full, months)
    if months < TARGET_CUSHION_MONTHS:
        reasons = [f"Your cushion covers {months:.1f} months of expenses; {TARGET_CUSHION_MONTHS:.0f} is the target."]
        if months < MIN_CUSHION_MONTHS:
            headline = f"Start with ${amount:,.0f}/month while you build a cushion"
            reasons.append(f"${amount:,.0f}/month builds the habit without touching your safety net.")
        else:
            headline = f"Start small: ${amount:,.0f}/month, the rest to your cushion"
            reasons.append("Your monthly amount grows as your cushion does.")
        to_target = _months_to_target(p, surplus, full)
        if to_target is not None:
            reasons.append(f"Save the rest of your ${surplus:,.0f} monthly surplus and you'll be fully ready "
                           f"in about {_duration(to_target)}, investing ${full:,.0f}/month.")
        return Readiness(status="start_small", safe_monthly_amount=amount, headline=headline, reasons=reasons)

    return Readiness(
        status="ready",
        safe_monthly_amount=amount,
        headline=f"Ready: ${amount:,.0f}/month is a safe amount",
        reasons=[
            f"Your cushion covers {months:.1f} months of expenses.",
            "No high-interest debt.",
            f"That's {SAFE_SHARE_OF_SURPLUS:.0%} of your ${surplus:,.0f} monthly surplus.",
        ],
    )


def safe_amount(full: float, cushion_months: float) -> float:
    """Monthly investing amount: the habit amount under 1 month of cushion, ramping
    smoothly up to the full share at 3 months, so there's no cliff at either line.
    Never more than the full share."""
    if cushion_months >= TARGET_CUSHION_MONTHS:
        return full
    progress = max(0.0, (cushion_months - MIN_CUSHION_MONTHS) / (TARGET_CUSHION_MONTHS - MIN_CUSHION_MONTHS))
    return round(min(full, max(HABIT_AMOUNT, full * progress)), 0)


def _months_to_pay_off(debt: float, apr: float, payment: float) -> int | None:
    rate = apr / 100 / 12
    for month in range(1, _MAX_PLAN_MONTHS + 1):
        debt = debt * (1 + rate) - payment
        if debt <= 0:
            return month
    return None


def _months_to_target(p: FinancialProfile, surplus: float, full: float) -> int | None:
    """Months until the cushion reaches the target if everything not invested is saved."""
    if p.monthly_expenses <= 0:
        return None
    cushion, target = p.cash_cushion, TARGET_CUSHION_MONTHS * p.monthly_expenses
    for month in range(1, _MAX_PLAN_MONTHS + 1):
        cushion += surplus - safe_amount(full, cushion / p.monthly_expenses)
        if cushion >= target:
            return month
    return None


def _duration(months: int | None) -> str:
    if months is None:
        return "a very long time at this pace"
    if months <= 18:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{months / 12:.1f} years"
