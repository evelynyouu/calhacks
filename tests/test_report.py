"""The fund report: windows, dollars, returns.  OWNER: Ethan.  (Lance's engine tests are in test_engine.py.)"""
from datetime import timedelta

import pytest

from core import fund, market
from core.fund import FundAccount
from core.report import build_report
from tests.fund_helpers import ANSWERS


def account(cid: str = "sam", started_days_ago: int = 0, **answers) -> FundAccount:
    a = FundAccount(cid, answers={**ANSWERS, **answers})
    fund.start(a, on=a.today - timedelta(days=started_days_ago))
    return a


def test_dollars_add_up_for_both_windows():
    a = account(starting_amount=5_000, started_days_ago=90)
    fund.paycheck(a)
    fund.paycheck(a)
    r = build_report(a)["returns"]
    for w in (r["period"], r["since_start"]):
        assert w["start_value"] + w["deposits"] + w["market_gain"] == pytest.approx(w["end_value"], abs=0.02)
    assert r["since_start"]["end_value"] == pytest.approx(fund.value(a), abs=0.01)
    assert r["since_start"]["deposits"] == pytest.approx(a.contributed, abs=0.01)


def test_period_runs_back_one_review_cadence_not_a_fixed_30_days():
    a = account("alex", started_days_ago=365, starting_amount=60_000)      # tier 4: weekly reviews
    rep = build_report(a)
    assert rep["period_days"] == 7 and rep["returns"]["period"]["start"] == a.today - timedelta(days=7)
    assert not rep["returns"]["period"]["from_start"]


def test_period_reaches_back_to_the_last_review_when_that_is_older():
    a = account(started_days_ago=200, starting_amount=3_000)               # tier 1: monthly
    a.last_review = a.today - timedelta(days=45)
    assert build_report(a)["period_days"] == 45


def test_a_fund_younger_than_the_period_says_so_instead_of_faking_30_days():
    a = account(started_days_ago=10, starting_amount=3_000)                # tier 1 cadence is 30 days
    rep = build_report(a)
    p = rep["returns"]["period"]
    assert p["from_start"] and p["days"] == 10 and p["start"] == a.today - timedelta(days=10)
    assert p["twr_pct"] == rep["returns"]["since_start"]["twr_pct"]
    assert rep["period_days"] == 10


def test_a_fund_with_nothing_in_it_has_no_returns_and_no_errors():
    a = account(starting_amount=0)
    rep = build_report(a)
    assert rep["returns"]["available"] is False and rep["returns"]["period"] is None
    assert rep["summary"]["value"] == 0 and rep["summary"]["gain"] == 0 and rep["summary"]["gain_pct"] == 0


def test_one_paycheck_and_no_starting_money_has_no_misleading_percent():
    a = account(starting_amount=0)
    fund.paycheck(a)                                                        # the first order is today: no time has passed
    w = build_report(a)["returns"]["since_start"]
    assert w["deposits"] == pytest.approx(a.contributed) and w["days"] == 0
    assert w["twr_pct"] is None and w["mwr_pct"] is None


def test_a_deposit_just_before_a_drop_shows_up_as_a_gap_between_the_two_returns():
    a = account(starting_amount=1_000, started_days_ago=30)
    fund.paycheck(a)
    market.add_shock(a.today + timedelta(days=1), -20)
    a.today += timedelta(days=2)
    w = build_report(a)["returns"]["since_start"]
    assert w["mwr_pct"] < w["twr_pct"] < 0      # the late deposit lost money too, so the customer's own return is worse
