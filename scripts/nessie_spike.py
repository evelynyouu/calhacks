"""Live smoke test of core/nessie.py against the real Nessie API.  OWNER: Evelyn.

Creates a throwaway customer with a checking and a savings account, writes one of each
transaction type through our client, reads everything back, checks our ledger balance, and
deletes both accounts. (Nessie can't delete customers, so an empty "Spike Test" customer stays.)

What it found on 2026-10-24, and what the client does about it:
- Nessie never applies transactions to an account's `balance`, so core.nessie.account_balance
  computes balances from our tagged transactions instead.
- Backdated transaction dates are kept as sent.
- Transfers have no destination field, so a move is a withdrawal + deposit pair.
- Purchases take purchase_date and need a status; bills need a payment_date; amounts are whole
  dollars. Leaving these out makes reading the list back fail.
- An empty transfer list comes back as a 404.

Usage: .venv/bin/python scripts/nessie_spike.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import nessie   # noqa: E402


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{f'  ({detail})' if detail else ''}")
    return ok


def main() -> int:
    nessie.CACHE_SECONDS = 0
    results = []
    cust = nessie.create_customer("Spike", "Test")["_id"]
    chk = nessie.create_account(cust, "Checking", "spike checking", 1000)["_id"]
    sav = nessie.create_account(cust, "Savings", "spike savings", 500)["_id"]
    try:
        merchant = nessie.load_ids().get("_merchants", {}).get("groceries") \
            or nessie.create_merchant("FirstFund Spike", "Testing")["_id"]
        print("writes:")
        nessie.create_deposit(chk, 100, "2026-09-01", "[payroll] spike")
        nessie.create_withdrawal(chk, 30, "2026-09-02", "[event] spike")
        nessie.create_purchase(chk, merchant, 40, "2026-09-03", "[spend] spike")
        nessie.create_transfer(chk, sav, 50, "2026-09-04", "[firstfund] spike")
        nessie.create_bill(chk, "Landlord", "Rent", 1100, 3, "2026-11-03")
        print("  all five writes accepted")

        print("reads:")
        deposits = nessie.get_deposits(chk)
        results.append(check("backdated deposit date kept",
                             any(d["transaction_date"] == "2026-09-01" for d in deposits)))
        results.append(check("purchases read back", len(nessie.get_purchases(chk)) == 1))
        bills = nessie.get_bills(chk)
        results.append(check("bills read back", [b["nickname"] for b in bills] == ["Rent"]))
        results.append(check("empty transfer list reads as []", nessie.get_transfers(sav) == []))

        print("balances:")
        results.append(check("ledger checking", nessie.account_balance(chk, 1000) == 980, "1000 + 100 - 30 - 40 - 50"))
        results.append(check("ledger savings", nessie.account_balance(sav, 500) == 550, "500 + 50"))
        print(f"  Nessie's own balance field: checking={nessie.get_account(chk)['balance']}, "
              f"savings={nessie.get_account(sav)['balance']} (it doesn't apply transactions)")
    finally:
        nessie.delete_account(chk)
        nessie.delete_account(sav)
        print("cleanup: both spike accounts deleted")
    print("PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
