"""The customer's whole money picture, and where the fund fits in it.  OWNER: Ethan.

The fund report says how the fund did. This says how the fund fits the rest of a paycheck: bills, spending, the
cash cushion, card debt, and the investing the customer chose. Python computes every number; the page and
Claude only explain them. Advise, don't judge: it states what each choice earns or costs, and says the choice
is the customer's. It never withholds anything.

Money-in-a-month numbers follow the team convention in core/profile.py (two paychecks a month). The projection
in core/sim.py uses the real 26 paychecks a year, and the difference is noted on the page.
"""
from __future__ import annotations

import math

from core.intake import goal_label
from core.models import FinancialProfile
from core.readiness import TARGET_CUSHION_MONTHS

HIGH_INTEREST_APR = 10.0        # a card at this rate or above costs more than a diversified fund is assumed to earn
PAYCHECKS_IN_A_MONTH = 2        # the profile convention for a typical month
CRASH_PCT = 30.0                # "if the market fell 30%": the example drop shown against the customer's comfort level
MAX_PAYOFF_MONTHS = 600


def months_to_pay_off(balance: float, apr: float, payment: float) -> int | None:
    """Months to clear a card at a fixed monthly payment (interest compounds monthly). None if the payment
    doesn't cover the interest, or there's nothing to pay."""
    r = apr / 100 / 12
    if balance <= 0 or payment <= 0:
        return None
    if r == 0:
        return math.ceil(balance / payment)
    if payment <= balance * r:
        return None
    return min(MAX_PAYOFF_MONTHS, math.ceil(-math.log(1 - r * balance / payment) / math.log(1 + r)))


def build_finances(profile: FinancialProfile, answers: dict, fund_value: float, per_paycheck: float,
                   paychecks_per_year: int, fund_return_pct: float, stock_beta: float, bond_share: float,
                   interest_share: float, fee_dollars: float, projection_typical: float | None) -> dict:
    """Everything about the customer's finances the report shows, with a plain-words list of facts for Claude."""
    monthly_invest = per_paycheck * PAYCHECKS_IN_A_MONTH
    surplus = profile.monthly_surplus
    expenses = profile.monthly_expenses
    budget = {
        "income": profile.monthly_income, "bills": profile.monthly_bills, "spending": profile.monthly_spending,
        "surplus": surplus, "paycheck": profile.paycheck_amount, "per_paycheck": per_paycheck,
        "monthly_invest": monthly_invest,
        "share_of_income_pct": round(monthly_invest / profile.monthly_income * 100, 1) if profile.monthly_income else 0.0,
        "share_of_surplus_pct": round(monthly_invest / surplus * 100, 1) if surplus > 0 else None,
        "left_after_investing": round(surplus - monthly_invest, 2),
        "over_surplus": monthly_invest > max(surplus, 0.0),
        "yearly_invest": per_paycheck * paychecks_per_year,
    }

    target = TARGET_CUSHION_MONTHS * expenses
    months = profile.cushion_months
    safety = {"cushion": profile.cash_cushion, "months": round(months, 1), "target_months": TARGET_CUSHION_MONTHS,
              "target_dollars": round(target, 2), "gap": round(max(0.0, target - profile.cash_cushion), 2),
              "met": months >= TARGET_CUSHION_MONTHS, "checking": profile.checking_balance}

    interest_per_year = profile.card_debt * profile.card_apr / 100
    debt = {"balance": profile.card_debt, "apr": profile.card_apr, "interest_per_year": round(interest_per_year, 2),
            "interest_per_month": round(interest_per_year / 12, 2), "high_interest": profile.card_debt > 0
            and profile.card_apr >= HIGH_INTEREST_APR, "fund_return_pct": fund_return_pct,
            "beats_fund": profile.card_debt > 0 and profile.card_apr > fund_return_pct,
            "months_if_investing_money_paid_it": months_to_pay_off(profile.card_debt, profile.card_apr, monthly_invest),
            "months_if_surplus_paid_it": months_to_pay_off(profile.card_debt, profile.card_apr, max(surplus, 0.0))}

    net_worth = fund_value + profile.cash_cushion + profile.checking_balance - profile.card_debt
    drop = round(stock_beta * CRASH_PCT, 1)
    comfort = answers.get("max_drop")
    role = {
        "fund_value": round(fund_value, 2), "net_worth": round(net_worth, 2),
        "fund_share_of_net_worth_pct": round(fund_value / net_worth * 100, 1) if net_worth > 0 else None,
        "bond_share": bond_share, "stock_share": round(1 - bond_share, 4), "interest_share": interest_share,
        "fee_dollars": fee_dollars, "goal": goal_label(answers), "typical_in_10_years": projection_typical,
        "crash_pct": CRASH_PCT, "crash_fund_drop_pct": drop, "crash_dollars": round(fund_value * drop / 100, 2),
        "comfort_drop_pct": comfort, "within_comfort": None if comfort is None else drop <= comfort,
    }

    ladder = [
        {"step": "Cover bills and everyday spending", "done": surplus >= 0,
         "detail": (f"Bills and spending use ${expenses:,.0f} of your ${profile.monthly_income:,.0f} a month, "
                    f"leaving ${surplus:,.0f}." if surplus >= 0 else
                    f"Bills and spending use ${expenses:,.0f}, ${-surplus:,.0f} more than your ${profile.monthly_income:,.0f} income.")},
        {"step": f"Hold about {TARGET_CUSHION_MONTHS:g} months of expenses in cash", "done": safety["met"],
         "detail": (f"Your cushion covers {months:.1f} months." if safety["met"] else
                    f"Your cushion covers {months:.1f} months; ${safety['gap']:,.0f} more reaches {TARGET_CUSHION_MONTHS:g}.")},
        {"step": "Weigh high-interest card debt against investing", "done": profile.card_debt <= 0,
         "detail": ("You have no card debt." if profile.card_debt <= 0 else
                    f"Your ${profile.card_debt:,.0f} balance costs about {profile.card_apr:g}% a year "
                    f"(${interest_per_year:,.0f}); the fund is assumed to earn about {fund_return_pct:g}%, with ups and downs.")},
        {"step": "Invest steadily in your fund", "done": monthly_invest > 0,
         "detail": (f"${per_paycheck:,.0f} each paycheck, about ${budget['yearly_invest']:,.0f} a year." if monthly_invest > 0 else
                    "Nothing goes in each paycheck right now; you can set a percent any time.")},
    ]

    facts = [
        f"Each paycheck of ${profile.paycheck_amount:,.0f} puts ${per_paycheck:,.0f} into the fund, about "
        f"${monthly_invest:,.0f} in a typical month ({budget['share_of_income_pct']:g}% of income"
        + (f", {budget['share_of_surplus_pct']:g}% of what's left after bills and spending" if budget["share_of_surplus_pct"] is not None else "") + ").",
        f"Bills and spending use ${expenses:,.0f} a month of ${profile.monthly_income:,.0f} income; the cash cushion covers "
        f"{months:.1f} months against a target of {TARGET_CUSHION_MONTHS:g}.",
    ]
    if budget["over_surplus"]:
        facts.append(f"Investing ${monthly_invest:,.0f} a month is more than the ${max(surplus, 0):,.0f} left after bills and "
                     "spending, so the rest would come out of checking or savings.")
    if profile.card_debt > 0:
        facts.append(f"Card debt of ${profile.card_debt:,.0f} at {profile.card_apr:g}% costs about ${interest_per_year:,.0f} a year, "
                     f"against the fund's assumed {fund_return_pct:g}% average return (an assumption, with ups and downs). "
                     "Paying the card down is a guaranteed return equal to its rate; whether to invest at the same time is the customer's call.")
        if debt["months_if_investing_money_paid_it"]:
            facts.append(f"Sending the ${monthly_invest:,.0f} a month now going to the fund at the card instead would clear it in "
                         f"{debt['months_if_investing_money_paid_it']} months.")
    facts.append(f"The fund is {role['stock_share']:.0%} stocks and {bond_share:.0%} bonds; if the market fell {CRASH_PCT:g}% it would "
                 f"typically fall about {drop:g}% (about ${role['crash_dollars']:,.0f} on ${fund_value:,.0f}), approximately."
                 + (f" They said they could live with a {comfort:g}% drop." if comfort is not None else ""))
    if role["fund_share_of_net_worth_pct"] is not None:
        facts.append(f"The fund is {role['fund_share_of_net_worth_pct']:g}% of net worth (${net_worth:,.0f}, counting the fund, cash and checking, minus card debt).")
    return {"budget": budget, "safety": safety, "debt": debt, "role": role, "ladder": ladder, "facts": facts}
