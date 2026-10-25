"""Data files match what the code reads.  OWNER: Lance.

personas.json feeds Evelyn's Nessie seeding; etfs.csv, companies.csv and holdings.csv feed the
fund builder, the simulated market and the report. These catch a renamed field or missing ticker
before the demo does.
"""
import csv
import json
from dataclasses import fields
from pathlib import Path

import pytest

from agent.tools import TOOLS
from core.intake import BY_KEY, INTEREST_WORDS
from core.models import FinancialProfile
from core.policy import tier_for
from core.portfolio import LEVEL_BONDS, RETURNS, THEMES

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


def test_core_funds_are_core_in_the_csv():
    # The fixed per-level mixes became a smooth bond share (core/portfolio.py); the core funds are the
    # ones with real 2008/2022 returns.
    assert all(ETFS[t]["role"] == "core" for t in RETURNS)
    assert {t for t, r in ETFS.items() if r["role"] == "core"} == set(RETURNS)


def test_more_risk_never_means_more_bonds():
    bonds = [LEVEL_BONDS[level] for level in sorted(LEVEL_BONDS)]
    assert bonds == sorted(bonds, reverse=True)


def test_every_theme_has_one_fund_with_a_label_and_description():
    tilts = [r for r in ETFS.values() if r["role"] == "tilt"]
    assert len({r["theme"] for r in tilts}) == len(tilts)
    assert all(r["label"] and r["about"] for r in ETFS.values())


def test_interest_chips_and_claude_tools_match_the_themes():
    assert {v for v, _ in BY_KEY["interests"].options} == set(THEMES)
    update = next(t for t in TOOLS if t["name"] == "update_interests")
    assert set(update["input_schema"]["properties"]["add"]["items"]["enum"]) == set(THEMES)


def test_every_theme_can_be_typed_as_other():
    assert set(INTEREST_WORDS) == set(THEMES)


def test_look_through_holdings_point_at_known_funds_and_companies():
    holdings = list(csv.DictReader((DATA / "holdings.csv").open()))
    companies = {r["ticker"] for r in csv.DictReader((DATA / "companies.csv").open())}
    for r in holdings:
        assert r["etf"] in ETFS and r["ticker"] in companies, r
    for etf in {r["etf"] for r in holdings}:
        assert sum(float(r["weight_pct"]) for r in holdings if r["etf"] == etf) < 100, etf


def test_market_params_are_sane():
    for t, r in ETFS.items():
        assert float(r["price"]) > 0 and 0 <= float(r["beta"]) <= 2 and 0 < float(r["resid_vol"]) < 1, t


def test_expense_ratios_are_sane_percents():
    for t, r in ETFS.items():
        assert 0 < float(r["expense_ratio"]) < 1, t


@pytest.mark.parametrize("balance,tier", [(0, 1), (4_999, 1), (5_000, 2), (19_999, 2), (20_000, 3),
                                          (50_000, 4), (65_000, 4), (100_000, 5)])
def test_tier_boundaries(balance, tier):
    assert tier_for(balance).tier == tier


def test_bigger_balances_never_review_less_often():
    order = ["Monthly", "Every 2 weeks", "Weekly"]
    balances = (0, 5_000, 20_000, 50_000, 100_000)
    freqs = [tier_for(b).check_frequency for b in balances]
    assert [order.index(f) for f in freqs] == sorted(order.index(f) for f in freqs)
    days = [tier_for(b).review_every_days for b in balances]
    assert days == sorted(days, reverse=True)


def test_auto_trades_unlock_at_5k():
    assert not tier_for(4_999).auto_trades
    assert all(tier_for(b).auto_trades for b in (5_000, 20_000, 50_000, 100_000))
