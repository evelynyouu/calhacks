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
from urllib3.exceptions import MaxRetryError, NewConnectionError
from dotenv import load_dotenv

from core import state

load_dotenv()

BASE = "https://api.nessieisreal.com"   # HTTPS only: plain HTTP (port 80) is refused
TIMEOUT = (3, 6)   # (connect, read) seconds: a hanging Nessie should hit the fallback in seconds
RETRIES = 3   # extra attempts after the first (backoff 0.5 + 1 + 2 s); real Nessie sometimes refuses connections briefly
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


def _redacted(e: requests.RequestException) -> requests.RequestException:
    """The same error with the API key taken out: requests puts the full URL (?key=...) in its
    messages, and those reach logs and tracebacks."""
    key = os.environ.get("NESSIE_API_KEY") or "\x00"
    return type(e)(str(e).replace(key, "***"))


_local = threading.local()


def _session() -> requests.Session:
    """One pooled connection per thread. Without it every call opened a new HTTPS connection (a TLS
    handshake each, ~20 at once per page), and real Nessie sometimes refused those bursts."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _http(method: str, url: str, **kwargs) -> requests.Response:
    """The one place HTTP happens (tests replace this)."""
    return _session().request(method, url, **kwargs)


def _never_sent(e: requests.ConnectionError) -> bool:
    """True when the connection was refused before the request went out (safe to send again).
    A connection that dropped after sending looks like a ConnectionError too, but Nessie may
    already have applied that write."""
    reason = e.args[0] if e.args else None
    if isinstance(reason, MaxRetryError):
        reason = reason.reason
    return isinstance(reason, NewConnectionError)


def _send(method: str, path: str, body: dict | None, params: dict | None) -> Any:
    """Retries refused connections (any method), and dropped connections and 5xx for GETs only:
    a write that may have reached Nessie is never sent twice. Timeouts aren't retried, so a
    hanging Nessie falls back to demo data quickly. Raised errors never contain the API key."""
    query = {"key": _key(), **(params or {})}
    error = None
    for attempt in range(RETRIES + 1):
        try:
            r = _http(method, f"{BASE}{path}", params=query, json=body, timeout=TIMEOUT)
        except requests.Timeout as e:
            error = e
            break
        except requests.ConnectionError as e:
            if attempt == RETRIES or not (method == "GET" or _never_sent(e)):
                error = e
                break
        else:
            if r.status_code < 500 or method != "GET" or attempt == RETRIES:
                break
        time.sleep(0.5 * 2 ** attempt)
    if error is not None:
        raise _redacted(error)   # raised outside the except block, so no context chain holds the key
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
    """Atomic: background rebuilds save while pages read, so a reader must never see half a file."""
    tmp = IDS_PATH.with_name(f"{IDS_PATH.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(ids, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, IDS_PATH)


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
# While live, _SHADOW holds a demo-data copy of each persona kept in step with Nessie: refreshed
# from every successful live read (core.profile calls sync_shadow) and with every successful live
# click applied to it too. If Nessie drops out, the app switches to it, so the screen continues
# from exactly what it last showed (including the click that hit the outage) with no jump.
_SHADOW: dict = {}


def offline_reason() -> str | None:
    """Why the app fell back to demo data, or None while it's live (or was always fixture)."""
    return _offline


class StaleIds(Exception):
    """data/nessie_ids.json points at accounts that no longer exist (e.g. Render restored an old
    copy after a reset deleted them). Falls back to demo data instead of a 500."""


def is_outage(e: BaseException) -> bool:
    return isinstance(e, requests.RequestException) or (isinstance(e, NessieError) and e.status >= 500)


def needs_fallback(e: BaseException) -> bool:
    """Outages, and ids that point at accounts Nessie no longer has. Other 4xx are our bugs: raise."""
    return is_outage(e) or isinstance(e, StaleIds) or (isinstance(e, NessieError) and e.status == 404)


def describe_failure(e: BaseException) -> str:
    """A short, safe reason for the badge and logs. Never str(e): requests errors carry the URL,
    and the URL carries the API key."""
    if isinstance(e, StaleIds) or (isinstance(e, NessieError) and e.status == 404):
        return "Nessie ids are out of date (accounts not found); re-seed or reset this persona"
    if isinstance(e, NessieError):
        return f"Nessie answered {e.status}"
    if isinstance(e, requests.Timeout):
        return "Nessie timed out"
    if isinstance(e, requests.ConnectionError):
        return "couldn't reach Nessie"
    return f"Nessie request failed ({type(e).__name__})"


def go_offline(reason: str) -> None:
    global _offline
    with _offline_lock:
        if _offline:
            return
        from core import profile   # lazily: core.profile imports this module
        for cid in _SEED:
            if cid not in _SHADOW:   # never read live in this run: last cached live numbers, else the start
                try:
                    snap = profile.cached_snapshot(cid)
                    _SHADOW[cid] = _state_from_snapshot(cid, *snap) if snap else copy.deepcopy(_SEED[cid])
                except Exception:   # a bad cache must never stop the fallback
                    _SHADOW[cid] = copy.deepcopy(_SEED[cid])
        _offline = reason


def go_online() -> None:
    """Back to live (tests; the app itself goes back to live on restart)."""
    global _offline
    _offline = None
    _SHADOW.clear()


def sync_shadow(cid: str, p, as_of) -> None:
    """Called after each successful live read: the fallback copy becomes exactly what's on screen."""
    if not _offline:
        old = _SHADOW.get(cid, {})
        _SHADOW[cid] = _state_from_snapshot(cid, p, as_of)
        try:
            _SHADOW[cid]["activity"] = _live().activity(cid)   # the lists were just read, so this is cached
        except Exception:
            if "activity" in old:
                _SHADOW[cid]["activity"] = old["activity"]


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
    """(True, result) from the live implementation, or (False, None) after a failure that needs
    the fallback, so the caller runs the demo-data path instead."""
    try:
        return True, getattr(_live(), name)(*args)
    except Exception as e:
        if not needs_fallback(e):
            raise
        go_offline(describe_failure(e))
        return False, None


def _configured_live() -> bool:
    return os.environ.get("NESSIE_MODE", "fixture") == LIVE


def _demo(name: str, fx, customer_id, *args):
    """Run a demo button. Fixture mode: on _STATE. Live: on Nessie, with the same effect applied to
    the shadow copy; if Nessie fails (now or earlier in this run), on the shadow copy instead."""
    if not _configured_live():
        return fx(_STATE, customer_id, *args)
    if not _offline:
        done, value = _try_live(name, customer_id, *args)
        if done:
            for cid in ([customer_id] if customer_id else list(_SHADOW)):
                if cid in _SHADOW:
                    fx(_SHADOW, cid, *args)   # keep the fallback copy in step
            return value
    return fx(_SHADOW, customer_id, *args)


def _store() -> dict:
    return _SHADOW if _offline else _STATE


# ---------- bank activity feed ----------

ACCOUNT_NAMES = {"checking": "Checking", "savings": "Savings", "investing": "Investing", "card": "Card"}


def activity_item(on: str, description: str, amount: float, flow: str, account: str, to: str | None = None) -> dict:
    """One feed line. `description` is the tagged Nessie description, e.g. "[payroll] paycheck".
    flow: "in" (money arrives), "out" (money leaves), "move" (between the persona's own accounts)."""
    tag, _, text = description.partition("] ")
    return {"on": str(on)[:10], "tag": tag.lstrip("["), "text": text, "amount": round(amount, 2), "flow": flow,
            "account": ACCOUNT_NAMES[account], "to": ACCOUNT_NAMES[to] if to else None}


# Within one day, the order things happen in the demo: the clock runs (spending, bills), the
# paycheck lands, then auto-invest, then whatever button was pressed. Newest-first puts events on top.
_SAME_DAY_ORDER = {"seed": 0, "spend": 1, "bill": 2, "payroll": 3, "payroll-adj": 4, "firstfund": 5, "event": 6}


def _feed_order(item: dict) -> tuple:
    return (item["on"], _SAME_DAY_ORDER.get(item["tag"], 9), item["text"], item["amount"], item["flow"], item["account"])


def recent_activity(customer_id: str, limit: int | None = 8) -> list[dict]:
    """The transactions FirstFund wrote for this persona, newest first: what the bank actually shows."""
    items = None
    if _configured_live() and not _offline:
        try:
            items = _live().activity(customer_id)
        except Exception as e:
            if not needs_fallback(e):
                raise
            go_offline(describe_failure(e))
    if items is None:
        items = list(_activity(_store(), customer_id))
    items.sort(key=_feed_order, reverse=True)
    return items if limit is None else items[:limit]


def _activity(store: dict, customer_id: str) -> list[dict]:
    """A demo-data persona's transaction log, starting with the same history the seeder writes."""
    c = store[customer_id]
    if "activity" not in c:
        from core import seed   # lazily: core.seed imports this module
        plan = seed.plan_persona(customer_id)
        log = [activity_item(d.isoformat(), "[payroll] paycheck", plan["persona"]["paycheck_amount"], "in", "checking")
               for d in plan["paydays"]]
        log += [activity_item(x["on"].isoformat(), f"[spend] {seed.MERCHANTS[x['merchant']][0]}", x["amount"], "out",
                              "checking") for x in plan["purchases"]]
        log += [activity_item(x["on"].isoformat(), f"[bill] {x['name']}", x["amount"], "out", "checking")
                for x in plan["bill_payments"]]
        t = plan["to_savings"]
        if t:
            src, dst = ("checking", "savings") if t > 0 else ("savings", "checking")
            log.append(activity_item(plan["paydays"][-1].isoformat(), "[seed] move leftover to savings", abs(t), "move",
                                     src, dst))
        c["activity"] = log
    return c["activity"]


def _log(store: dict, customer_id: str, description: str, amount: float, flow: str, account: str,
         to: str | None = None, on: str | None = None) -> None:
    _activity(store, customer_id).append(
        activity_item(on or store[customer_id]["as_of"], description, amount, flow, account, to))


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
    store = _store()
    if customer_id not in store:
        raise KeyError(customer_id)
    return store[customer_id]


# Each demo button: a public function (what the routes call) and its demo-data effect (_fx_*),
# which _demo runs on _STATE, or on the shadow copy alongside or instead of live Nessie.

def reset(customer_id: str | None = None) -> None:
    """Back to the starting numbers. Live mode swaps in the persona's spare Nessie copy."""
    return _demo("reset", _fx_reset, customer_id)


def _fx_reset(store: dict, customer_id: str | None) -> None:
    for cid in [customer_id] if customer_id else list(_SEED):
        store[cid] = copy.deepcopy(_SEED[cid])


def transfer_to_investing(customer_id: str, amount: float) -> str:
    """Checking -> Investing account. Returns a transfer id."""
    return _demo("transfer_to_investing", _fx_transfer_to_investing, customer_id, amount)


def _fx_transfer_to_investing(store: dict, customer_id: str, amount: float) -> str:
    c = store[customer_id]
    c["checking_balance"] = round(c["checking_balance"] - amount, 2)
    c["invested_balance"] = round(c["invested_balance"] + amount, 2)
    _log(store, customer_id, "[firstfund] payday auto-invest", amount, "move", "checking", "investing")
    return f"stub-xfer-{uuid.uuid4().hex[:8]}"


def deposit_paycheck(customer_id: str) -> float:
    return _demo("deposit_paycheck", _fx_deposit_paycheck, customer_id)


def _fx_deposit_paycheck(store: dict, customer_id: str) -> float:
    c = store[customer_id]
    c["checking_balance"] = round(c["checking_balance"] + c["paycheck_amount"], 2)
    c["days_to_payday"] = 14
    _log(store, customer_id, "[payroll] paycheck", c["paycheck_amount"], "in", "checking")
    return c["paycheck_amount"]


def advance_days(customer_id: str, days: int) -> None:
    """Move the demo clock forward: day-to-day spending and bills that come due leave checking."""
    return _demo("advance_days", _fx_advance_days, customer_id, days)


def _fx_advance_days(store: dict, customer_id: str, days: int) -> None:
    c = store[customer_id]
    start = date.fromisoformat(c["as_of"])
    end = start + timedelta(days=days)
    c["as_of"] = end.isoformat()
    c["days_to_payday"] = max(0, c["days_to_payday"] - days)
    spend = whole(c["monthly_spending"] * days / 30)
    c["checking_balance"] = round(c["checking_balance"] - spend, 2)
    if spend:
        _log(store, customer_id, "[spend] day-to-day spending", spend, "out", "checking", on=end.isoformat())
    for b in c["upcoming_bills"]:
        day = c["bill_days"][b["name"]]
        for d in state.occurrences(day, start, end):   # each due date we passed is paid
            c["checking_balance"] = round(c["checking_balance"] - b["amount"], 2)
            _log(store, customer_id, f"[bill] {b['name']}", b["amount"], "out", "checking", on=d.isoformat())
        b["due_in_days"] = (state.next_due(day, end) - end).days


def record_purchase(customer_id: str, name: str, amount: float) -> None:
    """Life event, e.g. 'Unexpected $600 car repair'. Savings covers it; anything savings
    can't cover goes on the credit card. Checking (earmarked for bills) is left alone."""
    return _demo("record_purchase", _fx_record_purchase, customer_id, name, amount)


def _fx_record_purchase(store: dict, customer_id: str, name: str, amount: float) -> None:
    c = store[customer_id]
    amount = whole(amount)
    from_savings = whole(min(amount, max(0.0, c["cash_cushion"])))
    c["cash_cushion"] = round(c["cash_cushion"] - from_savings, 2)
    c["card_debt"] = round(c["card_debt"] + amount - from_savings, 2)
    if from_savings > 0:
        _log(store, customer_id, f"[event] cover {name}", from_savings, "move", "savings", "checking")
        _log(store, customer_id, f"[event] {name}", from_savings, "out", "checking")
    if amount - from_savings > 0:
        _log(store, customer_id, f"[event] {name}", amount - from_savings, "out", "card")


def raise_paycheck(customer_id: str, amount: float) -> None:
    """Paychecks go up by `amount` from now on, and the first raise lands in checking now."""
    return _demo("raise_paycheck", _fx_raise_paycheck, customer_id, whole(amount))


def _fx_raise_paycheck(store: dict, customer_id: str, amount: float) -> None:
    c = store[customer_id]
    c["paycheck_amount"] = round(c["paycheck_amount"] + amount, 2)
    c["monthly_income"] = round(c["monthly_income"] + 2 * amount, 2)   # two paychecks a month
    c["checking_balance"] = round(c["checking_balance"] + amount, 2)
    _log(store, customer_id, f"[payroll-adj] raise to {c['paycheck_amount']:g}", amount, "in", "checking")   # as live writes


def apply_raise(customer_id: str, pct: float) -> None:
    done, paycheck = _try_live("current_paycheck", customer_id) if mode() == LIVE else (False, None)
    if not done:
        paycheck = _store()[customer_id]["paycheck_amount"]
    raise_paycheck(customer_id, round(paycheck * pct / 100, 2))


def apply_market_move(customer_id: str, pct: float) -> None:
    return _demo("apply_market_move", _fx_apply_market_move, customer_id, pct)


def _fx_apply_market_move(store: dict, customer_id: str, pct: float) -> None:
    c = store[customer_id]
    change = whole(c["invested_balance"] * pct / 100)
    c["invested_balance"] = round(c["invested_balance"] + change, 2)
    if change:
        _log(store, customer_id, f"[event] market move {pct:+g}%", abs(change), "in" if change > 0 else "out", "investing")
