"""The fund report: windows, dollars, returns.  OWNER: Ethan.  (Lance's engine tests are in test_engine.py.)"""
from datetime import timedelta

import pytest

from core import fund, market
from core.fund import FundAccount
from core.models import Holding, Portfolio
from core.report import benchmark_weights, build_report
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


# ---------- benchmark and attribution ----------

def _portfolio(**weights) -> Portfolio:
    roles = {"VTI": "core", "VXUS": "core", "BND": "core"}
    return Portfolio(2, "Moderate", [Holding(t, t, w, roles.get(t, "tilt"), 0.1) for t, w in weights.items()],
                     0.1, 2022, -15.0)


def test_benchmark_puts_slices_back_into_the_stock_funds_and_keeps_the_bond_share():
    w = benchmark_weights(_portfolio(VTI=0.30, VXUS=0.13, BND=0.45, XLK=0.12))
    assert w["BND"] == pytest.approx(0.45) and sum(w.values()) == pytest.approx(1)
    assert w["VTI"] / w["VXUS"] == pytest.approx(0.30 / 0.13)           # the fund's own US/international split


def test_benchmark_reads_target_weights_so_a_smooth_bond_share_works():
    w = benchmark_weights(_portfolio(VTI=0.489, VXUS=0.210, BND=0.301))
    assert w["BND"] == pytest.approx(0.301) and sum(w.values()) == pytest.approx(1)


def test_benchmark_for_a_fund_with_no_core_stocks_falls_back_to_70_30():
    w = benchmark_weights(_portfolio(BND=1.0))
    assert w == {"VTI": 0.0, "VXUS": 0.0, "BND": 1.0}


def test_benchmark_equals_the_fund_when_holdings_sit_at_core_weights_with_no_slices():
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": [], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 15)
    w = build_report(a)["returns"]["since_start"]
    assert w["benchmark_pct"] == pytest.approx(w["twr_pct"], abs=0.01) and w["vs_benchmark_pts"] == pytest.approx(0, abs=0.01)
    assert w["benchmark_value"] == pytest.approx(w["end_value"], abs=0.05)


def test_a_bond_heavy_fund_is_not_measured_against_the_s_and_p_500():
    a = account(starting_amount=10_000, started_days_ago=60, risk_priority="protect", risk_range="narrow",
                max_drop=0, swing_comfort=1, interests={"themes": [], "unmatched": []})
    market.add_shock(a.today - timedelta(days=30), 20)                  # a rally the bond-heavy fund can't match
    w = build_report(a)["returns"]["since_start"]
    assert w["vs_spy_pts"] < -3                                          # it trails the S&P 500 ...
    assert abs(w["vs_benchmark_pts"]) < 0.01                             # ... but matches what its own mix would do


def test_vs_benchmark_isolates_the_customers_choices():
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 30)                  # chips (beta 1.5) run ahead of the core
    w = build_report(a)["returns"]["since_start"]
    assert w["vs_benchmark_pts"] > 0 and w["twr_pct"] > w["benchmark_pct"]


def test_attribution_sums_to_the_market_gain_through_paychecks_and_a_rebalance():
    a = account(starting_amount=20_000, started_days_ago=120, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=60), 30)
    fund.paycheck(a)
    a.auto_trade = True
    fund.review_if_due(a)
    fund.confirm_pending(a)
    fund.paycheck(a)
    r = build_report(a)["returns"]
    for w in (r["period"], r["since_start"]):
        assert sum(x["dollars"] for x in w["attribution"]) == pytest.approx(w["market_gain"], abs=0.01)
    assert r["since_start"]["attribution"] == sorted(r["since_start"]["attribution"], key=lambda x: -abs(x["dollars"]))


def test_each_pick_is_compared_with_the_core_it_replaced():
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 30)
    pick = build_report(a)["returns"]["since_start"]["picks"][0]
    assert pick["ticker"] == "SMH" and pick["vs_core_pts"] == pytest.approx(pick["move_pct"] - pick["core_pct"], abs=0.01)
    assert pick["vs_core_pts"] > 0 and pick["vs_core_dollars"] > 0 and pick["dollars"] > pick["vs_core_dollars"]
