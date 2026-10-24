from datetime import date

import pytest

from core.profile import get_profile, list_personas, next_due_date, next_payday

SIM = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setenv("NESSIE_MODE", "fixture")


def test_four_personas():
    assert {p.persona for p in list_personas()} == {"maria", "jordan", "sam", "alex"}


@pytest.mark.parametrize(
    "persona, income, expenses, surplus, cushion_months, card, investing",
    [
        ("maria", 2708.33, 2050, 658.33, 1.28, 1200, 0),
        ("jordan", 2491.67, 2200, 291.67, 0.51, 0, 0),
        ("sam", 3033.33, 2200, 833.33, 3.19, 0, 3000),
        ("alex", 7366.67, 4300, 3066.67, 5.19, 0, 65000),
    ],
)
def test_fixture_numbers(persona, income, expenses, surplus, cushion_months, card, investing):
    p = get_profile(persona)
    assert p.as_of == SIM
    assert p.next_payday == date(2026, 10, 30)
    assert p.monthly_income == income
    assert p.monthly_expenses == expenses
    assert p.monthly_surplus == surplus
    assert p.cushion_months == cushion_months
    assert p.card_balance == card
    assert p.investing_balance == investing


def test_readiness_bands_land():
    # The thresholds belong to readiness.py; this just guards the seed numbers.
    assert get_profile("maria").card_balance > 0
    assert get_profile("jordan").cushion_months < 1
    assert get_profile("sam").cushion_months >= 3
    assert get_profile("alex").investing_balance >= 50000


def test_sam_car_repair_drops_below_three_months():
    p = get_profile("sam")
    spare = p.cushion_cash - p.savings_balance
    assert (p.savings_balance - 600 + spare) / p.monthly_expenses < 3


def test_sam_rent_due_four_days_after_payday():
    p = get_profile("sam", as_of=date(2026, 10, 30))
    rent = next(b for b in p.upcoming_bills if b.category == "rent")
    assert (rent.due_date - p.as_of).days == 4
    assert p.next_payday == date(2026, 11, 13)


def test_next_due_date_rolls_and_clamps():
    assert next_due_date(3, SIM) == date(2026, 11, 3)
    assert next_due_date(23, SIM) == date(2026, 10, 23)
    assert next_due_date(31, date(2026, 11, 5)) == date(2026, 11, 30)
    assert next_due_date(1, date(2026, 12, 15)) == date(2027, 1, 1)


def test_next_payday_frequencies():
    assert next_payday(date(2026, 10, 16), "biweekly") == date(2026, 10, 30)
    assert next_payday(date(2026, 10, 1), "semimonthly") == date(2026, 10, 15)
    assert next_payday(date(2026, 12, 15), "semimonthly") == date(2027, 1, 1)
    assert next_payday(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_unknown_persona():
    with pytest.raises(KeyError):
        get_profile("nobody")
