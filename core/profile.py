"""Raw Nessie data -> FinancialProfile.  OWNER: Evelyn.

STUB: reads the in-memory persona. Real version derives income/bills/spending
from Nessie deposits and purchases.
"""
from __future__ import annotations

from core import nessie
from core.models import Bill, FinancialProfile


def get_profile(customer_id: str) -> FinancialProfile:
    c = nessie.get_customer_raw(customer_id)
    return FinancialProfile(
        customer_id=customer_id,
        name=c["name"],
        monthly_income=c["monthly_income"],
        monthly_bills=c["monthly_bills"],
        monthly_spending=c["monthly_spending"],
        checking_balance=c["checking_balance"],
        cash_cushion=c["cash_cushion"],
        card_debt=c["card_debt"],
        card_apr=c["card_apr"],
        invested_balance=c["invested_balance"],
        paycheck_amount=c["paycheck_amount"],
        days_to_payday=c["days_to_payday"],
        upcoming_bills=[Bill(**b) for b in c["upcoming_bills"]],
    )
