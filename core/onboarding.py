"""New customers and the demo bank link.  OWNER: Lance (bank side reused from Evelyn's core/seed.py).

A new customer fills in a short form; we make a mock bank customer for them, shaped exactly like a
persona in data/personas.json, so every screen that calls get_profile(cid) works the same:
- fixture mode: their starting numbers are registered in core/nessie.py's in-memory stub;
- live mode: the same numbers are also written to the Nessie sandbox by core/seed.py (customer,
  Checking / Savings / FirstFund Investing / a $0 credit card, two months of tagged history).
The bank link step then shows those accounts and records what the customer agreed to.
Python computes every number here; nothing is a real bank.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
from dataclasses import dataclass

from core import nessie, seed
from core.profile import get_profile

BANK = "Capital One"
BANK_NOTE = "Nessie sandbox"   # Capital One's public test API: fake customers, fake money

# key -> (label, days between paydays on the demo clock, paychecks per month)
# Every 2 weeks counts as 2 a month, the same convention the personas use; weekly counts as 4.
FREQUENCIES = {
    "weekly": ("Every week", 7, 4),
    "biweekly": ("Every 2 weeks", 14, 2),
    "semimonthly": ("Twice a month", 15, 2),
    "monthly": ("Once a month", 30, 1),
}
DEFAULT_FREQUENCY = "biweekly"

MAX_PAYCHECK = 50_000
MAX_BALANCE = 1_000_000
MAX_NAME = 30
DAYS_TO_PAYDAY = 3        # the next paycheck lands 3 days after the demo starts
BILL_DUE_IN = 6           # the monthly bills come due 6 days in
SAVE_SHARE = 0.10         # history: about 10% of past paychecks went to savings (never more than savings holds)
NAME_OK = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ' .-]*$")


@dataclass
class NewCustomer:
    name: str
    paycheck: float
    frequency: str
    checking: float
    savings: float = 0.0
    bills: float = 0.0


class SignupError(Exception):
    """Plain-English problems with the form, by field."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


# ---------- the form ----------

def _money(text: str, field: str, required: bool, example: str, errors: dict, top: float) -> float:
    t = (text or "").strip().lower().replace(",", "").replace("$", "").replace(" ", "")
    if not t:
        if required:
            errors[field] = f"Enter an amount, like {example}."
        return 0.0
    try:
        amount = float(t[:-1]) * 1000 if t.endswith("k") else float(t)
    except ValueError:
        errors[field] = f"Type a dollar amount, like {example}."
        return 0.0
    if not math.isfinite(amount) or amount < 0:
        errors[field] = "That can't be negative."
        return 0.0
    if amount > top:
        errors[field] = f"For the demo, keep it at ${top:,.0f} or less."
        return 0.0
    return float(round(amount))   # the mock bank (Nessie) keeps whole dollars


def parse_form(name: str = "", paycheck: str = "", frequency: str = "", checking: str = "",
               savings: str = "", bills: str = "") -> NewCustomer:
    """The new-customer form -> NewCustomer, or SignupError with a message per field."""
    errors: dict[str, str] = {}
    name = " ".join((name or "").split())
    if not name:
        errors["name"] = "Enter your first name."
    elif len(name) > MAX_NAME or not NAME_OK.match(name):
        errors["name"] = f"Use letters only (up to {MAX_NAME}), like Maya."
    pay = _money(paycheck, "paycheck", True, "1,850", errors, MAX_PAYCHECK)
    if "paycheck" not in errors and pay <= 0:
        errors["paycheck"] = "Your take-home pay has to be more than $0 to invest part of it."
    if frequency not in FREQUENCIES:
        errors["frequency"] = "Pick how often you're paid."
    chk = _money(checking, "checking", True, "600 (0 is fine)", errors, MAX_BALANCE)
    sav = _money(savings, "savings", False, "1,500", errors, MAX_BALANCE)
    bil = _money(bills, "bills", False, "1,200", errors, MAX_PAYCHECK * 4)
    if errors:
        raise SignupError(errors)
    return NewCustomer(name=name[:1].upper() + name[1:], paycheck=pay, frequency=frequency,
                       checking=chk, savings=sav, bills=bil)


# ---------- the mock bank customer ----------

def starting_numbers(c: NewCustomer) -> dict:
    """A personas.json-shaped record for this customer. Spending isn't asked: it's solved so the two
    months of history add up (paychecks in = spending + bills + a little saved), which keeps every
    opening balance a whole, non-negative dollar amount."""
    label, period, per_month = FREQUENCIES[c.frequency]
    bills = [{"name": "Rent and bills", "amount": c.bills, "due_in_days": BILL_DUE_IN}] if c.bills else []
    p = {
        "name": c.name, "blurb": f"New customer, paid {label.lower()}", "new_customer": True,
        "pay_frequency": c.frequency, "pay_period_days": period, "paychecks_per_month": per_month,
        "monthly_income": round(c.paycheck * per_month, 2), "monthly_bills": c.bills, "monthly_spending": 0.0,
        "checking_balance": c.checking, "cash_cushion": c.savings, "card_debt": 0.0, "card_apr": 0.0,
        "invested_balance": 0.0, "paycheck_amount": c.paycheck, "days_to_payday": DAYS_TO_PAYDAY,
        "upcoming_bills": bills,
    }
    plan = seed.plan_from(nessie._with_other_bills(dict(p, upcoming_bills=list(bills))), check=False)
    pay_total = c.paycheck * len(plan["paydays"])
    bills_paid = sum(x["amount"] for x in plan["bill_payments"])
    keep = min(c.savings, float(round(SAVE_SHARE * pay_total)))
    p["monthly_spending"] = max(0.0, pay_total - bills_paid - keep) / 2   # two months of purchases
    seed.plan_from(nessie._with_other_bills(dict(p, upcoming_bills=list(bills))))   # raises if it doesn't add up
    return p


def new_id() -> str:
    while True:
        cid = f"{nessie.NEW_PREFIX}{secrets.token_hex(4)}"
        if cid not in nessie._SEED and (nessie.mode() != nessie.LIVE or cid not in nessie.load_ids()):
            return cid


class BankUnavailable(Exception):
    """Live mode, and the Nessie sandbox couldn't take the new customer."""


def create(c: NewCustomer) -> str:
    """Make the mock bank customer and return their id. Live mode writes them to Nessie too."""
    cid = new_id()
    nessie.register_customer(cid, starting_numbers(c))
    if nessie.mode() == nessie.LIVE:
        try:
            seed.seed_new_customer(cid)
        except Exception as e:
            nessie.forget_customer(cid)
            if nessie.needs_fallback(e) or isinstance(e, nessie.NessieError):
                raise BankUnavailable(nessie.describe_failure(e)) from None
            raise
    return cid


# ---------- the link step ----------

def mask(account_ref: str) -> str:
    """A stable, made-up 4-digit account ending (the mock bank has no real account numbers)."""
    return f"{int(hashlib.sha256(account_ref.encode()).hexdigest(), 16) % 10_000:04d}"


def ensure_investing(cid: str) -> None:
    """Every seeded customer has a FirstFund Investing account; make one if live ids lack it."""
    if nessie.mode() != nessie.LIVE:
        return
    entry = nessie.load_ids().get(cid)
    if not entry or "investing" in entry["accounts"]:
        return
    aid = nessie.create_account(entry["customer_id"], "Savings", "FirstFund Investing", 0)["_id"]

    def add(ids):
        ids[cid]["accounts"]["investing"] = aid
        ids[cid]["seed"]["investing"] = 0.0
    seed._update_ids(add)


def frequency_label(cid: str) -> str:
    return FREQUENCIES[nessie.persona_seed(cid).get("pay_frequency", DEFAULT_FREQUENCY)][0].lower()


def accounts(cid: str) -> list[dict]:
    """The customer's accounts at the mock bank, with balances from their profile (the same numbers
    every screen shows). role: "paycheck" (checking), "invest" (FirstFund Investing), or "untouched"."""
    p = get_profile(cid)
    refs = {k: f"{cid}:{k}" for k in ("checking", "savings", "investing", "card")}
    names = {"checking": f"{p.name} Checking", "savings": f"{p.name} Savings",
             "investing": "FirstFund Investing", "card": f"{p.name} Card"}
    if nessie.mode() == nessie.LIVE:
        entry = nessie.load_ids().get(cid, {})
        refs.update(entry.get("accounts", {}))
        try:
            nick = {a["_id"]: a.get("nickname") for a in nessie.get_accounts(entry["customer_id"])}
            names.update({k: nick[aid] for k, aid in entry.get("accounts", {}).items() if nick.get(aid)})
        except Exception:   # names are cosmetic; the balances already came from the profile
            pass
    rows = [
        {"key": "checking", "type": "Checking", "role": "paycheck", "balance": p.checking_balance},
        {"key": "investing", "type": "Investing", "role": "invest", "balance": p.invested_balance},
        {"key": "savings", "type": "Savings", "role": "untouched", "balance": p.cash_cushion},
        {"key": "card", "type": "Credit card", "role": "untouched", "balance": p.card_debt, "owed": True},
    ]
    for r in rows:
        r.update(name=names[r["key"]], mask=mask(refs[r["key"]]))
    return rows


def link(cid: str) -> dict:
    """What the session stores once the customer links: the bank, the two account endings, and a
    one-line note the intake chat opens with."""
    ensure_investing(cid)
    rows = {r["key"]: r for r in accounts(cid)}
    p = get_profile(cid)
    chk, inv = rows["checking"]["mask"], rows["investing"]["mask"]
    where = BANK_NOTE if nessie.mode() == nessie.LIVE else "in-memory demo data"
    return {
        "bank": f"{BANK} ({BANK_NOTE})", "checking": chk, "investing": inv,
        "note": (f"Linked: {BANK} checking ••{chk} and FirstFund Investing ••{inv} (demo bank, {where}). "
                 f"Paycheck ${p.paycheck_amount:,.0f} {frequency_label(cid)}."),
    }
