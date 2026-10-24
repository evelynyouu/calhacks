"""Payday auto-invest.  OWNER: Evelyn (flow) + Saahil (allocation, broker).

STUB logic is close to the plan: never invest before upcoming bills are covered.
"""
from __future__ import annotations

from core import broker, nessie, rebalance
from core.models import PaydayResult, Portfolio
from core.profile import get_profile
from core.readiness import check_readiness

CHECKING_BUFFER = 500.0   # GUESS: Lance to finalize
BILL_WINDOW_DAYS = 7


def run_payday(customer_id: str, portfolio: Portfolio | None) -> PaydayResult:
    paycheck = nessie.deposit_paycheck(customer_id)
    p = get_profile(customer_id)
    r = check_readiness(p)
    planned = round(r.safe_monthly_amount / 2, 2)   # two paychecks a month

    due = [b for b in p.upcoming_bills if b.due_in_days <= BILL_WINDOW_DAYS]
    due_total = sum(b.amount for b in due)
    free = p.checking_balance - due_total - CHECKING_BUFFER
    invest = round(max(0.0, min(planned, free)), 2)

    reasons = [f"Paycheck of ${paycheck:,.0f} detected."]
    for b in due:
        reasons.append(f"{b.name} (${b.amount:,.0f}) is due in {b.due_in_days} days, so it's set aside first.")
    if r.status == "not_yet":
        invest = 0.0
        reasons.append("Investing is paused until the card is paid off.")
    elif invest < planned:
        reasons.append(f"Invested ${invest:,.0f} instead of the usual ${planned:,.0f} to keep a ${CHECKING_BUFFER:,.0f} buffer.")
    else:
        reasons.append(f"Bills are covered, so the full ${planned:,.0f} was invested.")

    trades, xfer = [], None
    if invest > 0 and portfolio:
        xfer = nessie.transfer_to_investing(customer_id, invest)
        for t, d in rebalance.allocate_new_money(portfolio, broker.get_positions(customer_id), invest).items():
            trades.append(broker.buy_notional(t, d))
    return PaydayResult(paycheck, planned, invest, xfer, trades, reasons)
