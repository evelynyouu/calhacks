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
| `payday.reset_persona(cid)` | `FinancialProfile` | back to the starting numbers; live mode re-seeds that persona's Nessie accounts |

The route-level helpers keep their old signatures: `nessie.advance_days`, `deposit_paycheck`,
`record_purchase`, `apply_raise(cid, pct)`, `apply_market_move(cid, pct)`, `transfer_to_investing`, `reset`.

Payday rule: invest only what's left after bills due by the next paycheck, spending until then
(monthly spending x days / 30) and a $200 buffer, rounded down to $5 and capped at the planned amount
(readiness's safe monthly amount / 2). Savings is never touched. Sam's first payday: $50 of $150.

## NESSIE_MODE

- `fixture` (default): in-memory personas from `data/personas.json`. No key, no network; resets on restart.
- `live`: reads Nessie using the ids in `data/nessie_ids.json` (made by `python -m scripts.seed_nessie`).
  Balances are computed from our own tagged transactions, not Nessie's `balance` field.
  The last good profile per persona is cached in `data/cache/`, and that copy is served if Nessie is down.

`python -m core.profile --diff` compares live with fixture and flags any money difference over $1.

## Description tags

Every transaction we write starts with one of these, and only tagged transactions count toward balances:

| Tag | Written by |
|---|---|
| `[payroll]` | paychecks (seed history, fast-forward) |
| `[payroll-adj]` | the raise button's first $200 |
| `[event]` | car repair, market drop |
| `[firstfund]` | payday transfer from checking to Investing |
| `[spend]` | day-to-day spending (seed history, and the days fast-forward skips) |
| `[bill]` | bill payments |
| `[seed]` | the seeder moving leftover history to savings |

Don't call `DELETE /data` with the shared key: it wipes every team's data.

## Sim clock

Never call `date.today()`. A persona's "today" is its sim clock: it starts at **2026-10-23**
(`core.state.SIM_START`) and only moves when fast-forward runs. In live mode it lives in
`data/state.json` (per persona: `as_of`, `paycheck_override`). The profile shows it as
`days_to_payday` and each bill's `due_in_days`.
