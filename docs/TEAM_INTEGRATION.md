# Bank side is live: what changed and how to plug in

From Evelyn. The Capital One Nessie integration is done and checked against the real API. Nothing
you call changed shape: `get_profile(cid)` still returns a `FinancialProfile`, the life-event
functions keep their names, and `core/models.py` is untouched. Details of each call are in
[`EVELYN_API.md`](EVELYN_API.md).

## Everyone: do this once

1. **Re-sync.** `main` was rewritten twice on 2026-10-24 (to drop Claude co-author lines). If you
   pulled before that: `git fetch origin && git reset --hard origin/main`. Save unpushed work first
   with `git branch my-backup`.
2. **Turn on the key guard:** `git config core.hooksPath scripts/git-hooks`. It blocks any commit that
   stages a `.env` file or contains a key from your `.env`. `pytest -q` also fails if a key is in any
   tracked file. Keys live only in `.env` (gitignored) and in Render's Environment settings.
3. **Stop Claude Code from adding itself as co-author** (it puts Claude on the repo's contributor
   list): add `"attribution": {"commit": ""}` to `~/.claude/settings.json`.
4. `pytest -q` before you push. It should be all green in both modes.

## Demo data vs. real Nessie

| `NESSIE_MODE` | What you get | Needs |
|---|---|---|
| `fixture` (default) | the four personas in memory; no network | nothing |
| `live` | real Capital One Nessie accounts, seeded for all four personas | `NESSIE_API_KEY` in `.env` |

- Develop against `fixture`. Every function behaves the same in both modes (tests compare them after
  every button).
- **If Nessie or the wifi drops mid-demo,** live mode switches to demo data starting from the numbers
  on screen, and the click still works. The money panel badge then says "Nessie offline: demo data".
  Restart the server to go back to live.
- **Reset in live mode is instant** (it swaps in a pre-built copy of the persona). It changes
  `data/nessie_ids.json`, and the new ids are also saved in Nessie, so other machines and Render pick
  them up on their own: **no need to commit the file after a reset** any more. If ids still end up
  pointing at deleted accounts (e.g. Nessie unreachable during the reset), the app falls back to demo data with "re-seed or reset
  this persona"; pressing Reset fixes it.
- Health check before a demo: `python scripts/nessie_spike.py` (prints PASS) and
  `NESSIE_MODE=live python -m core.profile --diff` (prints OK for all four).
- Never call `DELETE /data` with the shared key. It wipes every team's data.

## Ethan (app, agent)

Three PRs touching your files were merged on 2026-10-24 (Evelyn approved merging them; please skim):
- **#1:** the money panel has a bank activity feed, a "Live: Capital One Nessie" / "Demo data" /
  "Nessie offline: demo data" badge, and a payday breakdown bar. `app/routes/pages.py` passes
  `activity`, `nessie_mode` and `nessie_offline` from `_ctx`.
- **#2:** the scripted chat no longer 500s on messages after the intake is done (only hit with no
  `ANTHROPIC_API_KEY`, i.e. bad wifi). Test: `tests/test_scripted_chat.py`.
- **#3:** Tailwind, htmx and Chart.js are bundled in `app/static/vendor/`, so the page looks right and
  the buttons work with no internet (before, htmx failed to load offline and nothing was clickable).
- `run_payday` now returns `PaydayRun`, a `PaydayResult` subclass with an extra `breakdown`
  dict (the bar's numbers). Anything that takes a `PaydayResult` still works.
- For Render: set `NESSIE_MODE=live` and `NESSIE_API_KEY` in the dashboard if you want live Nessie
  there. Without them it runs on demo data, which is the safe default.

## Saahil (engine, broker)

- Payday moves the invested amount checking → "FirstFund Investing" in Nessie when there's a
  portfolio, then calls your `rebalance.allocate_new_money` + `broker.buy_notional`. With no portfolio,
  if `broker.buy_underweight(cid, amount)` exists it's called instead (optional hook).
- `profile.invested_balance` is that Investing account's balance. The market-drop button takes 30% off
  it, so your crash test and tier logic see the same number as the screen.
- The planned amount is rounded down to $5 (Nessie only moves whole dollars), so trades are in
  whole dollars.

## Lance (readiness, personas)

- `data/personas.json` is still yours and still the source of truth. The seeder builds the Nessie
  accounts from it. **If you change a persona's numbers,** re-seed that persona so live matches:
  `NESSIE_MODE=live python -m scripts.seed_nessie --reset <cid>` (the new ids are saved in Nessie too).
- If `monthly_bills` is more than the bills listed in `upcoming_bills`, the gap shows as one
  "Other bills" line (so bills actually paid add up to `monthly_bills`).
- Payday uses `safe_monthly_amount / 2` per paycheck, rounded down to $5. Sam's demo line is still
  "invested $50 of $150".
