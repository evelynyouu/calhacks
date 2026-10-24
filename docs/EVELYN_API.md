# Bank side: what Saahil and Ethan call

Owner: Evelyn (`core/nessie.py`, `core/profile.py`, `core/payday.py`, `core/seed.py`, `scripts/seed_nessie.py`).
Every function below works the same in fixture and live mode. Shared types are in `core/models.py`.

## Functions

| Call | Returns | Notes |
|---|---|---|
| `profile.get_profile(cid)` | `FinancialProfile` | `KeyError` for an unknown or unseeded persona (the app shows 404) |
| `nessie.list_customers()` | `[{"id", "name", "blurb"}]` | the four personas |
| `payday.fast_forward_to_payday(cid, portfolio=None)` | `PaydayResult` | moves the clock to payday, deposits the paycheck, runs the safety check. With a `portfolio` it transfers to Investing and places trades |
| `payday.run_payday(cid, portfolio)` | `PaydayResult` | payday without moving the clock (what the route calls after `nessie.advance_days`) |
| `payday.apply_life_event(cid, event)` | `FinancialProfile` | `event` is `"car_repair"` ($600 from savings, then the card), `"raise"` (+$200 per paycheck) or `"market_drop"` (Investing -30%) |
| `payday.reset_persona(cid)` | `FinancialProfile` | back to the starting numbers. Live mode swaps in a pre-built spare copy (instant) and rebuilds the next spare in the background |

The route-level helpers keep their old signatures: `nessie.advance_days`, `deposit_paycheck`,
`record_purchase`, `apply_raise(cid, pct)`, `apply_market_move(cid, pct)`, `transfer_to_investing`, `reset`.

Payday rule: invest only what's left after bills due by the next paycheck, spending until then
(monthly spending x days / 30) and a $200 buffer, rounded down to $5 and capped at the planned amount
(readiness's safe monthly amount / 2). Savings is never touched. Sam's first payday: $50 of $150.

## NESSIE_MODE

- `fixture` (default): in-memory personas from `data/personas.json`. No key, no network; resets on restart.
- `live`: reads Nessie using the ids in `data/nessie_ids.json` (made by `python -m scripts.seed_nessie`).
  Balances are computed from our own tagged transactions: Nessie never updates its `balance` field.
  Moving money between accounts is a tagged withdrawal + deposit (Nessie transfers have no destination).

**If Nessie goes down mid-demo** (no connection, a timeout, or a 5xx), the app switches to demo data for
the rest of the run, starting from the last live numbers each persona showed, and the click that hit the
outage still completes. `nessie.offline_reason()` says why. Restart the server to go back to live.

**`data/nessie_ids.json` changes on every live reset** (the persona moves to new accounts). If you
reset in live mode, commit that file, or teammates' copies will point at deleted accounts.

`python -m core.profile --diff` compares live with fixture and flags any money difference over $1.

## Description tags

Every transaction we write starts with one of these, and only tagged transactions count toward balances:

| Tag | Written by |
|---|---|
| `[payroll]` | paychecks (seed history, fast-forward) |
| `[payroll-adj]` | the raise: deposits the first raise and records the new paycheck ("raise to 2250") |
| `[event]` | car repair, market drop |
| `[firstfund]` | payday move from checking to Investing (a withdrawal + deposit pair) |
| `[spend]` | day-to-day spending (seed history, and the days fast-forward skips) |
| `[bill]` | bill payments |
| `[seed]` | the seeder moving leftover history to savings |

Don't call `DELETE /data` with the shared key: it wipes every team's data.

## Sim clock

Never call `date.today()`. A persona's "today" is its sim clock: it starts at **2026-10-23**
(`core.state.SIM_START`) and only moves when fast-forward runs. In live mode it is read from Nessie
(the date of the newest transaction we wrote, see `core.profile.live_clock`), so a restart that wipes
local files (Render's free plan) loses nothing. The profile shows it as `days_to_payday` and each
bill's `due_in_days`.
