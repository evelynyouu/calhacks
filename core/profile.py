"""Raw Nessie data -> FinancialProfile.  OWNER: Evelyn.

NESSIE_MODE=fixture (default): reads the in-memory personas (core/nessie.py stub). No network.
NESSIE_MODE=live: derives the profile from Nessie using the ids in data/nessie_ids.json. The last
good live profile is cached in data/cache/{persona}.json and served if Nessie errors.

    python -m core.profile [persona ...]          # print profiles in the current mode
    python -m core.profile --diff [persona ...]   # live vs fixture, flags anything off by > $1
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests

from core import nessie, state
from core.models import Bill, FinancialProfile, to_dict

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
PAY_PERIOD_DAYS = 14
SPENDING_WINDOW_DAYS = 60
TOLERANCE = 1.0   # dollars; live vs fixture
EXACT_FIELDS = {"days_to_payday", "card_apr"}   # numbers that aren't dollar amounts


def get_profile(customer_id: str) -> FinancialProfile:
    if nessie.mode() == nessie.LIVE:
        return _live_profile(customer_id)
    c = nessie.get_customer_raw(customer_id)
    return FinancialProfile(
        customer_id=customer_id,
        name=c["name"],
        monthly_income=c["monthly_income"],
        monthly_bills=c["monthly_bills"],
        monthly_spending=c["monthly_spending"],
        checking_balance=c["checking_balance"],
        cash_cushion=c["cash_cushion"],
        card_debt=c["card_debt"],
        card_apr=c["card_apr"],
        invested_balance=c["invested_balance"],
        paycheck_amount=c["paycheck_amount"],
        days_to_payday=c["days_to_payday"],
        upcoming_bills=[Bill(**b) for b in c["upcoming_bills"]],
    )


# ---------- live ----------

def _live_profile(customer_id: str) -> FinancialProfile:
    ids = nessie.load_ids().get(customer_id)
    if ids is None:
        raise KeyError(customer_id)   # not seeded (the app shows 404)
    try:
        p = derive_live(customer_id, ids)
    except (nessie.NessieError, requests.RequestException):
        cached = load_cached(customer_id)
        if cached is None:
            raise
        return cached
    _save_cache(p)
    return p


def _date(txn: dict, key: str = "transaction_date") -> date:
    return date.fromisoformat(str(txn[key])[:10])


def _prefetch(acct: dict) -> None:
    """Fetch every list the profile reads in parallel; the reads below then hit the GET cache."""
    calls = [(fn, a, {}) for a in acct.values() for fn in (nessie.get_deposits, nessie.get_withdrawals, nessie.get_purchases)]
    calls.append((nessie.get_bills, acct["checking"], {}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(fn, a, **kw) for fn, a, kw in calls]:
            future.result()   # re-raise the first error, so the cache fallback kicks in


def derive_live(customer_id: str, ids: dict) -> FinancialProfile:
    persona = nessie.persona_seed(customer_id)
    acct, seed = ids["accounts"], ids["seed"]
    as_of = state.as_of(customer_id)
    if nessie.CACHE_SECONDS > 0:
        _prefetch(acct)

    payroll = [d for d in nessie.get_deposits(acct["checking"])
               if nessie.is_ours(d) and d["description"].startswith("[payroll]")]
    if not payroll:
        raise ValueError(f"{customer_id}: no [payroll] deposits in Nessie; re-seed this persona")
    latest = max(payroll, key=_date)
    paycheck = state.paycheck_override(customer_id) or latest["amount"]
    last_payday = _date(latest)

    # Spending average comes from the seeded history before the sim started, so it stays put
    # while the demo clock moves (the [spend] lumps fast-forward writes are later than SIM_START).
    window_start = state.SIM_START - timedelta(days=SPENDING_WINDOW_DAYS)
    spent = sum(x["amount"] for account in (acct["checking"], acct["card"]) for x in nessie.get_purchases(account)
                if nessie.is_ours(x) and not x["description"].startswith("[event")
                and window_start < date.fromisoformat(nessie.txn_date(x)) <= state.SIM_START)

    bills = [Bill(b["nickname"], b["payment_amount"], (state.next_due(b["recurring_date"], as_of) - as_of).days)
             for b in nessie.get_bills(acct["checking"])]

    return FinancialProfile(
        customer_id=customer_id,
        name=persona["name"],
        monthly_income=round(paycheck * 2, 2),   # team convention: two paychecks a month
        monthly_bills=round(sum(b.amount for b in bills), 2),
        monthly_spending=round(spent / (SPENDING_WINDOW_DAYS / 30), 2),
        checking_balance=nessie.account_balance(acct["checking"], seed["checking"]),
        cash_cushion=nessie.account_balance(acct["savings"], seed["savings"]),
        card_debt=nessie.account_balance(acct["card"], seed["card"], liability=True),
        card_apr=persona["card_apr"],   # Nessie has no APR
        invested_balance=nessie.account_balance(acct["investing"], seed["investing"]),
        paycheck_amount=paycheck,
        days_to_payday=max(0, (last_payday + timedelta(days=PAY_PERIOD_DAYS) - as_of).days),
        upcoming_bills=bills,
    )


def _save_cache(p: FinancialProfile) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{p.customer_id}.json").write_text(json.dumps(to_dict(p), indent=2))


def load_cached(customer_id: str) -> FinancialProfile | None:
    try:
        d = json.loads((CACHE_DIR / f"{customer_id}.json").read_text())
    except FileNotFoundError:
        return None
    d["upcoming_bills"] = [Bill(**b) for b in d["upcoming_bills"]]
    return FinancialProfile(**d)


# ---------- live vs fixture ----------

def diff(live: FinancialProfile, fixture: FinancialProfile) -> list[str]:
    """Fields where live and fixture disagree: money by more than TOLERANCE, anything else at all."""
    problems = []
    a, b = to_dict(live), to_dict(fixture)
    for key in a:
        if key == "upcoming_bills":
            continue
        x, y = a[key], b[key]
        money = key not in EXACT_FIELDS and all(isinstance(v, (int, float)) for v in (x, y))
        off = abs(x - y) > TOLERANCE if money else x != y
        if off:
            problems.append(f"{key}: live {x} vs fixture {y}")
    lb = {bill["name"]: bill for bill in a["upcoming_bills"]}
    fb = {bill["name"]: bill for bill in b["upcoming_bills"]}
    for name in sorted(set(lb) | set(fb)):
        if name not in lb or name not in fb:
            problems.append(f"bill {name!r}: only in {'live' if name in lb else 'fixture'}")
        elif abs(lb[name]["amount"] - fb[name]["amount"]) > TOLERANCE or lb[name]["due_in_days"] != fb[name]["due_in_days"]:
            problems.append(f"bill {name!r}: live {lb[name]} vs fixture {fb[name]}")
    return problems


def _main(argv: list[str]) -> int:
    import os
    do_diff = "--diff" in argv
    personas = [a for a in argv if not a.startswith("--")] or [c["id"] for c in nessie.list_customers()]
    bad = 0
    for cid in personas:
        if not do_diff:
            print(json.dumps(to_dict(get_profile(cid)), indent=2))
            continue
        os.environ["NESSIE_MODE"] = nessie.LIVE
        live = get_profile(cid)
        os.environ["NESSIE_MODE"] = "fixture"
        problems = diff(live, get_profile(cid))
        bad += bool(problems)
        print(f"{cid}: {'OK' if not problems else 'MISMATCH'}")
        for line in problems:
            print(f"  {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
