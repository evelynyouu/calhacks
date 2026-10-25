"""Return math against cases worked out by hand.  OWNER: Ethan."""
from datetime import date, timedelta

import pytest

from core import returns as R

D0 = date(2026, 1, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def prices(table: dict[str, dict[int, float]]):
    """px(ticker, date) from {ticker: {day number: price}}; a day not listed uses the last listed day."""
    def px(t: str, d: date) -> float:
        n = (d - D0).days
        known = [k for k in table[t] if k <= n]
        return table[t][max(known)] if known else table[t][min(table[t])]
    return px


def buy(n: int, symbol: str, dollars: float, px) -> dict:
    return {"on": day(n), "symbol": symbol, "side": "buy", "shares": dollars / px(symbol, day(n))}


def sell(n: int, symbol: str, dollars: float, px) -> dict:
    return {**buy(n, symbol, dollars, px), "side": "sell"}


PX = prices({"A": {0: 100, 10: 110, 20: 99}})


def test_one_deposit_dollars_and_both_returns():
    orders = [buy(0, "A", 1000, PX)]
    r = R.replay(orders, PX, day(-1), day(10))
    assert (r.start_value, r.deposits, r.end_value) == (0, pytest.approx(1000), pytest.approx(1100))
    assert r.market_gain == pytest.approx(100)
    assert R.twr(r) == pytest.approx(0.10)
    assert R.money_weighted(r) == pytest.approx(0.10, abs=1e-6)


def test_many_deposits_twr_ignores_timing():
    orders = [buy(0, "A", 1000, PX), buy(10, "A", 1000, PX)]
    r = R.replay(orders, PX, day(-1), day(20))
    # 1000 -> 1100 (+10%), then 1000 more at 110 buys 9.0909 shares, then the price falls to 99 (-10%)
    assert R.twr(r) == pytest.approx(1.10 * 0.90 - 1)                 # -1%
    assert r.deposits == pytest.approx(2000) and r.end_value == pytest.approx(1890)
    assert r.market_gain == pytest.approx(-110)
    assert r.start_value + r.deposits + r.market_gain == pytest.approx(r.end_value, abs=0.01)


def test_deposit_right_before_a_drop_hurts_the_money_weighted_return_more():
    px = prices({"A": {0: 100, 1: 100, 2: 90}})
    orders = [buy(0, "A", 1000, px), buy(1, "A", 9000, px)]
    r = R.replay(orders, px, day(-1), day(2))
    assert R.twr(r) == pytest.approx(-0.10)                           # the fund fell 10% while invested
    # 1000(1+r) + 9000(1+r)^0.5 = 9000  ->  u = (1+r)^0.5 = 0.90833  ->  r = -17.49%
    assert R.money_weighted(r) == pytest.approx(-0.1749, abs=1e-4)
    assert r.market_gain == pytest.approx(-1000)


def test_twr_is_the_price_return_when_nothing_is_added():
    orders = [buy(0, "A", 1000, PX)]
    r = R.replay(orders, PX, day(0), day(20))                          # window starts after the only deposit
    assert r.deposits == 0 and r.start_value == pytest.approx(1000)
    assert R.twr(r) == pytest.approx(99 / 100 - 1)
    assert R.money_weighted(r) == pytest.approx(99 / 100 - 1, abs=1e-6)


def test_window_in_the_middle_counts_only_later_deposits_as_new_money():
    orders = [buy(0, "A", 1000, PX), buy(10, "A", 500, PX)]
    r = R.replay(orders, PX, day(5), day(20))
    assert r.start_value == pytest.approx(1000) and r.deposits == pytest.approx(500)
    assert r.end_value == pytest.approx((10 + 500 / 110) * 99)
    assert r.start_value + r.deposits + r.market_gain == pytest.approx(r.end_value, abs=0.01)


def test_nothing_invested_gives_no_return():
    r = R.replay([], PX, day(-1), day(20))
    assert (r.start_value, r.end_value, r.deposits, r.market_gain) == (0, 0, 0, 0)
    assert R.twr(r) is None and R.money_weighted(r) is None


def test_attribution_adds_up_to_the_market_gain():
    px = prices({"A": {0: 100, 10: 110, 20: 99}, "B": {0: 50, 10: 45, 20: 60}})
    orders = [buy(0, "A", 600, px), buy(0, "B", 400, px), buy(10, "A", 300, px), buy(10, "B", 200, px)]
    r = R.replay(orders, px, day(-1), day(20))
    parts = R.attribution(r)
    assert sum(parts.values()) == pytest.approx(r.market_gain, abs=0.01)
    # A: 6 shares gain 10 each, then 6 + 300/110 shares lose 11 each. B: 8 shares lose 5 each, then 8 + 200/45 gain 15 each
    assert parts["A"] == pytest.approx(6 * 10 - (6 + 300 / 110) * 11)
    assert parts["B"] == pytest.approx(-8 * 5 + (8 + 200 / 45) * 15)


def test_selling_one_fund_to_buy_another_adds_no_new_money():
    px = prices({"A": {0: 100, 10: 110}, "B": {0: 50, 10: 50}})
    orders = [buy(0, "A", 1000, px), sell(10, "A", 220, px), buy(10, "B", 220, px)]
    r = R.replay(orders, px, day(-1), day(10))
    assert r.deposits == pytest.approx(1000, abs=1e-6)
    assert r.end_value == pytest.approx(1100)


def test_income_is_kept_apart_from_the_price_numbers():
    px = prices({"A": {0: 100, 365: 100}})
    r = R.replay([buy(0, "A", 1000, px)], px, day(-1), day(365), yields={"A": 0.04})
    assert r.market_gain == pytest.approx(0, abs=1e-9) and r.end_value == pytest.approx(1000)
    assert r.income == pytest.approx(40, abs=0.01)
    assert R.twr(r) == pytest.approx(0) and R.twr(r, with_income=True) == pytest.approx(0.04, abs=1e-4)


def test_xirr_matches_the_spreadsheet_example():
    # The worked XIRR example from Microsoft's Excel documentation: 37.34% a year.
    flows = [(date(2008, 1, 1), -10000), (date(2008, 3, 1), 2750), (date(2008, 10, 30), 4250),
             (date(2009, 2, 15), 3250), (date(2009, 4, 1), 2750)]
    assert R.xirr(flows) == pytest.approx(0.373362535, abs=1e-6)


def test_period_irr_known_case():
    # -1000 on day 0, -1000 on day 10, 2310 on day 20: 1000u^2 + 1000u = 2310 with u = (1+r)^0.5 = 1.1  ->  r = 21%
    flows = [(day(0), -1000), (day(10), -1000), (day(20), 2310)]
    assert R.period_irr(flows) == pytest.approx(0.21, abs=1e-6)


def test_irr_needs_money_in_and_out():
    assert R.period_irr([(day(0), -1000), (day(5), -500)]) is None
    assert R.period_irr([(day(0), -1000), (day(0), 1100)]) is None     # same day: no time passed


def test_shadow_portfolio_gets_the_same_new_money_at_the_given_weights():
    px = prices({"A": {0: 100, 10: 110}, "B": {0: 50, 10: 55}})
    orders = R.shadow_orders({day(0): 1000.0, day(10): 500.0}, {"A": 0.6, "B": 0.4}, px)
    r = R.replay(orders, px, day(-1), day(10))
    assert r.deposits == pytest.approx(1500)
    assert R.twr(r) == pytest.approx(0.10)         # both funds rose 10%, so any mix did
    assert R.shadow_orders({day(0): 1000.0, day(5): -400.0}, {"A": 1.0}, px)[-1]["side"] == "sell"
