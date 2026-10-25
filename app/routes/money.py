"""The Money tab: the customer's bank and their FirstFund investing on one page.

Every number here is computed in Python from the bank profile (core.profile), the bank feed
(core.nessie.recent_activity) and the fund (core.fund); the template only formats them.
Works before the fund is built (bank side only) and after.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.routes.pages import _ctx, _render, _session, _today
from core import fund, nessie

router = APIRouter()

FLOW_MONTHS = 4      # months shown in the money-flow chart
ACTIVITY_ROWS = 12   # rows in the recent bank activity list
TOP_HOLDINGS = 3

# Bank feed tags (core.nessie activity_item) -> money-flow bucket
FLOW_BUCKETS = (("paychecks", "Paychecks in"), ("invested", "Moved to investing"),
                ("spending", "Spending"), ("bills", "Bills"))


def _bucket(x: dict) -> str | None:
    """Which money-flow bucket a bank feed line belongs to, or None (moves to savings, market moves)."""
    if x["account"] == "Investing":
        return None   # the investing account's own market moves aren't cash flow
    if x["tag"] in ("payroll", "payroll-adj") and x["flow"] == "in":
        return "paychecks"
    if x["tag"] == "firstfund":
        return "invested"
    if x["tag"] == "bill":
        return "bills"
    if x["tag"] in ("spend", "event") and x["flow"] == "out":
        return "spending"
    return None


def money_flow(activity: list[dict], months: int = FLOW_MONTHS) -> dict:
    """Monthly totals per bucket, newest month first, with bar widths (percent of the biggest number shown)."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {k: 0.0 for k, _ in FLOW_BUCKETS})
    for x in activity:
        b = _bucket(x)
        if b:
            totals[x["on"][:7]][b] += x["amount"]
    keys = sorted(totals, reverse=True)[:months]
    biggest = max([v for k in keys for v in totals[k].values()] + [0.0])
    rows = []
    for k in keys:
        y, m = int(k[:4]), int(k[5:7])
        rows.append({"month": date(y, m, 1), "bars": [
            {"key": b, "label": label, "amount": round(totals[k][b], 2),
             "width": round(totals[k][b] / biggest * 100, 1) if biggest else 0.0} for b, label in FLOW_BUCKETS]})
    return {"months": rows, "buckets": FLOW_BUCKETS}


def bank_accounts(cid: str, profile, fund_value: float, has_fund: bool) -> list[dict]:
    """Checking, savings, card and FirstFund investing, with the last 4 of each Nessie account id (live mode only)."""
    ids = nessie.load_ids().get(cid, {}).get("accounts", {}) if nessie.mode() == nessie.LIVE else {}
    last4 = lambda kind: ids[kind][-4:] if ids.get(kind) else None
    return [
        {"kind": "checking", "name": "Checking", "balance": profile.checking_balance, "last4": last4("checking"),
         "note": "Paychecks land here"},
        {"kind": "savings", "name": "Savings", "balance": profile.cash_cushion, "last4": last4("savings"),
         "note": "Your cushion"},
        {"kind": "card", "name": "Credit card", "balance": profile.card_debt, "last4": last4("card"),
         "note": f"Owed · {profile.card_apr:g}% APR" if profile.card_debt else "Nothing owed", "owed": True},
        {"kind": "investing", "name": "FirstFund Investing", "balance": fund_value, "last4": last4("investing"),
         "note": "Fund value at today's prices" if has_fund else "Not built yet", "investing": True},
    ]


def bills_before(profile, today: date, days: int) -> list[dict]:
    """Bills due on or before the next payday, soonest first, with their due dates."""
    return [{"name": b.name, "amount": b.amount, "due": today + timedelta(days=b.due_in_days), "in_days": b.due_in_days}
            for b in profile.upcoming_bills if b.due_in_days <= days]


def money_context(s, profile, activity_all: list[dict]) -> dict:
    """Everything the Money tab shows, as numbers."""
    a = s.fund
    has_fund = a.portfolio is not None
    value = fund.value(a) if has_fund else 0.0
    nxt = fund.next_paycheck(a)
    bills = bills_before(profile, a.today, nxt["days"])
    from_paychecks = round(sum(d["invested"] for d in a.deposits), 2)
    moved = round(sum(x["amount"] for x in activity_all if _bucket(x) == "invested"), 2)
    inv = {"value": value, "contributed": a.contributed, "gain": round(value - a.contributed, 2),
           "starting": round(a.contributed - from_paychecks, 2), "from_paychecks": from_paychecks,
           "moved_by_bank": moved, "top": [], "day_change": 0.0, "day_pct": 0.0}
    if has_fund:
        t = _today(a)
        inv.update(day_change=t["day_change"], day_pct=t["day_pct"],
                   top=[dict(r, share=r["value"] / value if value else 0.0) for r in t["rows"][:TOP_HOLDINGS]])
    return {
        "accounts": bank_accounts(s.customer_id, profile, value, has_fund),
        "next_pay": nxt, "bills_due": bills, "bills_total": round(sum(b["amount"] for b in bills), 2),
        "flow": money_flow(activity_all), "feed": activity_all[:ACTIVITY_ROWS], "inv": inv, "has_fund": has_fund,
    }


def price_note(ctx: dict) -> str:
    """Same wording as the Portfolio tab's price badge."""
    if ctx["fast_forwarded"]:
        return f"Demo date {ctx['a'].today.strftime('%b %-d, %Y')}: prices simulated"
    return "Live prices · Nasdaq, delayed" if ctx["live_market"] else "Simulated prices"


@router.get("/p/{cid}/money", response_class=HTMLResponse)
def money(request: Request, cid: str):
    s = _session(cid)
    if not s.bank_linked:   # the bank-link step (onboarding) hasn't happened yet
        return RedirectResponse(f"/p/{cid}", status_code=303)
    ctx = _ctx(request, cid, tab="money")
    activity_all = nessie.recent_activity(cid, limit=None)
    ctx.update(money_context(s, ctx["profile"], activity_all))
    ctx["price_note"] = price_note(ctx)
    return _render("money.html", ctx)
