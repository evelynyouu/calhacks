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

import calendar
import contextlib
import itertools
import json
from datetime import date, timedelta

from core import nessie, state
from core.state import SIM_START

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


# ---------- dates ----------

def next_due(day: int, on_or_after: date) -> date:
    """Next date on/after `on_or_after` falling on day-of-month `day` (clamped to the month's length)."""
    year, month = on_or_after.year, on_or_after.month
    while True:
        candidate = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if candidate >= on_or_after:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def occurrences(day: int, start: date, end: date) -> list[date]:
    """Every due date for day-of-month `day` in [start, end)."""
    out, d = [], next_due(day, start)
    while d < end:
        out.append(d)
        d = next_due(day, d + timedelta(days=1))
    return out


# ---------- the pure plan ----------

def plan_persona(cid: str) -> dict:
    """Everything the seeder will write for a persona, with opening balances solved. No I/O."""
    p = nessie.persona_seed(cid)
    last_payday = SIM_START + timedelta(days=p["days_to_payday"] - PAY_PERIOD_DAYS)
    paydays = [last_payday - timedelta(days=PAY_PERIOD_DAYS * k) for k in range(PAYCHECKS - 1, -1, -1)]
    history_start = min(paydays[0], SIM_START - timedelta(days=7 * PURCHASES))

    total_spend = round(2 * p["monthly_spending"], 2)
    each = round(total_spend / PURCHASES, 2)
    purchases = [{"merchant": SPEND_MERCHANTS[i % len(SPEND_MERCHANTS)],
                  "on": SIM_START - timedelta(days=7 * (PURCHASES - i)),
                  "amount": each} for i in range(PURCHASES)]
    purchases[-1]["amount"] = round(total_spend - each * (PURCHASES - 1), 2)

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
    plan = plan_persona(cid)
    p, o = plan["persona"], plan["opening"]
    cust = nessie.create_customer(p["name"], "Demo")["_id"]
    acct = {
        "checking": nessie.create_account(cust, "Checking", f"{p['name']} Checking", o["checking"])["_id"],
        "savings": nessie.create_account(cust, "Savings", f"{p['name']} Savings", o["savings"])["_id"],
        "investing": nessie.create_account(cust, "Savings", "FirstFund Investing", o["investing"])["_id"],
        "card": nessie.create_account(cust, "Credit Card", f"{p['name']} Card", o["card"])["_id"],
    }
    chk = acct["checking"]
    for b in plan["bills"]:
        nessie.create_bill(chk, b["name"], b["name"], b["amount"], b["recurring_date"])
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
    return {"customer_id": cust, "accounts": acct, "seed": o}


def delete_persona(cid: str, ids: dict) -> None:
    """Deletes only the accounts we stored for this persona. Customers can't be deleted in Nessie."""
    for account_id in ids.get(cid, {}).get("accounts", {}).values():
        try:
            nessie.delete_account(account_id)
        except nessie.NessieError as e:
            if e.status != 404:   # already gone is fine
                raise
    ids.pop(cid, None)


def seed(personas: list[str] | None = None, reset: str | None = None, save: bool = True) -> dict:
    ids = nessie.load_ids()
    if reset:
        delete_persona(reset, ids)
        if save:
            nessie.save_ids(ids)
            state.clear(reset)
    merchants = ensure_merchants(ids)
    if save:
        nessie.save_ids(ids)
    for cid in personas or list(nessie._SEED):
        if cid in ids:
            print(f"{cid}: already seeded, skipping (use --reset {cid} to recreate)")
            continue
        ids[cid] = seed_persona(cid, merchants)
        if save:
            nessie.save_ids(ids)   # after each persona, so a failure midway keeps what was made
        print(f"{cid}: seeded customer {ids[cid]['customer_id']}")
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
