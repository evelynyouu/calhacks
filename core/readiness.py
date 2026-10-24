"""Readiness check.  RULES: Lance (v1 from the plan).

Close to the v1 rules already so the demo personas land correctly.
Lance: refine thresholds and the safe-amount share.
"""
from __future__ import annotations

from core.models import FinancialProfile, Readiness

HIGH_INTEREST_APR = 10.0
SAFE_SHARE_OF_SURPLUS = 0.25   # GUESS: Lance to finalize
HABIT_AMOUNT = 25.0


def check_readiness(p: FinancialProfile) -> Readiness:
    months = p.cushion_months
    surplus = max(0.0, p.monthly_surplus)

    if p.card_debt > 0 and p.card_apr >= HIGH_INTEREST_APR:
        return Readiness(
            status="not_yet",
            safe_monthly_amount=0.0,
            headline=f"Not yet: pay off your ${p.card_debt:,.0f} card first",
            reasons=[
                f"Your card charges about {p.card_apr:.0f}% a year.",
                f"Paying it off is a guaranteed {p.card_apr:.0f}% return; no fund reliably beats that.",
                f"At ${surplus * 0.5:,.0f}/month it's gone in about {p.card_debt / max(surplus * 0.5, 1):.0f} months.",
            ],
        )

    if months < 1:
        return Readiness(
            status="start_small",
            safe_monthly_amount=HABIT_AMOUNT,
            headline=f"Start with ${HABIT_AMOUNT:.0f}/month while you build a cushion",
            reasons=[
                f"Your cushion covers {months:.1f} months of expenses; aim for at least 1.",
                f"${HABIT_AMOUNT:.0f}/month builds the habit without touching your safety net.",
            ],
        )

    if months < 3:
        amount = round(surplus * SAFE_SHARE_OF_SURPLUS / 2, 0)
        return Readiness(
            status="start_small",
            safe_monthly_amount=amount,
            headline=f"Start small: ${amount:,.0f}/month, the rest to your cushion",
            reasons=[
                f"Your cushion covers {months:.1f} months; 3 is the target.",
                "New money is split between your cushion and investing until then.",
            ],
        )

    amount = round(surplus * SAFE_SHARE_OF_SURPLUS, 0)
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
