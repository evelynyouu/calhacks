"""Payday safety check. The Sam case is the demo: $50 instead of $150 because rent is due in 4 days."""
import pytest

from core import nessie
from core.models import Bill
from core.payday import run_payday, safety_check
from core.portfolio import build_portfolio
from core.profile import get_profile


@pytest.fixture(autouse=True)
def fresh_personas():
    nessie.reset()
    yield
    nessie.reset()


def payday(cid, portfolio=None):
    nessie.advance_days(cid, get_profile(cid).days_to_payday)   # what the "Fast-forward to payday" button does
    return run_payday(cid, portfolio)


def test_sam_demo_invests_50_of_150():
    r = payday("sam", build_portfolio(2, []))
    assert r.paycheck_detected == 2050
    assert r.planned_amount == 150
    assert r.invested_amount == 50
    assert r.transfer_id is not None
    assert sum(t.notional for t in r.trades) == pytest.approx(50)
    assert any("Rent ($1,350) is due in 4 days" in s for s in r.reasons)
    assert get_profile("sam").checking_balance == 575 - 35 + 2050 - 50   # 1 day of spending before payday


def test_sam_safety_check_math():
    d = safety_check(540 + 2050, 150, 14, [Bill("Rent", 1350, 4), Bill("Phone + utilities", 500, 6)], 1050)
    # 2,590 - 1,850 bills - 490 spending (1,050 x 14/30) - 200 buffer = 50
    assert [b.name for b in d.bills_due] == ["Rent", "Phone + utilities"]
    assert d.spend_reserved == 490
    assert d.available == 50
    assert (d.invest, d.held_back, d.reason) == (50, 100, "reduced_for_bills")


def test_second_payday_pays_and_rolls_bills():
    fund = build_portfolio(2, [])
    payday("sam", fund)
    second = payday("sam", fund)   # 14 days later: rent (Oct 28) and phone (Oct 30) were paid; next due by the calendar
    p = get_profile("sam")
    assert {b.name: b.due_in_days for b in p.upcoming_bills} == {"Rent": 21, "Phone + utilities": 23}   # Nov 7: rent on the 28th, phone on the 30th
    assert not any("is due in" in s for s in second.reasons)
    # 2,540 after payday 1, minus 14 days of spending (490) and both bills (1,850), plus paycheck 2
    assert p.checking_balance == 2540 - 490 - 1850 + 2050 - second.invested_amount


def test_maria_not_yet_invests_nothing():
    r = payday("maria", build_portfolio(2, []))
    assert r.planned_amount == 0 and r.invested_amount == 0 and r.transfer_id is None
    assert any("paused" in s for s in r.reasons)


def test_no_portfolio_means_no_transfer():
    r = payday("sam")
    assert r.invested_amount == 50 and r.transfer_id is None and r.trades == []


def test_full_when_room():
    d = safety_check(3000, 150, 14, [], 900)
    assert (d.invest, d.held_back, d.reason) == (150, 0, "full")


def test_negative_available_is_skipped():
    d = safety_check(1500, 150, 14, [Bill("Rent", 1100, 4)], 900)
    assert d.available < 0
    assert (d.invest, d.held_back, d.reason) == (0, 150, "skipped")


def test_planned_zero():
    d = safety_check(5000, 0, 14, [], 0)
    assert (d.invest, d.held_back, d.reason) == (0, 0, "skipped")


@pytest.mark.parametrize("due_in", [0, 14])
def test_bill_due_today_or_on_next_payday_counts(due_in):
    d = safety_check(1000, 500, 14, [Bill("Rent", 300, due_in)], 0)
    assert len(d.bills_due) == 1 and d.available == 500


def test_bill_due_after_next_payday_ignored():
    d = safety_check(1000, 500, 14, [Bill("Rent", 300, 15)], 0)
    assert d.bills_due == [] and d.available == 800


def test_empty_bills_ok():
    d = safety_check(1000, 500, 14, [], 300)   # 1,000 - 140 spending - 200
    assert d.bills_due == [] and d.spend_reserved == 140
    assert (d.available, d.invest, d.reason) == (660, 500, "full")


def test_rounds_down_to_five():
    d = safety_check(1000, 1000, 14, [Bill("Rent", 10.01, 14)], 0)   # available 789.99
    assert d.invest == 785


def test_days_to_payday_must_be_positive():
    with pytest.raises(ValueError):
        safety_check(1000, 100, 0, [], 0)


def test_planned_and_invested_are_whole_five_dollar_steps(monkeypatch):
    """Readiness can produce a planned amount like $189.50; Nessie only moves whole dollars."""
    import core.payday as payday_mod
    real = payday_mod.check_readiness

    def odd_amount(p):
        r = real(p)
        r.safe_monthly_amount = 379.0   # -> $189.50 a paycheck before rounding
        return r

    monkeypatch.setattr(payday_mod, "check_readiness", odd_amount)
    r = payday("alex", build_portfolio(2, []))   # Alex has room to invest the full planned amount
    assert (r.planned_amount, r.invested_amount) == (185, 185)
