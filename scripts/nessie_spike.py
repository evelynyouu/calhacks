"""One-off Nessie behavior check. Answers, before we build on it:

1. Do POSTed transactions change account.balance (and how fast)?
2. Is a backdated transaction_date accepted and preserved?
3. What status do new transactions get (pending vs executed)?
4. Do transfers move money on both sides?

Creates a throwaway customer + two accounts, deletes the accounts at the end.
Usage: .venv/bin/python scripts/nessie_spike.py
"""
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
KEY = os.environ.get("NESSIE_API_KEY")
BASE = "http://api.nessieisreal.com"
if not KEY:
    sys.exit("NESSIE_API_KEY missing: put it in .env")


def call(method, path, body=None):
    r = httpx.request(method, f"{BASE}{path}", params={"key": KEY}, json=body, timeout=15)
    print(f"  {method} {path} -> {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        data = r.text
    if r.status_code >= 400:
        print("   ", data)
    return data


def balance(acct_id):
    return call("GET", f"/accounts/{acct_id}").get("balance")


print("create customer")
cust = call("POST", "/customers", {
    "first_name": "Spike", "last_name": "Test",
    "address": {"street_number": "1", "street_name": "Test St", "city": "Berkeley", "state": "CA", "zip": "94720"},
})["objectCreated"]

print("create checking + savings (balance 1000 / 500)")
chk = call("POST", f"/customers/{cust['_id']}/accounts",
           {"type": "Checking", "nickname": "spike checking", "rewards": 0, "balance": 1000})["objectCreated"]
sav = call("POST", f"/customers/{cust['_id']}/accounts",
           {"type": "Savings", "nickname": "spike savings", "rewards": 0, "balance": 500})["objectCreated"]

try:
    print("backdated deposit 100 on 2026-09-01")
    dep = call("POST", f"/accounts/{chk['_id']}/deposits", {
        "medium": "balance", "transaction_date": "2026-09-01", "amount": 100, "description": "[payroll] spike",
    })
    print("   created:", dep.get("objectCreated") if isinstance(dep, dict) else dep)

    print("withdrawal 30")
    call("POST", f"/accounts/{chk['_id']}/withdrawals",
         {"medium": "balance", "transaction_date": "2026-09-02", "amount": 30, "description": "[event] spike"})

    print("transfer 50 checking -> savings")
    call("POST", f"/accounts/{chk['_id']}/transfers",
         {"medium": "balance", "payee_id": sav["_id"], "transaction_date": "2026-09-03", "amount": 50,
          "description": "[firstfund] spike"})

    for wait in (0, 5, 20):
        if wait:
            time.sleep(wait)
        print(f"balances after +{wait}s: checking={balance(chk['_id'])} (expect 1020) savings={balance(sav['_id'])} (expect 550)")

    print("deposit as stored:")
    for d in call("GET", f"/accounts/{chk['_id']}/deposits"):
        print("   ", {k: d.get(k) for k in ("transaction_date", "status", "amount", "description")})
    for t in call("GET", f"/accounts/{chk['_id']}/transfers"):
        print("    transfer", {k: t.get(k) for k in ("transaction_date", "status", "amount", "type")})
finally:
    print("cleanup")
    call("DELETE", f"/accounts/{chk['_id']}")
    call("DELETE", f"/accounts/{sav['_id']}")
