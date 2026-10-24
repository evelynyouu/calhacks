"""Payday auto-invest.  OWNER: Evelyn (flow) + Saahil (allocation, broker).

Rule: invest only what's left after every bill due before the next paycheck, spending until
then, and a checking buffer. Round down to $5 and cap at the planned amount. Savings is never
touched. safety_check is pure; run_payday does the deposits, transfers and trades.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal

from core import broker, nessie, rebalance
from core.models import Bill, PaydayResult, Portfolio
from core.profile import get_profile
from core.readiness import check_readiness

CHECKING_BUFFER = 200.0   # checking we never invest
DAYS_PER_MONTH = 30       # prorating convention for spending
STEP = 5.0                # invest in $5 steps

PaydayReason = Literal["full", "reduced_for_bills", "skipped"]


@dataclass
class PaydayDecision:
    invest: float
    planned: float
    held_back: float
    bills_due: list[Bill]
    spend_reserved: float
    buffer: float
    available: float          # checking left after bills, spending and buffer (may be negative)
    reason: PaydayReason


def safety_check(checking_after_deposit: float, planned: float, days_to_payday: int,
                 upcoming_bills: Iterable[Bill], monthly_spending: float) -> PaydayDecision:
    """How much of `planned` is safe to invest now. Bills due today or on the next payday count."""
    if days_to_payday <= 0:
        raise ValueError(f"days_to_payday must be positive, got {days_to_payday}")

    due = sorted((b for b in upcoming_bills if 0 <= b.due_in_days <= days_to_payday), key=lambda b: b.due_in_days)
    spend = round(monthly_spending * days_to_payday / DAYS_PER_MONTH, 2)
    available = round(checking_after_deposit - sum(b.amount for b in due) - spend - CHECKING_BUFFER, 2)

    planned = max(0.0, planned)
    invest = min(planned, math.floor(max(0.0, available) / STEP) * STEP)
    if invest <= 0:
        reason = "skipped"
    elif invest >= planned:
        reason = "full"
    else:
        reason = "reduced_for_bills"
    return PaydayDecision(round(invest, 2), round(planned, 2), round(planned - invest, 2), due,
                          spend, CHECKING_BUFFER, available, reason)


def run_payday(customer_id: str, portfolio: Portfolio | None) -> PaydayResult:
    paycheck = nessie.deposit_paycheck(customer_id)
    p = get_profile(customer_id)
    r = check_readiness(p)
    planned = 0.0 if r.status == "not_yet" else round(r.safe_monthly_amount / 2, 2)   # two paychecks a month

    d = safety_check(p.checking_balance, planned, p.days_to_payday, p.upcoming_bills, p.monthly_spending)
    reasons = [f"Paycheck of {_money(paycheck)} detected."]
    if r.status == "not_yet":
        reasons.append("Investing is paused until the card is paid off.")
    elif planned <= 0:
        reasons.append("Nothing is planned for investing this paycheck.")
    else:
        for b in d.bills_due:
            reasons.append(f"{b.name} ({_money(b.amount)}) is due in {b.due_in_days} days, so it's set aside first.")
        reasons.append(f"{_money(d.spend_reserved)} is set aside for spending over the {p.days_to_payday} days "
                       f"until the next paycheck, and {_money(d.buffer)} stays in checking as a buffer.")
        if d.reason == "full":
            reasons.append(f"Bills are covered, so the full {_money(planned)} was invested.")
        elif d.reason == "reduced_for_bills":
            reasons.append(f"Invested {_money(d.invest)} instead of the usual {_money(planned)}; "
                           f"the other {_money(d.held_back)} stays in checking.")
        else:
            reasons.append(f"Skipped investing this paycheck: nothing is left after bills, spending and the buffer. "
                           f"Investing picks up again next paycheck.")

    trades, xfer = [], None
    if d.invest > 0 and portfolio:
        xfer = nessie.transfer_to_investing(customer_id, d.invest)
        for t, amt in rebalance.allocate_new_money(portfolio, broker.get_positions(customer_id), d.invest).items():
            trades.append(broker.buy_notional(t, amt))
    return PaydayResult(paycheck, planned, d.invest, xfer, trades, reasons)


def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"
