"""Payday safety check: how much of the planned contribution is safe to invest this paycheck.

safety_check is pure (no Nessie, no clock). fast_forward_to_payday wires in the side effects.
Rule: invest only what's left after bills due by the next payday, prorated spending until then,
and the checking buffer; floor to $5 and cap at planned. Savings is never touched.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable

from core.models import PaydayDecision, UpcomingBill
from core.profile import CHECKING_BUFFER, DAYS_PER_MONTH

STEP = 5.0  # invest in $5 increments


def safety_check(
    checking_after_deposit: float,
    planned: float,
    as_of: date,
    next_payday: date,
    upcoming_bills: Iterable[UpcomingBill],
    monthly_spending: float,
) -> PaydayDecision:
    if next_payday <= as_of:
        raise ValueError(f"next_payday {next_payday} must be after as_of {as_of}")

    due = sorted((b for b in upcoming_bills if as_of <= b.due_date <= next_payday), key=lambda b: b.due_date)
    bills_total = sum(b.amount for b in due)
    spend = round(monthly_spending * (next_payday - as_of).days / DAYS_PER_MONTH, 2)
    available = round(checking_after_deposit - bills_total - spend - CHECKING_BUFFER, 2)

    planned = max(0.0, planned)
    invest = min(planned, math.floor(max(0.0, available) / STEP) * STEP)
    if invest <= 0:
        reason = "skipped"
    elif invest >= planned:
        reason = "full"
    else:
        reason = "reduced_for_bills"

    return PaydayDecision(
        invest=round(invest, 2),
        planned=round(planned, 2),
        held_back=round(planned - invest, 2),
        bills_due=due,
        spend_reserved=spend,
        buffer=CHECKING_BUFFER,
        available=available,
        reason=reason,
    )
