"""Shared data structures. Persons 2/3/4 all code against these; change only after telling the team.

Money is float dollars rounded to 2 places. Dates come from the sim clock (`as_of`), never date.today().
"""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel

BillCategory = Literal["rent", "utility", "phone", "internet", "transit", "subscription", "other"]
PayFrequency = Literal["biweekly", "semimonthly", "monthly"]
LifeEvent = Literal["car_repair", "raise", "market_drop"]


class UpcomingBill(BaseModel):
    name: str
    category: BillCategory
    amount: float
    due_date: date
    account_id: str


class FinancialProfile(BaseModel):
    persona: str
    customer_id: str
    name: str
    as_of: date

    checking_balance: float
    savings_balance: float
    investing_balance: float  # market value of the FirstFund Investing account
    card_balance: float
    card_apr: float  # 0.2499 = 24.99%

    paycheck_amount: float
    pay_frequency: PayFrequency
    last_payday: date
    next_payday: date
    monthly_income: float

    monthly_bills: float
    monthly_spending: float
    monthly_expenses: float
    monthly_surplus: float

    upcoming_bills: List[UpcomingBill]
    cushion_cash: float
    cushion_months: float


class PersonaSummary(BaseModel):
    persona: str
    name: str
    tagline: str


class PaydayDecision(BaseModel):
    invest: float
    planned: float
    held_back: float
    bills_due: List[UpcomingBill]
    spend_reserved: float
    buffer: float
    available: float
    reason: Literal["full", "reduced_for_bills", "skipped"]


class PaydayResult(BaseModel):
    decision: PaydayDecision
    transfer_id: Optional[str] = None
    order: Optional[dict] = None  # whatever broker.buy_underweight returns (Person 3)
    profile: FinancialProfile
