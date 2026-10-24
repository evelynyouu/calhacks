"""Builds a FinancialProfile. profile.py only measures; readiness.py decides.

NESSIE_MODE=fixture (default) reads data/personas.json, no network.
NESSIE_MODE=live reads Nessie (not wired yet).
"""
from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from core.models import FinancialProfile, PayFrequency, PersonaSummary, UpcomingBill

PERSONAS_PATH = Path(__file__).resolve().parent.parent / "data" / "personas.json"

CHECKING_BUFFER = 200.0  # checking we never count as spare or investable
DAYS_PER_MONTH = 30  # prorating convention shared with payday.py
UPCOMING_WINDOW_DAYS = 35
PERIODS_PER_YEAR = {"biweekly": 26, "semimonthly": 24, "monthly": 12}


def _load_personas() -> dict:
    return json.loads(PERSONAS_PATH.read_text())


def list_personas() -> List[PersonaSummary]:
    data = _load_personas()["personas"]
    return [PersonaSummary(persona=k, name=v["name"], tagline=v["tagline"]) for k, v in data.items()]


def sim_start() -> date:
    return date.fromisoformat(_load_personas()["sim_start"])


def get_profile(persona: str, as_of: Optional[date] = None) -> FinancialProfile:
    mode = os.environ.get("NESSIE_MODE", "fixture")
    if mode == "live":
        raise NotImplementedError("live Nessie profile not wired yet; use NESSIE_MODE=fixture")
    return _fixture_profile(persona, as_of or sim_start())


def _fixture_profile(persona: str, as_of: date) -> FinancialProfile:
    p = _load_personas()["personas"].get(persona)
    if p is None:
        raise KeyError(f"unknown persona {persona!r}")
    b = p["balances"]
    bills = [
        UpcomingBill(
            name=bill["name"],
            category=bill["category"],
            amount=bill["amount"],
            due_date=next_due_date(bill["due_day"], as_of),
            account_id=f"fixture-{persona}-checking",
        )
        for bill in p["bills"]
    ]
    return build_profile(
        persona=persona,
        customer_id=f"fixture-{persona}",
        name=p["name"],
        as_of=as_of,
        checking=b["checking"],
        savings=b["savings"],
        investing=b["investing"],
        card=b["card"],
        card_apr=p["card_apr"],
        paycheck=p["paycheck"],
        pay_frequency=p["pay_frequency"],
        last_payday=date.fromisoformat(p["last_payday"]),
        bills=bills,
        monthly_spending=p["monthly_spending"],
    )


def build_profile(
    *,
    persona: str,
    customer_id: str,
    name: str,
    as_of: date,
    checking: float,
    savings: float,
    investing: float,
    card: float,
    card_apr: float,
    paycheck: float,
    pay_frequency: PayFrequency,
    last_payday: date,
    bills: Iterable[UpcomingBill],
    monthly_spending: float,
) -> FinancialProfile:
    """Pure derivation shared by fixture and live modes. `bills` = every recurring bill with its next due date."""
    bills = sorted(bills, key=lambda x: x.due_date)
    next_pay = next_payday(last_payday, pay_frequency)
    while next_pay <= as_of:
        next_pay = next_payday(next_pay, pay_frequency)

    monthly_income = paycheck * PERIODS_PER_YEAR[pay_frequency] / 12
    monthly_bills = sum(x.amount for x in bills)
    monthly_expenses = monthly_bills + monthly_spending

    spare_checking = max(0.0, checking - obligations_until(as_of, next_pay, bills, monthly_spending) - CHECKING_BUFFER)
    cushion_cash = savings + spare_checking
    cushion_months = cushion_cash / monthly_expenses if monthly_expenses > 0 else 0.0

    horizon = as_of + timedelta(days=UPCOMING_WINDOW_DAYS)
    return FinancialProfile(
        persona=persona,
        customer_id=customer_id,
        name=name,
        as_of=as_of,
        checking_balance=round(checking, 2),
        savings_balance=round(savings, 2),
        investing_balance=round(investing, 2),
        card_balance=round(card, 2),
        card_apr=card_apr,
        paycheck_amount=round(paycheck, 2),
        pay_frequency=pay_frequency,
        last_payday=last_payday,
        next_payday=next_pay,
        monthly_income=round(monthly_income, 2),
        monthly_bills=round(monthly_bills, 2),
        monthly_spending=round(monthly_spending, 2),
        monthly_expenses=round(monthly_expenses, 2),
        monthly_surplus=round(monthly_income - monthly_expenses, 2),
        upcoming_bills=[x for x in bills if x.due_date <= horizon],
        cushion_cash=round(cushion_cash, 2),
        cushion_months=round(cushion_months, 2),
    )


def obligations_until(as_of: date, until: date, bills: Iterable[UpcomingBill], monthly_spending: float) -> float:
    """Bills due in [as_of, until] (inclusive) plus spending prorated over those days."""
    due = sum(x.amount for x in bills if as_of <= x.due_date <= until)
    return due + monthly_spending * (until - as_of).days / DAYS_PER_MONTH


def next_due_date(due_day: int, as_of: date) -> date:
    """Next date on/after as_of falling on due_day (clamped to month length)."""
    year, month = as_of.year, as_of.month
    for _ in range(2):
        day = min(due_day, calendar.monthrange(year, month)[1])
        candidate = date(year, month, day)
        if candidate >= as_of:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise AssertionError("unreachable")


def next_payday(last: date, freq: PayFrequency) -> date:
    if freq == "biweekly":
        return last + timedelta(days=14)
    if freq == "semimonthly":  # 1st and 15th
        if last.day < 15:
            return last.replace(day=15)
        return date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    year, month = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
    return date(year, month, min(last.day, calendar.monthrange(year, month)[1]))


if __name__ == "__main__":
    for key in sys.argv[1:] or [p.persona for p in list_personas()]:
        print(get_profile(key).model_dump_json(indent=2))
