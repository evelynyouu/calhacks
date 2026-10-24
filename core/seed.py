"""Seed the demo personas into Nessie.  OWNER: Evelyn.  CLI: scripts/seed_nessie.py

Per persona: a customer; Checking, Savings, "FirstFund Investing" (a Savings account; Nessie has
no investing type) and Credit Card accounts; recurring bills; 4 biweekly [payroll] deposits ending
on the last payday; 8 weekly [spend] purchases (two months of spending); [bill] payments for bills
that fell due in that window; and one [seed] transfer that moves the leftover to savings.
Opening balances are solved so the balances on SIM_START equal the persona's numbers.

Idempotent: ids go to data/nessie_ids.json and personas already there are skipped.
reset_persona deletes only that persona's accounts (by stored id), never anything else.
"""
from __future__ import annotations

import contextlib
import itertools
import json
from datetime import date, timedelta

from core import nessie, state
from core.state import SIM_START, next_due, occurrences   # noqa: F401  (re-exported for callers)

PAY_PERIOD_DAYS = 14
PAYCHECKS = 4
PURCHASES = 8
SPEND_MERCHANTS = ["groceries", "dining", "transit"]
MERCHANTS = {   # key -> (name, category); created once, shared by every persona
    "groceries": ("Berkeley Bowl", "Groceries"),
    "dining": ("Sliver Pizzeria", "Restaurants"),
    "transit": ("Bay Area Transit", "Transportation"),
    "auto": ("Mike's Auto", "Auto Repair"),
}


# ---------- the pure plan ----------

def plan_persona(cid: str) -> dict:
    """Everything the seeder will write for a persona, with opening balances solved. No I/O."""
    p = nessie.persona_seed(cid)
    last_payday = SIM_START + timedelta(days=p["days_to_payday"] - PAY_PERIOD_DAYS)
    paydays = [last_payday - timedelta(days=PAY_PERIOD_DAYS * k) for k in range(PAYCHECKS - 1, -1, -1)]
    history_start = min(paydays[0], SIM_START - timedelta(days=7 * PURCHASES))

    # Two months of spending in whole dollars (Nessie amounts are integers), spread as evenly as possible.
    total_spend = int(round(2 * p["monthly_spending"]))
    base, extra = divmod(total_spend, PURCHASES)
    purchases = [{"merchant": SPEND_MERCHANTS[i % len(SPEND_MERCHANTS)],
                  "on": SIM_START - timedelta(days=7 * (PURCHASES - i)),
                  "amount": float(base + (i < extra))} for i in range(PURCHASES)]

    bills = [{"name": b["name"], "amount": b["amount"],
              "recurring_date": (SIM_START + timedelta(days=b["due_in_days"])).day} for b in p["upcoming_bills"]]
    bill_payments = [{"name": b["name"], "amount": b["amount"], "on": d}
                     for b in bills for d in occurrences(b["recurring_date"], history_start, SIM_START)]

    pay_total = p["paycheck_amount"] * PAYCHECKS
    out_total = sum(x["amount"] for x in purchases) + sum(x["amount"] for x in bill_payments)
    to_savings = round(pay_total - out_total, 2)   # leftover the persona "saved"; negative = drew on savings
    opening = {
        "checking": round(p["checking_balance"], 2),
        "savings": round(p["cash_cushion"] - to_savings, 2),
        "investing": round(p["invested_balance"], 2),
        "card": round(p["card_debt"], 2),
    }
    for k, v in opening.items():
        if v < 0 or v != round(v):
            raise ValueError(f"{cid}: opening {k} balance {v} must be a whole, non-negative dollar amount")
    return {"persona": p, "paydays": paydays, "purchases": purchases, "bills": bills,
            "bill_payments": bill_payments, "to_savings": to_savings, "opening": opening}


# ---------- writes ----------

def ensure_merchants(ids: dict) -> dict:
    merchants = ids.setdefault("_merchants", {})
    for key, (name, category) in MERCHANTS.items():
        if key not in merchants:
            merchants[key] = nessie.create_merchant(name, category)["_id"]
    return merchants


def seed_persona(cid: str, merchants: dict) -> dict:
    """Creates the persona's accounts and history. If anything fails partway, the accounts made
    so far are deleted (the customer can't be) and the error is re-raised."""
    plan = plan_persona(cid)
    p, o = plan["persona"], plan["opening"]
    acct: dict = {}
    try:
        cust = nessie.create_customer(p["name"], "Demo")["_id"]
        for key, type_, nickname in (("checking", "Checking", f"{p['name']} Checking"),
                                     ("savings", "Savings", f"{p['name']} Savings"),
                                     ("investing", "Savings", "FirstFund Investing"),
                                     ("card", "Credit Card", f"{p['name']} Card")):
            acct[key] = nessie.create_account(cust, type_, nickname, o[key])["_id"]
        _write_history(plan, acct, merchants)
    except Exception:
        _delete_accounts(acct.values())
        raise
    return {"customer_id": cust, "accounts": acct, "seed": o}


def _write_history(plan: dict, acct: dict, merchants: dict) -> None:
    p = plan["persona"]
    chk = acct["checking"]
    for b in plan["bills"]:
        nessie.create_bill(chk, b["name"], b["name"], b["amount"], b["recurring_date"],
                           next_due(b["recurring_date"], SIM_START).isoformat())
    for d in plan["paydays"]:
        nessie.create_deposit(chk, p["paycheck_amount"], d.isoformat(), "[payroll] paycheck")
    for x in plan["purchases"]:
        nessie.create_purchase(chk, merchants[x["merchant"]], x["amount"], x["on"].isoformat(),
                               f"[spend] {MERCHANTS[x['merchant']][0]}")
    for x in plan["bill_payments"]:
        nessie.create_withdrawal(chk, x["amount"], x["on"].isoformat(), f"[bill] {x['name']}")
    t = plan["to_savings"]
    if t:
        src, dst = (chk, acct["savings"]) if t > 0 else (acct["savings"], chk)
        nessie.create_transfer(src, dst, abs(t), plan["paydays"][-1].isoformat(), "[seed] move leftover to savings")


def _delete_accounts(account_ids) -> None:
    """Deletes exactly these accounts (ids we created). Already gone is fine."""
    for account_id in list(account_ids):
        try:
            nessie.delete_account(account_id)
        except nessie.NessieError as e:
            if e.status != 404:
                raise


def seed(personas: list[str] | None = None, reset: str | None = None, save: bool = True) -> dict:
    """Seeds every persona in `personas` (default: all) that isn't in the ids file yet.
    `reset` is always (re)created: its new accounts are built first, and only once that succeeds
    are its old accounts deleted, so a failed reset leaves the old persona working."""
    ids = nessie.load_ids()
    merchants = ensure_merchants(ids)
    if save:
        nessie.save_ids(ids)
    todo = list(personas or nessie._SEED)
    if reset and reset not in todo:
        todo.append(reset)
    for cid in todo:
        if cid in ids and cid != reset:
            print(f"{cid}: already seeded, skipping (use --reset {cid} to recreate)")
            continue
        entry = seed_persona(cid, merchants)
        old = ids.get(cid)
        ids[cid] = entry
        if save:
            nessie.save_ids(ids)   # after each persona, so a failure midway keeps what was made
        if old:
            _delete_accounts(old["accounts"].values())
            if save:
                state.clear(cid)
        print(f"{cid}: {'re-seeded' if old else 'seeded'} customer {entry['customer_id']}")
    return ids


def reset_persona(cid: str) -> None:
    seed([cid], reset=cid)


@contextlib.contextmanager
def dry_run(out=print):
    """Print every request instead of sending it. POSTs get fake ids; GETs return []."""
    counter = itertools.count(1)
    real = nessie.request

    def fake(method, path, body=None, params=None):
        out(f"{method} {path}" + (f" {json.dumps(body, sort_keys=True)}" if body is not None else "")
            + (f" params={params}" if params else ""))
        return {"objectCreated": {"_id": f"dry-{next(counter)}"}} if method == "POST" else []

    nessie.request = fake
    try:
        yield
    finally:
        nessie.request = real
