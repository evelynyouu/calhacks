"""Balance tiers.  RULES: Lance.

More money -> FirstFund checks more often and does more. Behaviors are shown to
users, so they're in plain words; nothing here promises a number the code doesn't compute.
"""
from __future__ import annotations

from core.models import TierPolicy

_TIERS = [
    (5_000, TierPolicy(1, "$0–5K", "Monthly",
                       ["Invests each paycheck automatically", "Never sells", "Monthly report"])),
    (20_000, TierPolicy(2, "$5–20K", "Monthly",
                        ["Invests each paycheck automatically",
                         "Steers new money toward funds that have fallen behind their target"])),
    (50_000, TierPolicy(3, "$20–50K", "Every 2 weeks",
                        ["Rebalances when your mix drifts off target",
                         "Sells the shares with the smallest tax bill first"])),
    (100_000, TierPolicy(4, "$50–100K", "Weekly",
                         ["Rebalances when your mix drifts off target",
                          "Sells the shares with the smallest tax bill first",
                          "Tax-loss harvesting (preview)"])),
]
_TOP = TierPolicy(5, "$100K+", "You choose",
                  ["Shows the tax cost before confirming any trade"])


def tier_for(balance: float) -> TierPolicy:
    for upper, tier in _TIERS:
        if balance < upper:
            return tier
    return _TOP
