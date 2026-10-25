"""Readiness answer key. Lance: add 10–15 edge cases here (the 'proof it works' slide)."""
import json
from pathlib import Path

import pytest

from core.models import FinancialProfile
from core.profile import get_profile
from core.readiness import check_readiness

PERSONAS = json.loads((Path(__file__).resolve().parent.parent / "data" / "personas.json").read_text())


@pytest.mark.parametrize("cid", PERSONAS)
def test_demo_personas(cid):
    assert check_readiness(get_profile(cid)).status == PERSONAS[cid]["expected_readiness"]


def _p(**kw):
    base = dict(customer_id="x", name="X", monthly_income=3000, monthly_bills=1200, monthly_spending=1000,
                checking_balance=500, cash_cushion=7000, card_debt=0, card_apr=0, invested_balance=0,
                paycheck_amount=1500, days_to_payday=7)
    base.update(kw)
    return FinancialProfile(**base)


EDGE_CASES = [
    # (description, profile overrides, expected status)
    ("low-APR card doesn't block investing", dict(card_debt=500, card_apr=4), "ready"),
    ("high-APR card blocks investing", dict(card_debt=200, card_apr=29), "not_yet"),
    ("exactly 1 month cushion -> start small", dict(cash_cushion=2200), "start_small"),
    ("exactly 3 months cushion -> ready", dict(cash_cushion=6600), "ready"),
    ("spending more than income blocks investing", dict(monthly_spending=2000), "not_yet"),
    ("zero surplus blocks investing even with a big cushion", dict(monthly_spending=1800, cash_cushion=50000), "not_yet"),
    ("no income blocks investing", dict(monthly_income=0), "not_yet"),
    ("card at exactly 10% APR blocks investing", dict(card_debt=500, card_apr=10), "not_yet"),
    ("card just under 10% APR doesn't block", dict(card_debt=500, card_apr=9.9), "ready"),
    ("huge cushion doesn't excuse a high-APR card", dict(cash_cushion=100000, card_debt=300, card_apr=22), "not_yet"),
    ("no cushion at all -> start small", dict(cash_cushion=0), "start_small"),
    ("just under 3 months cushion -> start small", dict(cash_cushion=6590), "start_small"),
    ("tiny surplus still counts as ready", dict(monthly_spending=1790, cash_cushion=10000), "ready"),
    # Irregular income can't be expressed yet: FinancialProfile only has a monthly average.
]


@pytest.mark.parametrize("desc,kw,expected", EDGE_CASES, ids=[c[0] for c in EDGE_CASES])
def test_edge_cases(desc, kw, expected):
    assert check_readiness(_p(**kw)).status == expected


def test_ready_amount_is_a_quarter_of_surplus():
    assert check_readiness(_p()).safe_monthly_amount == 200   # surplus 800


def test_no_cliff_at_three_months():
    just_under = check_readiness(_p(cash_cushion=6590)).safe_monthly_amount
    at_target = check_readiness(_p(cash_cushion=6600)).safe_monthly_amount
    assert at_target - just_under <= 5


def test_amount_grows_with_cushion_and_never_exceeds_full_share():
    amounts = [check_readiness(_p(cash_cushion=c)).safe_monthly_amount for c in range(0, 8000, 100)]
    assert amounts == sorted(amounts)
    assert max(amounts) == 200


def test_tiny_surplus_never_invests_more_than_its_share():
    r = check_readiness(_p(monthly_spending=1790, cash_cushion=0))   # surplus 10, share 2.50 rounds to 2
    assert r.safe_monthly_amount == 2


def test_not_yet_card_gives_payoff_time_with_interest():
    # $1,200 at 25% paying $400/month: 3 months with no interest, 4 with it
    r = check_readiness(_p(card_debt=1200, card_apr=25))
    assert "about 4 months, interest included" in r.reasons[-1]


def test_start_small_says_when_fully_ready():
    r = check_readiness(_p(cash_cushion=2200))
    assert "fully ready in about" in r.reasons[-1]


def test_no_surplus_says_how_much_to_free_up():
    r = check_readiness(_p(monthly_spending=2000))   # $200/month short
    assert "$200/month" in r.reasons[-1]


def test_payment_below_interest_never_promises_a_date():
    r = check_readiness(_p(monthly_spending=1790, card_debt=50000, card_apr=29))
    assert "a very long time" in r.reasons[-1]


@pytest.mark.parametrize("risk,cap", [(1, 0.10), (2, 0.15), (3, 0.20), (4, 0.25)])
def test_tilt_cap_scales_with_risk(risk, cap):
    # One interest gets at most PER_INTEREST_CAP (8%) at any level; many interests fill up to the level's cap.
    from core.portfolio import PER_INTEREST_CAP, build_portfolio
    one = sum(h.weight for h in build_portfolio(risk, ["healthcare"]).holdings if h.role == "tilt")
    assert one == pytest.approx(min(cap, PER_INTEREST_CAP))
    many = ["healthcare", "staples", "financials", "utilities", "industrials", "dividends"]
    assert sum(h.weight for h in build_portfolio(risk, many).holdings if h.role == "tilt") == pytest.approx(cap, abs=1e-3)
