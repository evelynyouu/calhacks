"""Live-mode side effects behind the core/nessie.py demo functions.  OWNER: Evelyn.

Each function writes tagged Nessie transactions with the same effect the fixture stub has on its
in-memory numbers, so the app behaves the same in both modes. The sim clock and the current
paycheck are read back from Nessie (core.profile.live_clock), so nothing lives in local files.
"""
from __future__ import annotations

from datetime import timedelta

from core import nessie, seed, state
from core.profile import get_profile, live_clock


def _accounts(cid: str) -> dict:
    return nessie.load_ids()[cid]["accounts"]


def _today(cid: str) -> str:
    return live_clock(cid)[0].isoformat()


def current_paycheck(cid: str) -> float:
    return get_profile(cid).paycheck_amount


def deposit_paycheck(cid: str) -> float:
    amount = current_paycheck(cid)
    nessie.create_deposit(_accounts(cid)["checking"], amount, _today(cid), "[payroll] paycheck")
    return amount


def advance_days(cid: str, days: int) -> None:
    """Spending for the elapsed days and any bills that fell due leave checking; the clock moves."""
    if days <= 0:
        return
    acct, merchants = _accounts(cid), nessie.load_ids()["_merchants"]
    start = live_clock(cid)[0]
    end = start + timedelta(days=days)
    spend = nessie.whole(get_profile(cid).monthly_spending * days / 30)
    if spend:
        nessie.create_purchase(acct["checking"], merchants["groceries"], spend, end.isoformat(),
                               "[spend] day-to-day spending")
    for b in nessie.get_bills(acct["checking"]):
        for d in state.occurrences(b["recurring_date"], start, end):
            nessie.create_withdrawal(acct["checking"], b["payment_amount"], d.isoformat(), f"[bill] {b['nickname']}")


def transfer_to_investing(cid: str, amount: float) -> str:
    acct = _accounts(cid)
    return nessie.create_transfer(acct["checking"], acct["investing"], amount, _today(cid),
                                  "[firstfund] payday auto-invest")["_id"]


def record_purchase(cid: str, name: str, amount: float) -> None:
    """Savings covers the cost (moved to checking, then paid); anything left goes on the card."""
    acct, merchants = _accounts(cid), nessie.load_ids()["_merchants"]
    amount = nessie.whole(amount)
    from_savings = nessie.whole(min(amount, max(0.0, get_profile(cid).cash_cushion)))
    if from_savings > 0:
        nessie.create_transfer(acct["savings"], acct["checking"], from_savings, _today(cid), f"[event] cover {name}")
        nessie.create_purchase(acct["checking"], merchants["auto"], from_savings, _today(cid), f"[event] {name}")
    rest = round(amount - from_savings, 2)
    if rest > 0:
        nessie.create_purchase(acct["card"], merchants["auto"], rest, _today(cid), f"[event] {name}")


def raise_paycheck(cid: str, amount: float) -> None:
    new = round(current_paycheck(cid) + amount, 2)
    nessie.create_deposit(_accounts(cid)["checking"], amount, _today(cid), f"[payroll-adj] raise to {new:g}")


def apply_market_move(cid: str, pct: float) -> None:
    acct = _accounts(cid)["investing"]
    change = nessie.whole(get_profile(cid).invested_balance * pct / 100)
    if change < 0:
        nessie.create_withdrawal(acct, -change, _today(cid), f"[event] market move {pct:+g}%")
    elif change > 0:
        nessie.create_deposit(acct, change, _today(cid), f"[event] market move {pct:+g}%")


def activity(cid: str) -> list[dict]:
    """Every tagged transaction on the persona's accounts (cached GETs, so this is cheap after a profile)."""
    acct = _accounts(cid)
    items, outs, ins = [], [], []
    for r, aid in acct.items():
        ins += [(r, t) for t in filter(nessie.is_ours, nessie.get_deposits(aid))]
        outs += [(r, t) for t in filter(nessie.is_ours, nessie.get_withdrawals(aid))]
        for t in filter(nessie.is_ours, nessie.get_purchases(aid)):
            items.append(nessie.activity_item(nessie.txn_date(t), t["description"], t["amount"], "out", r))
    # A move between the persona's accounts is a withdrawal + deposit with the same date, amount and
    # description (Nessie transfers have no destination); show each pair as one "move" line.
    for r_out, w in outs:
        key = (nessie.txn_date(w), w["amount"], w["description"])
        match = next((i for i, (r_in, d) in enumerate(ins)
                      if r_in != r_out and (nessie.txn_date(d), d["amount"], d["description"]) == key), None)
        if match is None:
            items.append(nessie.activity_item(key[0], w["description"], w["amount"], "out", r_out))
        else:
            r_in, _ = ins.pop(match)
            items.append(nessie.activity_item(key[0], w["description"], w["amount"], "move", r_out, r_in))
    items += [nessie.activity_item(nessie.txn_date(d), d["description"], d["amount"], "in", r) for r, d in ins]
    return items


def reset(cid: str | None = None) -> None:
    for persona in [cid] if cid else list(nessie._SEED):
        seed.reset_persona(persona)
