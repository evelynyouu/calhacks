"""Nessie API client.  OWNER: Evelyn.

Two halves:
- HTTP client (top): thin wrappers over http://api.nessieisreal.com. Used by seeding and,
  later, the live profile. One shared team key from .env.
- Demo stub (bottom): an in-memory copy of personas.json that the web layer calls today
  (deposit_paycheck, advance_days, ...). Those keep their signatures when they go live.

There is deliberately no helper for /data: DELETE /data wipes every team's data under the key.
"""
from __future__ import annotations

import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "http://api.nessieisreal.com"
TIMEOUT = 10
RETRIES = 2                                     # extra attempts after the first
OUR_TAGS = ("[payroll", "[event", "[firstfund")  # description prefixes for transactions we write


class NessieError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: Any):
        super().__init__(f"{method} {path} -> {status}: {body}")
        self.status = status
        self.body = body


def _key() -> str:
    key = os.environ.get("NESSIE_API_KEY")
    if not key:
        raise RuntimeError("NESSIE_API_KEY missing: put it in .env")
    return key


def request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
    """Retries connection errors and 5xx. A POST/DELETE is retried only if it never reached
    Nessie (connection failed), since a 5xx or read timeout may already have been applied."""
    if path.split("?")[0].rstrip("/") == "/data":
        raise ValueError("refusing to touch /data")
    query = {"key": _key(), **(params or {})}
    for attempt in range(RETRIES + 1):
        try:
            r = requests.request(method, f"{BASE}{path}", params=query, json=body, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            safe = method == "GET" or isinstance(e, requests.ConnectionError)
            if attempt == RETRIES or not safe:
                raise
        else:
            if r.status_code < 500 or method != "GET" or attempt == RETRIES:
                break
        time.sleep(0.5 * 2 ** attempt)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text
        raise NessieError(method, path, r.status_code, detail)
    return r.json() if r.content else None


def _post(path: str, body: dict) -> dict:
    return request("POST", path, body)["objectCreated"]


# ---------- customers / accounts ----------

def get_customer(customer_id: str) -> dict:
    return request("GET", f"/customers/{customer_id}")


def create_customer(first_name: str, last_name: str) -> dict:
    return _post("/customers", {
        "first_name": first_name, "last_name": last_name,
        "address": {"street_number": "1", "street_name": "Sproul Plaza", "city": "Berkeley", "state": "CA", "zip": "94720"},
    })


def get_accounts(customer_id: str) -> list[dict]:
    return request("GET", f"/customers/{customer_id}/accounts")


def get_account(account_id: str) -> dict:
    return request("GET", f"/accounts/{account_id}")


def create_account(customer_id: str, type_: str, nickname: str, balance: float) -> dict:
    """type_: "Checking" | "Savings" | "Credit Card". Nessie balances are whole dollars."""
    return _post(f"/customers/{customer_id}/accounts",
                 {"type": type_, "nickname": nickname, "rewards": 0, "balance": int(round(balance))})


def delete_account(account_id: str) -> None:
    request("DELETE", f"/accounts/{account_id}")


# ---------- transactions ----------

def _txn(amount: float, on: str, description: str, **extra) -> dict:
    return {"medium": "balance", "transaction_date": on, "amount": round(amount, 2), "description": description, **extra}


def get_deposits(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/deposits")


def create_deposit(account_id: str, amount: float, on: str, description: str) -> dict:
    return _post(f"/accounts/{account_id}/deposits", _txn(amount, on, description))


def get_withdrawals(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/withdrawals")


def create_withdrawal(account_id: str, amount: float, on: str, description: str) -> dict:
    return _post(f"/accounts/{account_id}/withdrawals", _txn(amount, on, description))


def get_purchases(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/purchases")


def create_purchase(account_id: str, merchant_id: str, amount: float, on: str, description: str) -> dict:
    body = _txn(amount, on, description, merchant_id=merchant_id)
    body["purchase_date"] = body.pop("transaction_date")
    return _post(f"/accounts/{account_id}/purchases", body)


def get_transfers(account_id: str, role: str | None = None) -> list[dict]:
    """role: "payer" (money out), "payee" (money in), or None for Nessie's default."""
    return request("GET", f"/accounts/{account_id}/transfers", params={"type": role} if role else None)


def create_transfer(from_id: str, to_id: str, amount: float, on: str, description: str) -> dict:
    return _post(f"/accounts/{from_id}/transfers", _txn(amount, on, description, payee_id=to_id))


# ---------- bills / merchants ----------

def get_bills(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/bills")


def create_bill(account_id: str, payee: str, nickname: str, amount: float, recurring_day: int) -> dict:
    return _post(f"/accounts/{account_id}/bills", {
        "status": "recurring", "payee": payee, "nickname": nickname,
        "payment_amount": round(amount, 2), "recurring_date": recurring_day,
    })


def create_merchant(name: str, category: str) -> dict:
    return _post("/merchants", {
        "name": name, "category": category,
        "address": {"street_number": "1", "street_name": "Telegraph Ave", "city": "Berkeley", "state": "CA", "zip": "94704"},
        "geocode": {"lat": 37.87, "lng": -122.26},
    })


# ---------- ledger balance ----------

def account_balance(account_id: str, seed_balance: float) -> float:
    """Balance from our own records: the balance we seeded plus every transaction we tagged.

    Used instead of Nessie's `balance` field, which may lag or ignore pending transactions
    (the spike hasn't confirmed either way). Cancelled transactions don't count.
    """
    def ours(txns: list[dict]) -> float:
        return sum(t["amount"] for t in txns
                   if t.get("status") != "cancelled" and str(t.get("description", "")).startswith(OUR_TAGS))

    total = (seed_balance
             + ours(get_deposits(account_id))
             - ours(get_withdrawals(account_id))
             - ours(get_purchases(account_id))
             - ours(get_transfers(account_id, role="payer"))
             + ours(get_transfers(account_id, role="payee")))
    return round(total, 2)


# ---------- demo stub (in-memory; what the web layer calls today) ----------

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
    """Move the demo clock forward: day-to-day spending and bills that come due leave checking."""
    c = _STATE[customer_id]
    c["days_to_payday"] = max(0, c["days_to_payday"] - days)
    c["checking_balance"] = round(c["checking_balance"] - c["monthly_spending"] * days / 30, 2)
    for b in c["upcoming_bills"]:
        b["due_in_days"] -= days
        while b["due_in_days"] < 0:   # paid; recurs monthly
            c["checking_balance"] = round(c["checking_balance"] - b["amount"], 2)
            b["due_in_days"] += 30


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
