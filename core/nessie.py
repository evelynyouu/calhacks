"""Nessie API client.  OWNER: Evelyn.

Two halves:
- HTTP client (top): thin wrappers over https://api.nessieisreal.com. Used by seeding and,
  later, the live profile. One shared team key from .env.
- Demo stub (bottom): an in-memory copy of personas.json that the web layer calls today
  (deposit_paycheck, advance_days, ...). Those keep their signatures when they go live.

There is deliberately no helper for /data: DELETE /data wipes every team's data under the key.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv

from core import state

load_dotenv()

BASE = "https://api.nessieisreal.com"   # HTTPS only: plain HTTP (port 80) is refused
TIMEOUT = 10
RETRIES = 2                                     # extra attempts after the first
# Description prefixes for every transaction we write (see docs/EVELYN_API.md):
# [payroll] paycheck, [payroll-adj] raise, [event] life event, [firstfund] auto-invest,
# [spend] day-to-day spending, [bill] bill payment, [seed] history moves made by the seeder.
OUR_TAGS = ("[payroll", "[event", "[firstfund", "[spend", "[bill", "[seed")
IDS_PATH = Path(__file__).resolve().parent.parent / "data" / "nessie_ids.json"
LIVE = "live"


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


CACHE_SECONDS = 15   # GETs are reused this long; a write clears what it could have changed
_cache: dict = {}
_writes: dict = {}   # scope -> write count; a GET is cached only if its scope saw no write while it ran
_ALL = "*"           # the scope clear_cache() bumps
_lock = threading.Lock()
_SCOPE = re.compile(r"^/(accounts|customers)/([^/?]+)")


def _scope(path: str) -> str | None:
    """The cached lists a request to `path` reads or changes: one account's (or customer's)."""
    m = _SCOPE.match(path)
    return f"/{m.group(1)}/{m.group(2)}" if m else None


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _writes[_ALL] = _writes.get(_ALL, 0) + 1


def _evict(scope: str) -> None:
    with _lock:
        for key in [k for k in _cache if k[0] == scope or k[0].startswith(scope + "/")]:
            del _cache[key]
        _writes[scope] = _writes.get(scope, 0) + 1


def request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
    """One Nessie call. A live profile reads ~21 lists, and one button click rebuilds the profile
    several times, so GETs are cached briefly. Only this app writes these accounts, so evicting
    what a write touches keeps the cache exact. Writes are scoped to one account: a background
    rebuild writing to a spare persona doesn't evict the live persona's lists."""
    if path.split("?")[0].rstrip("/") == "/data":
        raise ValueError("refusing to touch /data")
    scope = _scope(path)
    if method != "GET":
        if scope:
            _evict(scope)
        try:
            return _send(method, path, body, params)
        finally:
            if scope:
                _evict(scope)   # again after: a GET that ran during the write may have read old data
    key = (path, tuple(sorted((params or {}).items())))
    with _lock:
        hit = _cache.get(key)
        started = (_writes.get(scope, 0), _writes.get(_ALL, 0))
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return copy.deepcopy(hit[1])
    result = _send(method, path, body, params)
    with _lock:
        if (_writes.get(scope, 0), _writes.get(_ALL, 0)) == started:
            _cache[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def _send(method: str, path: str, body: dict | None, params: dict | None) -> Any:
    """Retries connection errors and 5xx. A POST/DELETE is retried only if it never reached
    Nessie (connection failed), since a 5xx or read timeout may already have been applied."""
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

def whole(amount: float) -> float:
    """Nessie's transaction amounts are integers (whole dollars), so every amount we write is one.
    Both modes round with this, so fixture and live stay identical."""
    return float(round(amount))


def txn_date(txn: dict) -> str:
    """Nessie's docs date every transaction with transaction_date; older data used purchase_date."""
    return str(txn.get("transaction_date") or txn.get("purchase_date"))[:10]


# Write bodies follow what the real API accepts (measured 2026-10-24, scripts/nessie_spike.py):
# deposits/withdrawals take transaction_date; purchases take purchase_date and must carry a
# status (one without breaks reading the account's purchases); amounts are whole dollars.

def _txn(amount: float, on: str, description: str, date_field: str = "transaction_date", **extra) -> dict:
    return {"medium": "balance", date_field: on, "status": "completed", "amount": int(whole(amount)),
            "description": description, **extra}


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
    return _post(f"/accounts/{account_id}/purchases",
                 _txn(amount, on, description, date_field="purchase_date", merchant_id=merchant_id))


def get_transfers(account_id: str) -> list[dict]:
    """Nessie answers an empty transfer list with a 404, so that counts as no transfers."""
    try:
        return request("GET", f"/accounts/{account_id}/transfers")
    except NessieError as e:
        if e.status == 404:
            return []
        raise


def create_transfer(from_id: str, to_id: str, amount: float, on: str, description: str) -> dict:
    """Move money between two of the persona's accounts. Nessie's transfers have no destination
    field, so a move is a tagged withdrawal from one account plus a tagged deposit to the other.
    Returns the deposit."""
    create_withdrawal(from_id, amount, on, description)
    return create_deposit(to_id, amount, on, description)


# ---------- bills / merchants ----------

def get_bills(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/bills")


def create_bill(account_id: str, payee: str, nickname: str, amount: float, recurring_day: int,
                payment_date: str) -> dict:
    """payment_date is required in practice: a bill without one makes reading the account's bills fail."""
    return _post(f"/accounts/{account_id}/bills", {
        "status": "recurring", "payee": payee, "nickname": nickname,
        "payment_amount": round(amount, 2), "recurring_date": recurring_day, "payment_date": payment_date,
    })


def create_merchant(name: str, category: str) -> dict:
    return _post("/merchants", {
        "name": name, "category": category,
        "address": {"street_number": "1", "street_name": "Telegraph Ave", "city": "Berkeley", "state": "CA", "zip": "94704"},
        "geocode": {"lat": 37.87, "lng": -122.26},
    })


# ---------- ledger balance ----------

def is_ours(txn: dict) -> bool:
    return txn.get("status") != "cancelled" and str(txn.get("description", "")).startswith(OUR_TAGS)


def account_balance(account_id: str, seed_balance: float, liability: bool = False) -> float:
    """Balance from our own records: the balance we seeded plus every transaction we tagged.

    Nessie never applies transactions to its own `balance` field (confirmed against the real
    API: it stays at the opening value), so this is the balance. Cancelled transactions don't
    count. Moves between accounts are withdrawal + deposit pairs, so transfers aren't read.
    liability=True is for the credit card: purchases raise the amount owed, payments lower it.
    """
    def ours(txns: list[dict]) -> float:
        return sum(t["amount"] for t in txns if is_ours(t))

    net = (ours(get_deposits(account_id))
           - ours(get_withdrawals(account_id))
           - ours(get_purchases(account_id)))
    return round(seed_balance - net if liability else seed_balance + net, 2)


# ---------- Nessie ids (written by scripts/seed_nessie.py) ----------

def load_ids() -> dict:
    """{persona: {customer_id, accounts: {...}, seed: {...}}, "_merchants": {...}}"""
    try:
        return json.loads(IDS_PATH.read_text())
    except FileNotFoundError:
        return {}


def save_ids(ids: dict) -> None:
    IDS_PATH.write_text(json.dumps(ids, indent=2, sort_keys=True) + "\n")


def mode() -> str:
    """"live" or "fixture". Live drops to fixture for the rest of the run if Nessie goes down."""
    return "fixture" if _offline else os.environ.get("NESSIE_MODE", "fixture")


# ---------- Nessie going down mid-demo ----------
# On an outage (no connection, a timeout, or a 5xx from Nessie) the app switches to the in-memory
# demo data for the rest of the run, starting from the last live numbers each persona showed, so
# nothing on screen jumps and the button that hit the outage still completes. A 4xx is a bug in
# our request and is raised, not hidden. Restart the server to go back to live Nessie.

_offline: str | None = None
_offline_lock = threading.Lock()


def offline_reason() -> str | None:
    """Why the app fell back to demo data, or None while it's live (or was always fixture)."""
    return _offline


def is_outage(e: BaseException) -> bool:
    return isinstance(e, requests.RequestException) or (isinstance(e, NessieError) and e.status >= 500)


def go_offline(reason: str) -> None:
    global _offline
    with _offline_lock:
        if _offline:
            return
        from core import profile   # lazily: core.profile imports this module
        for cid in _SEED:
            snap = profile.cached_snapshot(cid)
            _STATE[cid] = _state_from_snapshot(cid, *snap) if snap else copy.deepcopy(_SEED[cid])
        _offline = reason


def go_online() -> None:
    global _offline
    _offline = None


def _state_from_snapshot(cid: str, p, as_of) -> dict:
    """Fixture state that continues from a live profile (what the screen last showed)."""
    c = copy.deepcopy(_SEED[cid])
    c.update(monthly_income=p.monthly_income, monthly_bills=p.monthly_bills, monthly_spending=p.monthly_spending,
             checking_balance=p.checking_balance, cash_cushion=p.cash_cushion, card_debt=p.card_debt,
             card_apr=p.card_apr, invested_balance=p.invested_balance, paycheck_amount=p.paycheck_amount,
             days_to_payday=p.days_to_payday, as_of=as_of.isoformat(),
             upcoming_bills=[{"name": b.name, "amount": b.amount, "due_in_days": b.due_in_days}
                             for b in p.upcoming_bills])
    return c


def _try_live(name: str, *args):
    """(True, result) from the live implementation, or (False, None) after an outage so the
    caller runs the fixture path instead."""
    try:
        return True, getattr(_live(), name)(*args)
    except Exception as e:
        if not is_outage(e):
            raise
        go_offline(f"{type(e).__name__}: {e}")
        return False, None


# ---------- demo stub (in-memory; what the web layer calls today) ----------

_PERSONAS_PATH = Path(__file__).resolve().parent.parent / "data" / "personas.json"
OTHER_BILLS = "Other bills"
OTHER_BILLS_DUE_IN = 29   # days from SIM_START; outside every persona's first payday window


def _with_other_bills(p: dict) -> dict:
    """personas.json lists the bills that matter for payday, but monthly_bills can be larger.
    The remainder becomes one "Other bills" line so bills paid over time match monthly_bills."""
    rest = round(p["monthly_bills"] - sum(b["amount"] for b in p["upcoming_bills"]), 2)
    if rest > 0:
        p["upcoming_bills"].append({"name": OTHER_BILLS, "amount": rest, "due_in_days": OTHER_BILLS_DUE_IN})
    # The stub keeps its own sim clock, and each bill's day of the month, so bills recur by the
    # calendar exactly as they do in live mode (Nessie bills have a recurring_date, not an interval).
    p["as_of"] = state.SIM_START.isoformat()
    p["bill_days"] = {b["name"]: (state.SIM_START + timedelta(days=b["due_in_days"])).day for b in p["upcoming_bills"]}
    return p


_SEED: dict = {cid: _with_other_bills(p) for cid, p in json.loads(_PERSONAS_PATH.read_text()).items()}
_STATE: dict = copy.deepcopy(_SEED)


def persona_seed(customer_id: str) -> dict:
    """The persona's starting numbers (personas.json + Other bills). Seeding and live mode read this."""
    return copy.deepcopy(_SEED[customer_id])


def _live():
    from core import live   # imported lazily: core.live imports core.profile, which imports this module
    return live


def list_customers() -> list[dict]:
    return [{"id": cid, "name": p["name"], "blurb": p["blurb"]} for cid, p in _STATE.items()]


def get_customer_raw(customer_id: str) -> dict:
    if customer_id not in _STATE:
        raise KeyError(customer_id)
    return _STATE[customer_id]


def reset(customer_id: str | None = None) -> None:
    """Back to the starting numbers. Live mode re-seeds the persona's Nessie accounts."""
    if mode() == LIVE:
        done, value = _try_live("reset", customer_id)
        if done:
            return value
    if customer_id is None:
        _STATE.clear()
        _STATE.update(copy.deepcopy(_SEED))
    else:
        _STATE[customer_id] = copy.deepcopy(_SEED[customer_id])


def transfer_to_investing(customer_id: str, amount: float) -> str:
    """Checking -> Investing account. Returns a transfer id."""
    if mode() == LIVE:
        done, value = _try_live("transfer_to_investing", customer_id, amount)
        if done:
            return value
    c = _STATE[customer_id]
    c["checking_balance"] = round(c["checking_balance"] - amount, 2)
    c["invested_balance"] = round(c["invested_balance"] + amount, 2)
    return f"stub-xfer-{uuid.uuid4().hex[:8]}"


def deposit_paycheck(customer_id: str) -> float:
    if mode() == LIVE:
        done, value = _try_live("deposit_paycheck", customer_id)
        if done:
            return value
    c = _STATE[customer_id]
    c["checking_balance"] = round(c["checking_balance"] + c["paycheck_amount"], 2)
    c["days_to_payday"] = 14
    return c["paycheck_amount"]


def advance_days(customer_id: str, days: int) -> None:
    """Move the demo clock forward: day-to-day spending and bills that come due leave checking."""
    if mode() == LIVE:
        done, value = _try_live("advance_days", customer_id, days)
        if done:
            return value
    c = _STATE[customer_id]
    start = date.fromisoformat(c["as_of"])
    end = start + timedelta(days=days)
    c["as_of"] = end.isoformat()
    c["days_to_payday"] = max(0, c["days_to_payday"] - days)
    spend = whole(c["monthly_spending"] * days / 30)
    c["checking_balance"] = round(c["checking_balance"] - spend, 2)
    for b in c["upcoming_bills"]:
        day = c["bill_days"][b["name"]]
        for _ in state.occurrences(day, start, end):   # each due date we passed is paid
            c["checking_balance"] = round(c["checking_balance"] - b["amount"], 2)
        b["due_in_days"] = (state.next_due(day, end) - end).days


def record_purchase(customer_id: str, name: str, amount: float) -> None:
    """Life event, e.g. 'Unexpected $600 car repair'. Savings covers it; anything savings
    can't cover goes on the credit card. Checking (earmarked for bills) is left alone."""
    if mode() == LIVE:
        done, value = _try_live("record_purchase", customer_id, name, amount)
        if done:
            return value
    c = _STATE[customer_id]
    amount = whole(amount)
    from_savings = whole(min(amount, max(0.0, c["cash_cushion"])))
    c["cash_cushion"] = round(c["cash_cushion"] - from_savings, 2)
    c["card_debt"] = round(c["card_debt"] + amount - from_savings, 2)


def raise_paycheck(customer_id: str, amount: float) -> None:
    """Paychecks go up by `amount` from now on, and the first raise lands in checking now."""
    amount = whole(amount)
    if mode() == LIVE:
        done, value = _try_live("raise_paycheck", customer_id, amount)
        if done:
            return value
    c = _STATE[customer_id]
    c["paycheck_amount"] = round(c["paycheck_amount"] + amount, 2)
    c["monthly_income"] = round(c["monthly_income"] + 2 * amount, 2)   # two paychecks a month
    c["checking_balance"] = round(c["checking_balance"] + amount, 2)


def apply_raise(customer_id: str, pct: float) -> None:
    done, paycheck = _try_live("current_paycheck", customer_id) if mode() == LIVE else (False, None)
    if not done:
        paycheck = _STATE[customer_id]["paycheck_amount"]
    raise_paycheck(customer_id, round(paycheck * pct / 100, 2))


def apply_market_move(customer_id: str, pct: float) -> None:
    if mode() == LIVE:
        done, value = _try_live("apply_market_move", customer_id, pct)
        if done:
            return value
    c = _STATE[customer_id]
    change = whole(c["invested_balance"] * pct / 100)
    c["invested_balance"] = round(c["invested_balance"] + change, 2)
