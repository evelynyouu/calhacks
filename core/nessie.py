"""Nessie API client.  OWNER: Evelyn.

STUB: an in-memory copy of personas.json stands in for Nessie so the site
works end to end. Replace each function body with real Nessie calls; keep
the signatures so the web layer doesn't change.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

_PERSONAS_PATH = Path(__file__).resolve().parent.parent / "data" / "personas.json"
_SEED: dict = json.loads(_PERSONAS_PATH.read_text())
_STATE: dict = copy.deepcopy(_SEED)


def list_customers() -> list[dict]:
    return [{"id": cid, "name": p["name"], "blurb": p["blurb"]} for cid, p in _STATE.items()]


def get_customer_raw(customer_id: str) -> dict:
    if customer_id not in _STATE:
        raise KeyError(customer_id)
    return _STATE[customer_id]


def reset(customer_id: str | None = None) -> None:
    if customer_id is None:
        _STATE.clear()
        _STATE.update(copy.deepcopy(_SEED))
    else:
        _STATE[customer_id] = copy.deepcopy(_SEED[customer_id])


def transfer_to_investing(customer_id: str, amount: float) -> str:
    """Checking -> Investing account. Returns a transfer id."""
    c = _STATE[customer_id]
    c["checking_balance"] -= amount
    c["invested_balance"] += amount
    return f"stub-xfer-{uuid.uuid4().hex[:8]}"


def deposit_paycheck(customer_id: str) -> float:
    c = _STATE[customer_id]
    c["checking_balance"] += c["paycheck_amount"]
    c["days_to_payday"] = 14
    return c["paycheck_amount"]


def advance_days(customer_id: str, days: int) -> None:
    c = _STATE[customer_id]
    c["days_to_payday"] = max(0, c["days_to_payday"] - days)
    for b in c["upcoming_bills"]:
        b["due_in_days"] = max(0, b["due_in_days"] - days)


def record_purchase(customer_id: str, name: str, amount: float) -> None:
    """Life event, e.g. 'Unexpected $600 car repair'. Pulls from checking, then cushion."""
    c = _STATE[customer_id]
    from_checking = min(amount, max(0.0, c["checking_balance"]))
    c["checking_balance"] -= from_checking
    c["cash_cushion"] -= amount - from_checking


def apply_raise(customer_id: str, pct: float) -> None:
    c = _STATE[customer_id]
    c["monthly_income"] = round(c["monthly_income"] * (1 + pct / 100), 2)
    c["paycheck_amount"] = round(c["paycheck_amount"] * (1 + pct / 100), 2)


def apply_market_move(customer_id: str, pct: float) -> None:
    c = _STATE[customer_id]
    c["invested_balance"] = round(c["invested_balance"] * (1 + pct / 100), 2)
