from datetime import date

import pytest

from core.models import UpcomingBill
from core.payday import safety_check
from core.profile import get_profile

PAYDAY = date(2026, 10, 30)
NEXT = date(2026, 11, 13)


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setenv("NESSIE_MODE", "fixture")


def bill(amount, due, name="Bill"):
    return UpcomingBill(name=name, category="other", amount=amount, due_date=due, account_id="x")


def sam_on_payday(planned):
    p = get_profile("sam", as_of=PAYDAY)
    return safety_check(p.checking_balance + p.paycheck_amount, planned, p.as_of, p.next_payday,
                        p.upcoming_bills, p.monthly_spending)


def test_sam_demo_invests_50_of_150():
    d = sam_on_payday(150)
    # 430 + 1400 - (1100 rent + 60 phone) - 900*14/30 - 200 = 50
    assert [b.name for b in d.bills_due] == ["Rent", "Phone"]
    assert d.spend_reserved == 420
    assert d.available == 50
    assert d.invest == 50
    assert d.held_back == 100
    assert d.reason == "reduced_for_bills"


def test_full_when_room():
    d = safety_check(3000, 150, PAYDAY, NEXT, [], 900)
    assert d.invest == 150 and d.held_back == 0 and d.reason == "full"


def test_zero_is_skipped():
    d = safety_check(1500, 150, PAYDAY, NEXT, [bill(1100, date(2026, 11, 3))], 900)
    assert d.available < 0
    assert d.invest == 0 and d.held_back == 150 and d.reason == "skipped"


@pytest.mark.parametrize("due", [PAYDAY, NEXT])
def test_bill_on_boundary_day_counts(due):
    d = safety_check(1000, 500, PAYDAY, NEXT, [bill(300, due)], 0)
    assert len(d.bills_due) == 1
    assert d.available == 500


def test_bill_after_next_payday_ignored():
    d = safety_check(1000, 500, PAYDAY, NEXT, [bill(300, date(2026, 11, 14))], 0)
    assert d.bills_due == [] and d.available == 800


def test_planned_zero():
    d = safety_check(5000, 0, PAYDAY, NEXT, [], 0)
    assert d.invest == 0 and d.held_back == 0 and d.reason == "skipped"


def test_floors_to_five():
    d = safety_check(1000, 1000, PAYDAY, NEXT, [bill(10.01, NEXT)], 0)  # available 789.99
    assert d.invest == 785


def test_next_payday_must_be_after_as_of():
    with pytest.raises(ValueError):
        safety_check(1000, 100, PAYDAY, PAYDAY, [], 0)
