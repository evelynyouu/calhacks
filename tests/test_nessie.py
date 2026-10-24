"""Nessie HTTP client, offline: requests.request is faked. No network, no key needed."""
import json

import pytest
import requests

from core import nessie


def _response(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    return r


@pytest.fixture
def fake(monkeypatch):
    """Queue (status, body) tuples or exceptions; every call is recorded."""
    calls, queue = [], []

    def request(method, url, params=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "json": json, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _response(*item)

    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setattr(nessie.requests, "request", request)
    monkeypatch.setattr(nessie.time, "sleep", lambda _: None)
    monkeypatch.setattr(nessie, "CACHE_SECONDS", 0)   # cache tests turn it on
    nessie.clear_cache()
    return calls, queue


# ---------- transport ----------

def test_get_sends_key_and_timeout(fake):
    calls, queue = fake
    queue.append((200, {"_id": "a1", "balance": 100}))
    assert nessie.get_account("a1") == {"_id": "a1", "balance": 100}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://api.nessieisreal.com/accounts/a1"
    assert calls[0]["params"] == {"key": "test-key"}
    assert calls[0]["timeout"] == 10


def test_get_retries_5xx_twice_then_succeeds(fake):
    calls, queue = fake
    queue += [(503, {}), (502, {}), (200, [])]
    assert nessie.get_accounts("c1") == []
    assert len(calls) == 3


def test_get_gives_up_after_two_retries(fake):
    calls, queue = fake
    queue += [(500, {"m": 1}), (500, {"m": 2}), (500, {"m": 3})]
    with pytest.raises(nessie.NessieError) as e:
        nessie.get_customer("c1")
    assert len(calls) == 3
    assert e.value.status == 500 and e.value.body == {"m": 3}


def test_get_retries_connection_errors(fake):
    calls, queue = fake
    queue += [requests.ConnectionError("down"), requests.ReadTimeout("slow"), (200, {"_id": "c1"})]
    assert nessie.get_customer("c1") == {"_id": "c1"}
    assert len(calls) == 3


def test_post_retried_when_connection_failed(fake):
    calls, queue = fake
    queue += [requests.ConnectionError("down"), (201, {"objectCreated": {"_id": "d1"}})]
    assert nessie.create_deposit("a1", 100, "2026-10-30", "[payroll]")["_id"] == "d1"
    assert len(calls) == 2


def test_post_not_retried_on_5xx(fake):
    # The POST may have been applied before the 5xx; retrying could duplicate a transaction.
    calls, queue = fake
    queue.append((500, {"message": "boom"}))
    with pytest.raises(nessie.NessieError):
        nessie.create_deposit("a1", 100, "2026-10-30", "[payroll]")
    assert len(calls) == 1


def test_post_not_retried_after_read_timeout(fake):
    calls, queue = fake
    queue.append(requests.ReadTimeout("slow"))
    with pytest.raises(requests.ReadTimeout):
        nessie.create_deposit("a1", 100, "2026-10-30", "[payroll]")
    assert len(calls) == 1


def test_4xx_raises_with_status_and_body_no_retry(fake):
    calls, queue = fake
    queue.append((404, {"message": "Invalid ID"}))
    with pytest.raises(nessie.NessieError) as e:
        nessie.get_account("nope")
    assert (e.value.status, e.value.body) == (404, {"message": "Invalid ID"})
    assert len(calls) == 1


def test_refuses_data_endpoint(fake):
    calls, _ = fake
    for path in ("/data", "/data/", "/data?type=Accounts"):
        with pytest.raises(ValueError):
            nessie.request("DELETE", path)
    assert calls == []
    assert not [n for n in dir(nessie) if "data" in n.lower() and callable(getattr(nessie, n))]


def test_missing_key(monkeypatch):
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        nessie.get_account("a1")


# ---------- endpoints ----------

def test_create_customer_unwraps_object_created(fake):
    calls, queue = fake
    queue.append((201, {"code": 201, "message": "Created customer", "objectCreated": {"_id": "c9"}}))
    assert nessie.create_customer("Sam", "Rivera") == {"_id": "c9"}
    assert calls[0]["url"].endswith("/customers")
    assert calls[0]["json"]["first_name"] == "Sam" and "address" in calls[0]["json"]


def test_create_account_body(fake):
    calls, queue = fake
    queue.append((201, {"objectCreated": {"_id": "a9"}}))
    nessie.create_account("c9", "Savings", "FirstFund Investing", 3000)
    assert calls[0]["url"].endswith("/customers/c9/accounts")
    assert calls[0]["json"] == {"type": "Savings", "nickname": "FirstFund Investing", "rewards": 0, "balance": 3000}


def test_transaction_bodies(fake):
    """The exact shapes the real API accepted (measured 2026-10-24)."""
    calls, queue = fake
    queue += [(201, {"objectCreated": {"_id": f"t{i}"}}) for i in range(5)]
    nessie.create_deposit("a1", 1400, "2026-10-30", "[payroll]")
    nessie.create_withdrawal("a1", 900, "2026-10-30", "[event] market drop")
    nessie.create_purchase("a1", "m1", 600, "2026-10-30", "[event] car repair")
    moved = nessie.create_transfer("a1", "a2", 50, "2026-10-30", "[firstfund] payday")
    dep, wd, pur, move_out, move_in = (c["json"] for c in calls)
    assert dep == {"medium": "balance", "transaction_date": "2026-10-30", "status": "completed", "amount": 1400,
                   "description": "[payroll]"}
    assert wd == dict(dep, amount=900, description="[event] market drop")
    assert pur == {"medium": "balance", "purchase_date": "2026-10-30", "status": "completed", "amount": 600,
                   "description": "[event] car repair", "merchant_id": "m1"}
    # a move is a withdrawal from one account and a deposit to the other (Nessie transfers have no destination)
    assert [c["url"].rsplit("/accounts/", 1)[1] for c in calls[3:]] == ["a1/withdrawals", "a2/deposits"]
    assert move_out == move_in == dict(dep, amount=50, description="[firstfund] payday")
    assert moved == {"_id": "t4"}


def test_create_bill_and_merchant(fake):
    calls, queue = fake
    queue += [(201, {"objectCreated": {"_id": "b1"}}), (201, {"objectCreated": {"_id": "m1"}})]
    assert nessie.create_bill("a1", "Landlord", "Rent", 1100, 3, "2026-11-03")["_id"] == "b1"
    assert nessie.create_merchant("Mike's Auto", "auto")["_id"] == "m1"
    assert calls[0]["json"]["recurring_date"] == 3 and calls[0]["json"]["status"] == "recurring"
    assert calls[0]["json"]["payment_date"] == "2026-11-03"   # without it, reading bills back fails
    assert calls[1]["url"].endswith("/merchants")


def test_list_endpoints(fake):
    calls, queue = fake
    queue += [(200, [])] * 5
    nessie.get_deposits("a1")
    nessie.get_withdrawals("a1")
    nessie.get_purchases("a1")
    nessie.get_transfers("a1")
    nessie.get_bills("a1")
    assert [c["url"].rsplit("/", 1)[1] for c in calls] == ["deposits", "withdrawals", "purchases", "transfers", "bills"]


def test_delete_account(fake):
    calls, queue = fake
    queue.append((204, None))
    assert nessie.delete_account("a1") is None
    assert (calls[0]["method"], calls[0]["url"]) == ("DELETE", "https://api.nessieisreal.com/accounts/a1")


# ---------- ledger balance ----------

def test_account_balance_sums_only_our_tagged_transactions(fake):
    _, queue = fake

    def t(amount, description, status="pending"):
        return {"amount": amount, "description": description, "status": status}

    queue += [
        (200, [t(1400, "[payroll]"), t(200, "[payroll-adj] raise"), t(999, "someone else's deposit")]),  # deposits
        (200, [t(900, "[event] market drop"), t(5, "[event] old", status="cancelled")]),               # withdrawals
        (200, [t(600, "[event] car repair"), t(40, "groceries")]),                                    # purchases
    ]
    # 1000 + 1400 + 200 - 900 - 600; transfers aren't read (moves are withdrawal + deposit pairs)
    assert nessie.account_balance("a1", 1000) == 1100


# ---------- GET cache ----------

def test_gets_are_cached_until_a_write(fake, monkeypatch):
    calls, queue = fake
    monkeypatch.setattr(nessie, "CACHE_SECONDS", 30)
    queue += [(200, [{"amount": 1}]), (201, {"objectCreated": {"_id": "d1"}}), (200, [{"amount": 2}])]
    assert nessie.get_deposits("a1") == [{"amount": 1}]
    assert nessie.get_deposits("a1") == [{"amount": 1}]          # served from cache
    nessie.create_deposit("a1", 5, "2026-10-30", "[payroll]")    # any write clears it
    assert nessie.get_deposits("a1") == [{"amount": 2}]
    assert [c["method"] for c in calls] == ["GET", "POST", "GET"]


def test_cache_expires(fake, monkeypatch):
    calls, queue = fake
    clock = [1000.0]
    monkeypatch.setattr(nessie, "CACHE_SECONDS", 30)
    monkeypatch.setattr(nessie.time, "monotonic", lambda: clock[0])
    queue += [(200, []), (200, [])]
    nessie.get_bills("a1")
    clock[0] += 31
    nessie.get_bills("a1")
    assert len(calls) == 2


def test_get_in_flight_during_a_write_is_not_cached(monkeypatch):
    """A GET that started before a write may hold pre-write data; it must not be cached after the write."""
    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setattr(nessie, "CACHE_SECONDS", 30)
    nessie.clear_cache()
    server = {"deposits": [1]}

    def send(method, path, body=None, params=None):
        if method == "GET":
            snapshot = list(server["deposits"])
            nessie.request("POST", "/accounts/a1/deposits", {"amount": 2})   # another thread writes meanwhile
            return snapshot
        server["deposits"].append(2)
        return {"objectCreated": {}}

    monkeypatch.setattr(nessie, "_send", send)
    assert nessie.get_deposits("a1") == [1]          # this caller saw the old list; fine
    monkeypatch.setattr(nessie, "_send", lambda m, p, b=None, q=None: list(server["deposits"]))
    assert nessie.get_deposits("a1") == [1, 2]       # the next read must not get the stale [1]


def test_empty_transfer_list_404_means_no_transfers(fake):
    _, queue = fake
    queue += [(404, "No transfers found for this account")] + [(500, {"message": "boom"})] * 3   # 5xx GETs retry twice
    assert nessie.get_transfers("a1") == []
    with pytest.raises(nessie.NessieError):
        nessie.get_transfers("a1")   # other errors still raise


def test_a_write_only_evicts_the_account_it_touched(monkeypatch):
    """Background rebuilds write to the spare's accounts; that must not evict the live persona's lists."""
    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setattr(nessie, "CACHE_SECONDS", 30)
    nessie.clear_cache()
    gets = []

    def send(method, path, body=None, params=None):
        if method == "GET":
            gets.append(path)
            if path == "/accounts/a1/deposits" and len(gets) == 1:
                nessie.request("POST", "/accounts/b9/deposits", {"amount": 1})   # someone else's account, mid-read
            return [path]
        return {"objectCreated": {}}

    monkeypatch.setattr(nessie, "_send", send)
    nessie.get_deposits("a1")
    nessie.request("POST", "/customers", {"first_name": "x"})   # creating customers touches no account lists
    nessie.request("POST", "/accounts/b9/withdrawals", {"amount": 1})
    nessie.get_deposits("a1")
    assert gets == ["/accounts/a1/deposits"]                       # still cached
    nessie.request("POST", "/accounts/a1/deposits", {"amount": 1})
    nessie.get_deposits("a1")
    assert gets == ["/accounts/a1/deposits"] * 2                   # a write to a1 does evict a1
