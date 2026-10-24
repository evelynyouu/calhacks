"""Demo buttons in fixture mode: fast-forward, life events, reset."""
import pytest

from core import nessie
from core.payday import apply_life_event, fast_forward_to_payday, reset_persona
from core.profile import get_profile
from core.readiness import check_readiness


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    nessie.reset()
    yield
    nessie.reset()


def test_sam_fast_forward_invests_50():
    r = fast_forward_to_payday("sam")
    assert (r.planned_amount, r.invested_amount) == (150, 50)
    assert get_profile("sam").days_to_payday == 14


def test_car_repair_takes_sam_below_three_months():
    before = get_profile("sam")
    p = apply_life_event("sam", "car_repair")
    assert p.cash_cushion == before.cash_cushion - 600
    assert p.checking_balance == before.checking_balance and p.card_debt == 0
    assert before.cushion_months >= 3 > p.cushion_months
    assert check_readiness(p).status == "start_small"


def test_car_repair_goes_on_card_when_savings_runs_out():
    for _ in range(3):   # Jordan has $1,500 saved
        p = apply_life_event("jordan", "car_repair")
    assert (p.cash_cushion, p.card_debt) == (0, 300)


def test_raise_then_fast_forward_invests_full_planned():
    before = get_profile("sam")
    p = apply_life_event("sam", "raise")
    assert p.paycheck_amount == before.paycheck_amount + 200
    assert p.monthly_income == before.monthly_income + 400
    r = fast_forward_to_payday("sam")
    # Lance's readiness: 25% of the $1,600 surplus = $400/mo -> $200/paycheck, and bills leave room for all of it
    assert (r.planned_amount, r.invested_amount) == (200, 200)


def test_market_drop_takes_alex_to_45500():
    assert apply_life_event("alex", "market_drop").invested_balance == 45500


def test_reset_restores_everything():
    start = {cid: get_profile(cid) for cid in ("sam", "alex")}
    fast_forward_to_payday("sam")
    for event in ("car_repair", "raise", "market_drop"):
        apply_life_event("sam", event)
    apply_life_event("alex", "market_drop")
    assert reset_persona("sam") == start["sam"]
    assert get_profile("alex") != start["alex"]   # reset is per persona
    assert reset_persona("alex") == start["alex"]


def test_unknown_event():
    with pytest.raises(ValueError):
        apply_life_event("sam", "lottery")


def test_other_bills_fill_the_gap_to_monthly_bills():
    for cid in ("maria", "jordan", "sam", "alex"):
        p = get_profile(cid)
        assert sum(b.amount for b in p.upcoming_bills) == p.monthly_bills
    assert [b.name for b in get_profile("sam").upcoming_bills] == ["Rent", "Phone + utilities"]
    assert next(b for b in get_profile("maria").upcoming_bills if b.name == "Other bills").amount == 310
