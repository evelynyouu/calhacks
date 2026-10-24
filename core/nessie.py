"""Thin Nessie client: one shared key, timeouts, retries on network errors and 5xx.

There is deliberately no helper for DELETE /data: it wipes every team's data under the shared key.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "http://api.nessieisreal.com"
TIMEOUT = 10
RETRIES = 3


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


def request(method: str, path: str, body: Optional[dict] = None) -> Any:
    if path.rstrip("/") == "/data":
        raise ValueError("refusing to touch /data")
    for attempt in range(RETRIES):
        try:
            r = requests.request(method, f"{BASE}{path}", params={"key": _key()}, json=body, timeout=TIMEOUT)
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
        else:
            # Only GET is retried on 5xx: a POST may have been applied before the error.
            if r.status_code < 500 or method != "GET" or attempt == RETRIES - 1:
                break
        time.sleep(0.5 * 2 ** attempt)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text
        raise NessieError(method, path, r.status_code, detail)
    return r.json() if r.content else None


def _created(resp: dict) -> dict:
    return resp["objectCreated"]


# --- customers / accounts ---

def get_customer(customer_id: str) -> dict:
    return request("GET", f"/customers/{customer_id}")


def create_customer(first_name: str, last_name: str) -> dict:
    return _created(request("POST", "/customers", {
        "first_name": first_name, "last_name": last_name,
        "address": {"street_number": "1", "street_name": "Sproul Plaza", "city": "Berkeley", "state": "CA", "zip": "94720"},
    }))


def get_accounts(customer_id: str) -> list:
    return request("GET", f"/customers/{customer_id}/accounts")


def get_account(account_id: str) -> dict:
    return request("GET", f"/accounts/{account_id}")


def create_account(customer_id: str, type_: str, nickname: str, balance: float) -> dict:
    return _created(request("POST", f"/customers/{customer_id}/accounts", {
        "type": type_, "nickname": nickname, "rewards": 0, "balance": int(round(balance)),
    }))


def delete_account(account_id: str) -> None:
    request("DELETE", f"/accounts/{account_id}")


# --- transactions ---

def _txn(amount: float, on: str, description: str, **extra) -> dict:
    return {"medium": "balance", "transaction_date": on, "amount": round(amount, 2), "description": description, **extra}


def get_deposits(account_id: str) -> list:
    return request("GET", f"/accounts/{account_id}/deposits")


def create_deposit(account_id: str, amount: float, on: str, description: str) -> dict:
    return _created(request("POST", f"/accounts/{account_id}/deposits", _txn(amount, on, description)))


def get_withdrawals(account_id: str) -> list:
    return request("GET", f"/accounts/{account_id}/withdrawals")


def create_withdrawal(account_id: str, amount: float, on: str, description: str) -> dict:
    return _created(request("POST", f"/accounts/{account_id}/withdrawals", _txn(amount, on, description)))


def get_purchases(account_id: str) -> list:
    return request("GET", f"/accounts/{account_id}/purchases")


def create_purchase(account_id: str, merchant_id: str, amount: float, on: str, description: str) -> dict:
    body = _txn(amount, on, description, merchant_id=merchant_id)
    body["purchase_date"] = body.pop("transaction_date")
    return _created(request("POST", f"/accounts/{account_id}/purchases", body))


def get_transfers(account_id: str) -> list:
    return request("GET", f"/accounts/{account_id}/transfers")


def create_transfer(from_id: str, to_id: str, amount: float, on: str, description: str) -> dict:
    return _created(request("POST", f"/accounts/{from_id}/transfers", _txn(amount, on, description, payee_id=to_id)))


# --- bills / merchants ---

def get_bills(account_id: str) -> list:
    return request("GET", f"/accounts/{account_id}/bills")


def create_bill(account_id: str, payee: str, nickname: str, amount: float, recurring_day: int) -> dict:
    return _created(request("POST", f"/accounts/{account_id}/bills", {
        "status": "recurring", "payee": payee, "nickname": nickname,
        "payment_amount": round(amount, 2), "recurring_date": recurring_day,
    }))


def create_merchant(name: str, category: str) -> dict:
    return _created(request("POST", "/merchants", {
        "name": name, "category": category,
        "address": {"street_number": "1", "street_name": "Telegraph Ave", "city": "Berkeley", "state": "CA", "zip": "94704"},
        "geocode": {"lat": 37.87, "lng": -122.26},
    }))
