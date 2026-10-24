"""In-memory stand-in for the Nessie API, swapped in for core.nessie.request in tests.

Implements the endpoints core/nessie.py uses, with Nessie's response shapes
(objectCreated on POST, lists on GET, ?type=payer|payee on transfers).
"""
from __future__ import annotations

import itertools
import re

from core import nessie

TXN_KINDS = ("deposits", "withdrawals", "purchases", "transfers", "bills")


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

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path, body, params))
        if path.split("?")[0].rstrip("/") == "/data":
            raise ValueError("refusing to touch /data")
        if method == "POST" and path == "/customers":
            cid = self._new("cust")
            self.customers[cid] = {"_id": cid, **body}
            return {"code": 201, "objectCreated": self.customers[cid]}
        if method == "POST" and path == "/merchants":
            mid = self._new("merch")
            self.merchants[mid] = {"_id": mid, **body}
            return {"code": 201, "objectCreated": self.merchants[mid]}

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
            if aid not in self.accounts:
                raise nessie.NessieError(method, path, 404, {"message": "Invalid ID"})
            if kind is None:
                if method == "DELETE":
                    del self.accounts[aid]
                    for store in self.txns.values():
                        for tid in [t for t, r in store.items() if aid in (r["account_id"], r.get("payee_id"))]:
                            del store[tid]
                    return None
                return self.accounts[aid]
            store = self.txns[kind]
            if method == "POST":
                tid = self._new(kind[:3])
                store[tid] = {"_id": tid, "account_id": aid, "status": "pending", **body}
                if kind == "transfers":
                    store[tid]["payer_id"] = aid
                return {"code": 201, "objectCreated": store[tid]}
            if kind == "transfers":
                role = (params or {}).get("type")
                if role == "payer":
                    return [r for r in store.values() if r["payer_id"] == aid]
                if role == "payee":
                    return [r for r in store.values() if r["payee_id"] == aid]
                return [r for r in store.values() if aid in (r["payer_id"], r["payee_id"])]
            return [r for r in store.values() if r["account_id"] == aid]

        raise AssertionError(f"FakeNessie: unhandled {method} {path}")
