"""The 10-year range is built from the fund's own holdings.  OWNER: Ethan."""
import math

import pytest

from core import sim
from core.models import Holding, Portfolio
from core.portfolio import build_portfolio
from core.report import PAYCHECKS_PER_YEAR, build_report
from tests.test_report import account


def portfolio(fee: float = 0.0, **weights) -> Portfolio:
    return Portfolio(2, "Moderate", [Holding(t, t, w, "core", 0.0) for t, w in weights.items()], fee, 2022, -15.0)


def test_fund_return_and_volatility_follow_the_weights():
    a = sim.portfolio_assumptions(portfolio(VTI=0.6, BND=0.4))
    assert a["mean"] == pytest.approx(0.6 * 0.07 + 0.4 * 0.04)
    # 0.36 x 16%^2 + 0.16 x 5.5%^2 + 2 x 0.6 x 0.4 x 0.10 (stock-bond correlation) x 16% x 5.5%
    assert a["vol"] == pytest.approx(math.sqrt(0.36 * 0.16 ** 2 + 0.16 * 0.055 ** 2 + 0.48 * 0.10 * 0.16 * 0.055))


def test_the_yearly_fee_comes_off_the_return():
    assert sim.portfolio_assumptions(portfolio(fee=0.5, VTI=1.0))["mean"] == pytest.approx(0.07 - 0.005)


def test_stocks_and_bonds_together_swing_less_than_their_average():
    both = sim.portfolio_assumptions(portfolio(VTI=0.5, BND=0.5))["vol"]
    assert both < 0.5 * 0.16 + 0.5 * 0.055


def test_every_fund_and_group_pair_has_an_assumption():
    groups = {v["group"] for v in sim.ASSUMPTIONS.values()}
    from core.market import ETFS
    assert set(ETFS) <= set(sim.ASSUMPTIONS)
    assert all(frozenset({a, b}) in sim.CORRELATION for a in groups for b in groups)


def test_two_funds_at_the_same_risk_level_get_different_ranges_when_their_holdings_differ():
    chips = sim.portfolio_assumptions(build_portfolio(3, ["ai_chips"]))
    steady = sim.portfolio_assumptions(build_portfolio(3, ["utilities"]))
    assert chips["vol"] > steady["vol"] and chips["mean"] > steady["mean"]


def test_a_smooth_bond_share_works_not_just_four_fixed_ones():
    low, high = (sim.portfolio_assumptions(portfolio(VTI=1 - b, BND=b))["mean"] for b in (0.301, 0.45))
    assert low > high


def test_bands_are_ordered_and_start_at_the_balance():
    r = sim.simulate(build_portfolio(2, ["tech"]), 5_000, 300)
    assert r.p10[0] == r.p50[0] == r.p90[0] == 5_000
    assert all(lo < mid < hi for lo, mid, hi in zip(r.p10[1:], r.p50[1:], r.p90[1:]))
    assert r.total_contributed == 5_000 + 300 * 12 * 10 and r.expected_return_pct > 0 and r.volatility_pct > 0


def test_with_no_volatility_the_typical_path_is_the_plain_annuity_formula(monkeypatch):
    monkeypatch.setitem(sim.ASSUMPTIONS, "BND", {"group": "bonds", "mean": 0.04, "vol": 0.0})
    r = sim.simulate(portfolio(BND=1.0), 1_000, 100)
    rate = 1.04
    for y in (1, 5, 10):
        # each monthly deposit grows for the months left: sum of 100 x 1.04^(j/12), j = 0 .. 12y-1
        annuity = 100 * (rate ** y - 1) / (rate ** (1 / 12) - 1)
        assert r.p50[y] == pytest.approx(1_000 * rate ** y + annuity, abs=0.05)
        assert r.p10[y] == pytest.approx(r.p90[y], abs=0.05)


def test_a_paycheck_is_26_a_year_not_24_and_the_deposit_matches_fund_paycheck_rounding():
    assert PAYCHECKS_PER_YEAR == 26
    rep = build_report(account(paycheck_pct=10))                       # Sam: $2,050 paycheck, 10% -> $205 each
    assert rep["projection_inputs"]["per_paycheck"] == 205
    assert rep["projection"].monthly_contribution == pytest.approx(205 * 26 / 12, abs=0.01)   # not 205 x 2 = 410
    assert rep["projection"].total_contributed == pytest.approx(3_000 + rep["projection"].monthly_contribution * 120, abs=1)
