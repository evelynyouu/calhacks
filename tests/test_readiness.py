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
    # TODO(Lance): negative surplus, zero income, irregular income, huge cushion + debt, ...
]


@pytest.mark.parametrize("desc,kw,expected", EDGE_CASES, ids=[c[0] for c in EDGE_CASES])
def test_edge_cases(desc, kw, expected):
    assert check_readiness(_p(**kw)).status == expected
