"""Data files match what the code reads.  OWNER: Lance.

personas.json feeds Evelyn's Nessie seeding and the readiness answer key; etfs.csv feeds
Saahil's portfolio builder and price cache. These catch a renamed field or missing ticker
before the demo does.
"""
import csv
import json
from dataclasses import fields
from pathlib import Path

import pytest

from agent.tools import TOOLS
from core.models import FinancialProfile
from core.policy import tier_for
from core.portfolio import CORE_BY_RISK, TILTS_BY_INTEREST

DATA = Path(__file__).resolve().parent.parent / "data"
PERSONAS = json.loads((DATA / "personas.json").read_text())
ETFS = {r["ticker"]: r for r in csv.DictReader((DATA / "etfs.csv").open())}
PROFILE_FIELDS = {f.name for f in fields(FinancialProfile)} - {"customer_id"}
MONEY_FIELDS = ["monthly_income", "monthly_bills", "monthly_spending", "checking_balance",
                "cash_cushion", "card_debt", "invested_balance", "paycheck_amount"]


@pytest.mark.parametrize("cid", PERSONAS)
def test_persona_has_every_profile_field(cid):
    p = PERSONAS[cid]
    assert PROFILE_FIELDS <= p.keys()
    assert p["expected_readiness"] in ("not_yet", "start_small", "ready")
    assert p["blurb"]


@pytest.mark.parametrize("cid", PERSONAS)
def test_persona_amounts_are_whole_dollars(cid):
    # Nessie stores whole-dollar amounts; cents would drift between fixture and live mode.
    p = PERSONAS[cid]
    for f in MONEY_FIELDS:
        assert float(p[f]).is_integer(), f
    for b in p["upcoming_bills"]:
        assert float(b["amount"]).is_integer(), b["name"]


@pytest.mark.parametrize("cid", PERSONAS)
def test_listed_bills_fit_inside_monthly_bills(cid):
    # core/nessie.py turns the remainder into an "Other bills" line; a negative remainder breaks that.
    p = PERSONAS[cid]
    assert sum(b["amount"] for b in p["upcoming_bills"]) <= p["monthly_bills"]
    assert all(b["due_in_days"] >= 0 for b in p["upcoming_bills"])


def test_core_funds_exist_with_matching_risk_level():
    for level, ticker in CORE_BY_RISK.items():
        assert ETFS[ticker]["role"] == "core"
        assert int(ETFS[ticker]["risk_level"]) == level


def test_tilt_funds_exist_with_matching_theme():
    for interest, tickers in TILTS_BY_INTEREST.items():
        for t in tickers:
            assert ETFS[t]["role"] == "tilt"
            assert ETFS[t]["theme"] == interest


def test_claude_interest_options_match_tilts():
    build = next(t for t in TOOLS if t["name"] == "build_fund")
    assert set(build["input_schema"]["properties"]["interests"]["items"]["enum"]) == set(TILTS_BY_INTEREST)


def test_expense_ratios_are_sane_percents():
    for t, r in ETFS.items():
        assert 0 < float(r["expense_ratio"]) < 1, t


@pytest.mark.parametrize("balance,tier", [(0, 1), (4_999, 1), (5_000, 2), (19_999, 2), (20_000, 3),
                                          (50_000, 4), (65_000, 4), (100_000, 5)])
def test_tier_boundaries(balance, tier):
    assert tier_for(balance).tier == tier


def test_bigger_balances_never_check_less_often():
    order = ["Monthly", "Every 2 weeks", "Weekly"]
    freqs = [tier_for(b).check_frequency for b in (0, 5_000, 20_000, 50_000)]
    assert [order.index(f) for f in freqs] == sorted(order.index(f) for f in freqs)
