# FirstFund: Evelyn (Nessie + money flow) scoping plan

## Context
FirstFund's whole loop starts from the bank data: readiness, fund, payday, then report. Person 3 (engine) and Person 4 (web + agent) both read `FinancialProfile`, so its shape and a fixture-backed stub have to ship first. Everything else (seeding, payday, life events) has to produce numbers that land each demo persona on its expected result. Ground rule: Python computes every number, Claude only explains.

Decisions so far:
- **Cushion** = savings + *spare* checking (checking left after bills due before next payday, prorated spending, and a $200 buffer). Payday never draws from savings.
- **Market drop** = a real withdrawal on the Investing account, so Nessie's Investing balance is always the market value. The tier is allowed to react (Alex goes from 4 to 3).

The repo is currently empty, so everything here is new. Conventions: all money is `float` dollars rounded to 2 places at boundaries. A **sim clock** (`as_of`) is used everywhere and `date.today()` is never called.

---

## 1. FinancialProfile (`core/models.py`, shared with the team)

Pydantic models, so FastAPI and Jinja can use them directly. Source key: **N** = read straight from Nessie, **D** = derived in profile.py, **P** = personas.json, **S** = local demo state (`data/state.json`, gitignored).

```python
class UpcomingBill(BaseModel):
    name: str                 # N  bill.nickname / payee
    category: Literal["rent","utility","phone","internet","transit","subscription","other"]  # P (Nessie has no category)
    amount: float             # N  bill.payment_amount
    due_date: date            # D  next occurrence of bill.recurring_date on/after as_of
    account_id: str           # N

class FinancialProfile(BaseModel):
    persona: str              # P  "maria" | "jordan" | "sam" | "alex"
    customer_id: str          # N
    name: str                 # N  customer.first_name
    as_of: date               # S  sim clock

    checking_balance: float   # N
    savings_balance: float    # N
    investing_balance: float  # N  "Savings"-type account nicknamed "FirstFund Investing" = market value
    card_balance: float       # N  Credit Card account balance (0 if none)
    card_apr: float           # P  e.g. 0.2499 (Nessie has no APR)

    paycheck_amount: float    # D  latest [payroll] deposit, overridden by S.raise
    pay_frequency: Literal["biweekly","semimonthly","monthly"]  # P
    last_payday: date         # D  latest [payroll] deposit date
    next_payday: date         # D  last_payday + frequency
    monthly_income: float     # D  paycheck_amount * periods_per_year / 12

    monthly_bills: float      # D  sum of recurring Nessie bills
    monthly_spending: float   # D  purchases over the last 60 days / 2, excluding [event]-tagged
    monthly_expenses: float   # D  bills + spending
    monthly_surplus: float    # D  income - expenses

    upcoming_bills: list[UpcomingBill]  # D  sorted by due_date, next 35 days
    cushion_cash: float       # D  savings + max(0, checking - due_before_payday - spend_until_payday - BUFFER)
    cushion_months: float     # D  cushion_cash / monthly_expenses
```

Boundary: profile.py only **measures**. readiness.py (Lance's rules) **decides**: it reads `card_balance`, `card_apr`, `cushion_months`, `monthly_surplus` and `investing_balance` (for the tier). Tagging convention for Nessie `description` fields: `[payroll]`, `[payroll-adj]`, `[event] ...`, `[firstfund] ...`. This keeps derivation unambiguous.

## 2. Nessie surface area (`core/nessie.py`)

Base `https://api.nessieisreal.com` (HTTPS only; port 80 is refused), `?key=` with one shared team key.

| Endpoint | Read | Write (who) |
|---|---|---|
| `GET/POST /customers`, `GET /customers/{id}` | profile | seed |
| `GET/POST /customers/{id}/accounts`, `GET /accounts/{id}` | balances | seed |
| `DELETE /accounts/{id}` | — | seed `--reset` (own IDs only, **never** `DELETE /data`) |
| `GET/POST /accounts/{id}/deposits` | income | seed history, fast-forward paycheck, raise adj |
| `GET/POST /accounts/{id}/withdrawals` | — | market drop |
| `GET/POST /accounts/{id}/purchases`, `GET/POST /merchants` | spending | seed history, car repair |
| `GET/POST /accounts/{id}/bills` | bills + due days | seed |
| `GET/POST /accounts/{id}/transfers` | — | payday checking→Investing, car repair savings→checking |

Gaps and workarounds:
- **No Investing account type.** Use a `Savings` account nicknamed "FirstFund Investing". Store its ID in `data/nessie_ids.json`.
- **No recurring paychecks.** `pay_frequency` and an anchor date go in personas.json. Seed backdates about 2 months of `[payroll]` deposits. Fast-forward writes the next one.
- **No APR and no bill category.** Both go in personas.json, keyed by bill nickname.
- **Bill due dates.** Nessie bills do have `recurring_date` (day of month). Derive the next due date from `as_of`.
- **No notion of "today."** Use the sim clock in state.json. Fast-forward advances it.
- **Balance semantics unknown.** Nessie may not apply POSTed transactions to `balance`, because they can stay `pending`. A 20-minute spike decides this. Fallback: `balance = seed_balance + Σ our tagged transactions since seed`, computed in nessie.py.
- **Outages.** Use `NESSIE_MODE=fixture|live`. Fixture mode builds profiles from personas.json, and live mode caches the last good profile to `data/cache/{persona}.json`.

## 3. Seeding (`scripts/seed_nessie.py`)

The script is idempotent: it reads and writes `data/nessie_ids.json`, and `--reset <persona>` deletes and recreates only that persona's accounts. For each persona it creates the customer, then checking, savings, Investing and (optionally) credit card accounts, then recurring bills, about 4 backdated paychecks and about 8 weekly purchases summing to the spending figure. The opening balance is solved so that the post-history balance equals the target. Sim date: **Fri 2026-10-23**, payday **Fri 10-30**, next **11-13**, biweekly.

| | Maria | Jordan | Sam | Alex |
|---|---|---|---|---|
| Paycheck (biweekly) | 1,250 | 1,150 | 1,400 | 3,400 |
| Monthly income (×26/12) | 2,708 | 2,492 | 3,033 | 7,367 |
| Rent / other bills | 950 / 200 | 1,000 / 200 | 1,100 (due 3rd) / 200 | 2,200 / 300 |
| Spending | 900 | 1,000 | 900 | 1,800 |
| Expenses | 2,050 | 2,200 | 2,200 | 4,300 |
| Surplus | 658 | 292 | 833 | 3,067 |
| Checking / Savings | 600 / 2,500 | 650 / 900 | 430 / 7,000 | 3,000 / 20,000 |
| Card balance @ APR | **1,200 @ 24.99%** | 0 | 0 | 0 |
| Cushion months | 1.22 | **0.41** | **3.18** | 4.65 |
| Investing | 0 | 0 | **3,000** | **65,000** |
| Expected | Not yet (card) | Start, $25/mo | Ready, Tier 1 | Ready, Tier 4 |

Why each lands:
- **Maria:** card debt > 0, and the debt rule is evaluated first. Paying the card is a "guaranteed 25%," using `card_apr`.
- **Jordan:** no debt, and 900/2,200 = 0.41 < 1 month, so build the cushion with the $25 habit.
- **Sam:** no debt, and 7,000/2,200 = 3.18 ≥ 3. Spare checking = max(0, 430 − 1,160 − 420 − 200) = 0. Investing is $3K, so Tier 1.
- **Alex:** no debt. 20,000/4,300 = 4.65, plus spare checking. Investing is $65K, so Tier 4.

Edge personas for `test_readiness.py` (Lance owns the answers): a card balance paid in full, cushion exactly 1.0 and 3.0, negative surplus, no savings account, a $50K boundary, and someone with a high cushion but a $40 card balance.

## 4. Payday safety check (`core/payday.py`, pure)

```python
BUFFER = 200.0; STEP = 5.0

def safety_check(checking_after_deposit: float, planned: float, as_of: date,
                 next_payday: date, upcoming_bills: list[UpcomingBill],
                 monthly_spending: float) -> PaydayDecision:
    due = [b for b in upcoming_bills if as_of <= b.due_date <= next_payday]   # inclusive both ends
    bills_due = sum(b.amount for b in due)
    spend = monthly_spending * (next_payday - as_of).days / 30
    available = checking_after_deposit - bills_due - spend - BUFFER
    invest = min(planned, max(0.0, floor(available / STEP) * STEP))
    return PaydayDecision(invest=invest, planned=planned, held_back=planned - invest,
                          bills_due=due, spend_reserved=spend, buffer=BUFFER,
                          available=available, reason=<enum: full | reduced_for_bills | skipped>)
```

The rule: invest only what's left after every bill due before the next paycheck, the prorated spending until then, and a $200 buffer. Round down to $5 and cap at the planned amount. `planned` comes from readiness (`safe_monthly × 12/26`, rounded down to $5). The demo assumes Lance's share is about 40% of surplus: $333/mo becomes **$150/check**.

Demo math (Sam, 10-30): 430 + 1,400 = 1,830 → 1,830 − 1,160 (rent 1,100 on 11-03 + phone 60 on 11-08) − 420 (900 × 14/30) − 200 = **50**, so invest $50 and hold back $100 because of rent.

**When the amount is $0:** no Nessie transfer and no Alpaca order. The decision is still returned with `reason="skipped"` so Claude can explain it ("rent is due in 4 days; we'll catch up next paycheck"). No catch-up debt is carried. Edge cases: `planned ≤ 0` (not-yet personas) returns 0 immediately. Negative `available` clamps to 0. A bill due today or on the next payday counts. `next_payday ≤ as_of` raises. An empty bill list is fine.

`fast_forward_to_payday(persona)` covers the side effects: advance the clock, POST the `[payroll]` deposit, rebuild the profile, call readiness for `planned`, run `safety_check`, and if invest > 0, POST a `[firstfund]` transfer from checking to Investing and call `broker.buy_underweight(invest)` (Person 3). It returns `PaydayResult` (decision, transfer_id, order, new profile).

## 5. Life events (`apply_life_event(persona, event) -> FinancialProfile`)

| Button | Nessie writes | Profile change → app reaction (Sam) |
|---|---|---|
| Unexpected $600 car repair | `[event]` purchase of $600 on checking at "Mike's Auto"; transfer savings → checking for min(600, savings); any remainder goes as a purchase on the credit card | savings 7,000 → 6,400; cushion 3.18 → **2.91**, so Ready **→ Start small**. Spending average unchanged (event-tagged). If savings is short, card debt appears, so the result is **Not yet**. |
| Got a raise | state `paycheck_override = paycheck + 200`; one `[payroll-adj]` $200 deposit to checking | income 3,033 → 3,467, surplus 833 → 1,267, so the safe amount rises. Next fast-forward: 2,030 − 1,780 = 250 available, so it invests **$230** (planned) instead of $50 |
| Market drops 30% | `[event]` withdrawal of 30% × Investing balance on the Investing account | Sam 3,000 → 2,100 (Tier 1 holds). Alex 65,000 → 45,500, so **Tier 4 → 3** (weekly → monthly, TLH badge hidden). Person 3's crash test reads the same balance |

Each event returns the rebuilt profile. Person 4 re-renders the readiness and fund cards via HTMX. A **Reset demo** control calls `seed --reset <persona>` and resets state.json, which is required for rehearsals.

## 6. Risks at 1 AM, and what to stub first

1. Nessie doesn't update balances, or rejects backdated `transaction_date`. Spike first; ledger fallback.
2. Nessie or venue wifi is down. Fixture mode is the default for Persons 3 and 4, plus a cached live profile.
3. Shared-key accidents: double seeding creates duplicate customers, and someone may run `DELETE /data`. The ID-file idempotency and reset-by-ID cover this. Tell the team never to call `/data`.
4. Clock drift: anything calling `date.today()` breaks "rent due in 4 days." Use the sim clock everywhere.
5. Demo state not resettable between rehearsals. Build the reset early.
6. Float noise in displayed money. Round at model boundaries, and floor the invest amount to $5.

**Stub first (pushed within the first hour):** `core/models.py` (FinancialProfile, UpcomingBill, PaydayDecision, PaydayResult), `data/personas.json` with the 4 personas above, `profile.get_profile(persona)` in fixture mode, `profile.list_personas()`, plus `fast_forward_to_payday` and `apply_life_event` returning hard-coded post-event profiles. Persons 3 and 4 code against these signatures.

## Order of work tonight
1. Nessie spike (~20 min): create a throwaway customer and account, POST a backdated deposit, re-GET the balance. This settles risks 1 and 3.
2. `core/models.py` + `personas.json` + fixture `get_profile`, then push and post signatures in team chat.
3. `payday.safety_check` + `tests/test_payday.py` (the $50 demo case, the $0 case, bill on boundary day, planned=0).
4. `core/nessie.py` thin client (get/post helpers, timeouts, retries, ledger fallback if needed).
5. `seed_nessie.py`: run it, commit `nessie_ids.json` (IDs only, never the key).
6. Live `get_profile` derivation; check that it matches fixture numbers for all 4 personas.
7. Stubbed `fast_forward_to_payday` / `apply_life_event` wired to Person 4's buttons. Do real Nessie writes and the Alpaca hook with Person 3 on Saturday 1–6 PM.

## Verification
- `pytest tests/test_payday.py tests/test_profile.py`: profile from fixtures gives the cushion and surplus numbers in the table, and the safety check returns 50 and 0 in the demo cases.
- After seeding: run `python -m core.profile sam --live`, and its output matches fixture output to within $1.
- Run each button, then reset, twice in a row with no drift. Hand Lance the profiles for `test_readiness.py`.
