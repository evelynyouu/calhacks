"""A paycheck buys the ten most underweight holdings, not all sixty.  OWNER: Ethan."""
import pytest

from core import fund
from core.broker import get_broker, values
from core.fund import FundAccount
from core.rebalance import MIN_TRADE, NEW_MONEY_PICKS, allocate_new_money, drift
from core.state import SIM_START
from tests.fund_helpers import ANSWERS, make_custom_fund, make_legacy_etf_fund, make_portfolio_fixture


def targets(p):
    return {h.ticker: h.weight for h in p.holdings}


def test_a_paycheck_buys_at_most_ten_holdings_and_every_dollar_is_placed():
    p = make_portfolio_fixture()
    plan = allocate_new_money(p, {}, 205.0, NEW_MONEY_PICKS)
    assert len(plan) == NEW_MONEY_PICKS == 10 and sum(plan.values()) == pytest.approx(205.0, abs=1e-9)
    assert min(plan.values()) >= MIN_TRADE


def test_the_ten_are_the_most_underweight():
    p = make_portfolio_fixture()
    current = {h.ticker: h.weight * 1000 for h in p.holdings}
    current["BND"] = 0.0                                        # bonds are missing, so they're the furthest below target
    current["VXUS"] *= 0.5
    current["TGT"] = 0.0                                        # and one spend company is missing too
    plan = allocate_new_money(p, current, 300.0, 3)
    assert set(plan) == {"BND", "VXUS", "TGT"} and plan["BND"] > plan["VXUS"] > plan["TGT"]
    assert set(allocate_new_money(p, current, 300.0, 2)) == {"BND", "VXUS"}              # the limit keeps the furthest two


def test_a_holding_only_a_few_cents_under_target_is_left_for_later_not_given_a_sub_dollar_order():
    p = make_portfolio_fixture()
    current = {h.ticker: h.weight * 1000 for h in p.holdings}
    current["BND"] = 0.0
    current["VXUS"] *= 0.5
    plan = allocate_new_money(p, current, 300.0, 3)                                     # the third gap is under a dollar's share
    assert set(plan) == {"BND", "VXUS"} and min(plan.values()) >= MIN_TRADE and sum(plan.values()) == pytest.approx(300.0)


def test_thin_money_makes_fewer_bigger_orders_never_one_under_a_dollar():
    p = make_portfolio_fixture()
    for amount in (3.0, 5.0, 12.5, 40.0):
        plan = allocate_new_money(p, {}, amount, NEW_MONEY_PICKS)
        assert plan and min(plan.values()) >= MIN_TRADE and len(plan) <= min(NEW_MONEY_PICKS, int(amount))
        assert sum(plan.values()) == pytest.approx(amount, abs=1e-9)


def test_less_than_a_dollar_places_nothing():
    assert allocate_new_money(make_portfolio_fixture(), {}, 0.5, NEW_MONEY_PICKS) == {}


def test_without_a_limit_everything_underweight_is_bought_as_before():
    p = make_portfolio_fixture()
    plan = allocate_new_money(p, {}, 10_000.0)
    assert len(plan) == 58 and sum(plan.values()) == pytest.approx(10_000.0, abs=1e-9)
    assert max(abs(plan[t] / 10_000 - w) for t, w in targets(p).items()) < 0.0001      # a first deposit lands on target


def test_an_older_fund_with_fewer_than_ten_holdings_is_unchanged():
    p = make_legacy_etf_fund()
    assert allocate_new_money(p, {}, 205.0, NEW_MONEY_PICKS) == allocate_new_money(p, {}, 205.0)


def test_the_starting_deposit_buys_every_holding_and_a_paycheck_buys_ten():
    s = make_custom_fund("sam", 10_000)                         # fund.invest with no limit, like fund.start
    a = s.fund
    assert len(get_broker().positions("sam")) == 58
    before = len(get_broker().orders("sam"))
    a.invest_pct = 10
    entry = fund.paycheck(a)
    placed = get_broker().orders("sam")[before:]
    assert len(entry["trades"]) == len(placed) <= NEW_MONEY_PICKS
    assert all(o["notional"] >= MIN_TRADE for o in placed) and sum(o["notional"] for o in placed) == pytest.approx(entry["invested"], abs=0.05)


def test_weights_converge_to_target_over_26_paychecks_with_no_order_under_a_dollar():
    a = FundAccount("sam", answers=dict(ANSWERS))
    a.portfolio = make_portfolio_fixture()
    worst = []
    for _ in range(26):
        trades = fund.invest(a, 500.0, SIM_START, NEW_MONEY_PICKS)
        assert 1 <= len(trades) <= NEW_MONEY_PICKS and all(x.notional >= MIN_TRADE for x in trades)
        worst.append(max(abs(d) for d in drift(a.portfolio, values("sam", SIM_START)).values()))
    assert worst[0] > 0.05 and worst[-1] < 0.01                 # from 5+ points off target to under one point
    assert worst[-1] < worst[len(worst) // 2] < worst[0]
    assert len(get_broker().orders("sam")) <= 26 * NEW_MONEY_PICKS          # not 26 x 58
