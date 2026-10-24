"""Balance tiers.  RULES: Lance."""
from __future__ import annotations

from core.models import TierPolicy

_TIERS = [
    (5_000, TierPolicy(1, "$0–5K", "Monthly",
                       ["Paycheck auto-invest only", "Never sells", "Monthly report"])),
    (20_000, TierPolicy(2, "$5–20K", "Every 2 months",
                        ["Paycheck auto-invest", "Threshold rebalancing"])),
    (50_000, TierPolicy(3, "$20–50K", "Monthly",
                        ["Threshold rebalancing", "Tax-aware selling"])),
    (100_000, TierPolicy(4, "$50–100K", "Weekly",
                         ["Threshold rebalancing", "Tax-aware selling", "Tax-loss harvesting (shown, not built)"])),
]
_TOP = TierPolicy(5, "$100K+", "Customer chooses",
                  ["Shows the tax trade-off before confirming any trade"])


def tier_for(balance: float) -> TierPolicy:
    for upper, tier in _TIERS:
        if balance < upper:
            return tier
    return _TOP
