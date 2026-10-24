"""Balance tiers.  RULES: Lance.

More money -> FirstFund reviews more often and does more. Behaviors are shown to users, so they're
in plain words; nothing here promises a number the code doesn't compute.
"""
from __future__ import annotations

from core.models import TierPolicy

_EVERY_PAYCHECK = "Invests your chosen percent of every paycheck the moment it lands"
_CONFIRM = "Each report can line up rebalancing trades; nothing trades until you confirm"

_TIERS = [
    (5_000, TierPolicy(1, "$0–5K", "Monthly",
                       [_EVERY_PAYCHECK, "New money goes to the funds furthest below target",
                        "Never sells; monthly report"], 30, False)),
    (20_000, TierPolicy(2, "$5–20K", "Monthly",
                        [_EVERY_PAYCHECK, "Monthly report", _CONFIRM], 30, True)),
    (50_000, TierPolicy(3, "$20–50K", "Every 2 weeks",
                        [_EVERY_PAYCHECK, "Report every 2 weeks", _CONFIRM], 14, True)),
    (100_000, TierPolicy(4, "$50–100K", "Weekly",
                         [_EVERY_PAYCHECK, "Weekly report", _CONFIRM], 7, True)),
]
_TOP = TierPolicy(5, "$100K+", "Weekly",
                  [_EVERY_PAYCHECK, "Weekly report", _CONFIRM,
                   "Worth talking to a tax professional about where to hold each fund"], 7, True)


def tier_for(balance: float) -> TierPolicy:
    for upper, tier in _TIERS:
        if balance < upper:
            return tier
    return _TOP
