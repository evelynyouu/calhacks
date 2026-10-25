"""The personal-finances part of the report, against numbers worked out by hand.  OWNER: Ethan."""
import pytest

from core.finances import build_finances, months_to_pay_off
from core.models import FinancialProfile


def profile(**kw) -> FinancialProfile:
    base = dict(customer_id="x", name="Maria", monthly_income=3200, monthly_bills=1450, monthly_spending=1150,
                checking_balance=900, cash_cushion=1800, card_debt=1200, card_apr=25.0, invested_balance=0,
                paycheck_amount=1600, days_to_payday=3)
    return FinancialProfile(**{**base, **kw})


def finances(p=None, per_paycheck=100, **kw):
    args = dict(answers={"goal": "A house", "max_drop": 15}, fund_value=2_000.0, per_paycheck=per_paycheck,
                paychecks_per_year=26, fund_return_pct=5.8, stock_beta=0.58, bond_share=0.45, interest_share=0.15,
                fee_dollars=0.8, projection_typical=40_000.0)
    args.update(kw)
    return build_finances(p or profile(), **args)


def test_budget_math():
    b = finances()["budget"]
    assert (b["surplus"], b["monthly_invest"], b["yearly_invest"]) == (600, 200, 2600)
    assert b["share_of_income_pct"] == pytest.approx(6.25, abs=0.06)                     # 200 / 3200
    assert b["share_of_surplus_pct"] == pytest.approx(33.3) and b["left_after_investing"] == 400
    assert not b["over_surplus"]


def test_investing_more_than_the_surplus_is_flagged():
    f = finances(per_paycheck=350)                                                      # $700 a month against $600 left over
    assert f["budget"]["over_surplus"] and f["budget"]["left_after_investing"] == -100
    assert any("more than the $600 left" in x for x in f["facts"])


def test_cushion_against_the_three_month_target():
    s = finances()["safety"]
    assert s["months"] == pytest.approx(0.7) and s["target_dollars"] == 7_800 and s["gap"] == 6_000 and not s["met"]
    assert finances(profile(cash_cushion=9_000))["safety"]["met"]


def test_card_debt_cost_and_payoff_time():
    d = finances()["debt"]
    assert (d["interest_per_year"], d["interest_per_month"]) == (300, 25) and d["high_interest"] and d["beats_fund"]
    # 25%/12 a month on $1,200 with $200 a month: n = -ln(1 - 0.0208333 x 1200 / 200) / ln(1.0208333) = 6.48 -> 7 months
    assert d["months_if_investing_money_paid_it"] == 7
    # with the whole $600 surplus: -ln(1 - 25/600) / ln(1.0208333) = 2.06 -> 3 months
    assert d["months_if_surplus_paid_it"] == 3


def test_payoff_needs_a_payment_bigger_than_the_interest():
    assert months_to_pay_off(1200, 25, 25) is None            # $25 a month is exactly the interest
    assert months_to_pay_off(1200, 0, 200) == 6               # no interest: balance / payment
    assert months_to_pay_off(0, 25, 200) is None and months_to_pay_off(1200, 25, 0) is None


def test_no_card_debt_means_nothing_to_compare():
    f = finances(profile(card_debt=0))
    assert not f["debt"]["beats_fund"] and not f["debt"]["high_interest"]
    assert not any("Card debt" in x for x in f["facts"]) and f["ladder"][2]["done"]


def test_a_cheap_card_does_not_beat_the_fund():
    d = finances(profile(card_apr=4.0))["debt"]
    assert not d["beats_fund"] and not d["high_interest"]


def test_a_market_fall_against_what_the_customer_said_they_could_live_with():
    r = finances()["role"]
    assert r["crash_fund_drop_pct"] == pytest.approx(17.4) and r["crash_dollars"] == pytest.approx(348.0)
    assert r["within_comfort"] is False                       # they said 15%
    assert finances(answers={"max_drop": 25})["role"]["within_comfort"] is True
    assert finances(answers={})["role"]["within_comfort"] is None


def test_net_worth_counts_the_fund_cash_and_checking_minus_the_card():
    r = finances()["role"]
    assert r["net_worth"] == 2_000 + 1_800 + 900 - 1_200 == 3_500
    assert r["fund_share_of_net_worth_pct"] == pytest.approx(57.1)


def test_the_ladder_marks_what_is_done():
    steps = finances()["ladder"]
    assert [s["done"] for s in steps] == [True, False, False, True]
    assert "$6,000 more reaches 3" in steps[1]["detail"]
    broke = finances(profile(monthly_income=2_000))["ladder"][0]
    assert not broke["done"] and "$600 more than your $2,000 income" in broke["detail"]


def test_facts_name_the_choice_as_the_customers():
    facts = " ".join(finances()["facts"])
    assert "customer's call" in facts
    assert "Each paycheck of $1,600 puts $100 into the fund" in facts and "$200 in a typical month" in facts
