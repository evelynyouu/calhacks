"""In-memory stand-in for the Nessie API, swapped in for core.nessie._send in tests.

It enforces the schema measured against the real API on 2026-10-24 (scripts/nessie_spike.py
plus field probes): which fields each write requires and forbids, whole-dollar amounts, and
the read-side quirks (an empty transfer list is a 404; a purchase without `status` or a bill
without `payment_date` makes the whole list fail to read).
"""
from __future__ import annotations

import itertools
import re

from core import nessie

TXN_KINDS = ("deposits", "withdrawals", "purchases", "transfers", "bills")

# kind: (required fields, optional fields); anything else is "extra fields not permitted"
SCHEMA = {
    "deposits": ({"medium", "transaction_date", "status", "amount", "description"}, set()),
    "withdrawals": ({"medium", "amount"}, {"transaction_date", "status", "description"}),
    "purchases": ({"merchant_id", "medium", "amount"}, {"purchase_date", "status", "description"}),
    "transfers": ({"transaction_date", "status", "amount", "description"}, set()),
    "bills": ({"status", "payee", "payment_amount"}, {"nickname", "recurring_date", "payment_date"}),
}


class FakeNessie:
    def __init__(self):
        self._ids = itertools.count(1)
        self.customers: dict = {}
        self.accounts: dict = {}
        self.merchants: dict = {}
        self.txns: dict = {k: {} for k in TXN_KINDS}   # kind -> id -> record
        self.calls: list = []

    def _new(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    @staticmethod
    def _validate(method, path, kind, body):
        required, optional = SCHEMA[kind]
        errors = [f"{f}: field required" for f in sorted(required - set(body))]
        errors += [f"{f}: extra fields not permitted" for f in sorted(set(body) - required - optional)]
        if kind != "bills" and "amount" in body and not isinstance(body["amount"], int):
            errors.append(f"amount: value is not a valid integer ({body['amount']!r})")
        if errors:
            raise nessie.NessieError(method, path, 400, " | ".join(errors))

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path, body, params))
        if path.split("?")[0].rstrip("/") == "/data":
            raise ValueError("refusing to touch /data")
        if method == "POST" and path == "/customers":
            cid = self._new("cust")
            self.customers[cid] = {"_id": cid, **body}
            return {"code": 201, "objectCreated": self.customers[cid]}
        if method == "POST" and path == "/merchants":
            if "name" not in body:
                raise nessie.NessieError(method, path, 400, "name: field required")
            mid = self._new("merch")
            self.merchants[mid] = {"_id": mid, **body}
            return {"code": 201, "objectCreated": self.merchants[mid]}

        if method == "GET" and path == "/accounts":
            return list(self.accounts.values())

        m = re.fullmatch(r"/customers/([^/]+)(/accounts)?", path)
        if m:
            cid, sub = m.groups()
            if cid not in self.customers:
                raise nessie.NessieError(method, path, 404, {"message": "Invalid ID"})
            if not sub:
                return self.customers[cid]
            if method == "POST":
                aid = self._new("acct")
                self.accounts[aid] = {"_id": aid, "customer_id": cid, **body}
                return {"code": 201, "objectCreated": self.accounts[aid]}
            return [a for a in self.accounts.values() if a["customer_id"] == cid]

        m = re.fullmatch(r"/accounts/([^/]+)(?:/([a-z]+))?", path)
        if m:
            aid, kind = m.groups()
            if kind is None and method == "DELETE":
                self.accounts.pop(aid, None)   # real Nessie answers 200 even if it's already gone
                for store in self.txns.values():
                    for tid in [t for t, r in store.items() if r["account_id"] == aid]:
                        del store[tid]
                return None
            if aid not in self.accounts:
                raise nessie.NessieError(method, path, 404, {"message": "Invalid ID"})
            if kind is None:
                return self.accounts[aid]
            store = self.txns[kind]
            if method == "POST":
                self._validate(method, path, kind, body)
                tid = self._new(kind[:3])
                store[tid] = {"_id": tid, "account_id": aid, **body}
                if kind == "withdrawals":
                    store[tid].update(type="withdrawal", payer_id=aid)
                return {"code": 201, "objectCreated": store[tid]}
            items = [r for r in store.values() if r["account_id"] == aid]
            if kind == "transfers" and not items:
                raise nessie.NessieError(method, path, 404, "No transfers found for this account")
            if kind == "purchases" and any("status" not in r for r in items):
                raise nessie.NessieError(method, path, 400, "1 validation error for Purchase: status field required")
            if kind == "bills" and any("payment_date" not in r for r in items):
                raise nessie.NessieError(method, path, 400, "1 validation error for Bill: payment_date field required")
            return items

        raise AssertionError(f"FakeNessie: unhandled {method} {path}")
