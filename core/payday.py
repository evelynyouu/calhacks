"""Payday auto-invest.  OWNER: Evelyn (flow) + Saahil (allocation, broker).

Rule: invest only what's left after every bill due before the next paycheck, spending until
then, and a checking buffer. Round down to $5 and cap at the planned amount. Savings is never
touched. safety_check is pure; run_payday does the deposits, transfers and trades.

fast_forward_to_payday, apply_life_event and reset_persona are the demo buttons. They go through
the core/nessie.py demo functions, so they work the same in fixture and live mode.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

from core import broker, nessie, rebalance
from core.models import Bill, FinancialProfile, PaydayResult, Portfolio
from core.profile import get_profile
from core.readiness import check_readiness

CHECKING_BUFFER = 200.0   # checking we never invest
DAYS_PER_MONTH = 30       # prorating convention for spending
STEP = 5.0                # invest in $5 steps

CAR_REPAIR = 600.0        # "Unexpected $600 car repair"
RAISE = 200.0             # per paycheck
MARKET_DROP_PCT = -30.0
LIFE_EVENTS = ("car_repair", "raise", "market_drop")

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


@dataclass
class PaydayRun(PaydayResult):
    """A PaydayResult plus the numbers behind it, for the payday breakdown bar."""
    breakdown: dict = field(default_factory=dict)


# Bar colors: the app's clay / sun / moss plus a blue, validated together (dataviz validator, light, on #fff)
BAR_SEGMENTS = (   # key, legend label, tooltip, color
    ("bills", "Bills due", "Bills due before the next paycheck", "#c2603a"),
    ("spending", "Spending", "Day-to-day spending until the next paycheck", "#c99a1a"),
    ("buffer", "Buffer", "Buffer that always stays in checking", "#2a78d6"),
    ("invested", "Invested", "Moved to your FirstFund Investing account", "#2f7a4f"),
)


def breakdown(cash: float, d: PaydayDecision) -> dict:
    """How payday's checking balance splits up. If bills + spending + buffer exceed the cash,
    the bar is scaled to what's needed and `short` says by how much."""
    amounts = {"bills": round(sum(b.amount for b in d.bills_due), 2), "spending": d.spend_reserved,
               "buffer": d.buffer, "invested": d.invest}
    used = sum(amounts.values())
    total = max(cash, used) or 1.0
    left = round(max(0.0, cash - used), 2)
    return {
        "cash": round(cash, 2),
        "segments": [{"key": k, "label": label, "tip": tip, "color": color, "amount": amounts[k],
                      "pct": amounts[k] / total * 100} for k, label, tip, color in BAR_SEGMENTS],
        "left": left, "left_pct": left / total * 100,
        "short": round(max(0.0, used - cash), 2),
    }


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
    # Two paychecks a month, in whole $5 steps: Nessie only moves whole dollars, and the plan invests in $5 steps.
    planned = 0.0 if r.status == "not_yet" else math.floor(r.safe_monthly_amount / 2 / STEP) * STEP

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
    buy_underweight = getattr(broker, "buy_underweight", None)   # Saahil's, once it exists
    if d.invest > 0 and portfolio:
        xfer = nessie.transfer_to_investing(customer_id, d.invest)
        for t, amt in rebalance.allocate_new_money(portfolio, broker.get_positions(customer_id), d.invest).items():
            trades.append(broker.buy_notional(t, amt))
    elif d.invest > 0 and buy_underweight:
        xfer = nessie.transfer_to_investing(customer_id, d.invest)
        trades = list(buy_underweight(customer_id, d.invest) or [])
    return PaydayRun(paycheck, planned, d.invest, xfer, trades, reasons, breakdown(p.checking_balance, d))


def fast_forward_to_payday(customer_id: str, portfolio: Portfolio | None = None) -> PaydayResult:
    """The "Fast-forward to payday" button: run the clock to payday, then run_payday."""
    nessie.advance_days(customer_id, get_profile(customer_id).days_to_payday)
    return run_payday(customer_id, portfolio)


def apply_life_event(customer_id: str, event: str) -> FinancialProfile:
    """car_repair: $600 from savings (card if savings runs out). raise: +$200 per paycheck.
    market_drop: the Investing balance falls 30%. Returns the rebuilt profile."""
    if event == "car_repair":
        nessie.record_purchase(customer_id, "Car repair", CAR_REPAIR)
    elif event == "raise":
        nessie.raise_paycheck(customer_id, RAISE)
    elif event == "market_drop":
        nessie.apply_market_move(customer_id, MARKET_DROP_PCT)
    else:
        raise ValueError(f"unknown life event {event!r}; expected one of {LIFE_EVENTS}")
    return get_profile(customer_id)


def reset_persona(customer_id: str) -> FinancialProfile:
    """Back to the starting numbers (live mode re-seeds the persona's Nessie accounts)."""
    nessie.reset(customer_id)
    return get_profile(customer_id)


def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"
