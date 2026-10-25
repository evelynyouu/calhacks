# Task: show and run FirstFund's new custom index fund (Ethan's half)

## Setup
```bash
git fetch origin
git checkout -b ethan/custom-index-ui origin/evelyn/custom-index   # after PR #5 merges: origin/lance/fund-builder
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
pytest -q   # should pass before you change anything
```
Push `ethan/custom-index-ui` and open a PR into `lance/fund-builder`. Don't push to `lance/fund-builder` or `main`, and don't rebase or force-push shared branches.

## Background
Evelyn is replacing the fund's three-fund core (VTI/VXUS/BND plus small slices) with the customer's own index. Her plan is `docs/superpowers/plans/2026-10-24-custom-index-fund.md`; read its **Contract** section first. The new fund has six slices:
- **Bonds** (BND)
- **International** (VXUS, 30% of the stock part)
- **Your US index**: about 50 US companies weighted by size, none over 6% of the slice
- **Where you spend**: companies the customer buys from, read from Nessie purchases, 5–12% by risk
- **Your interests**: each interest's 2 biggest companies, or its theme fund if there are none
- **Companies you added**

No company reaches 8% of a fresh fund. A fund now has roughly 55–65 holdings instead of 5–8.

Your half is everything that **shows or uses** the fund. Evelyn's half decides **what's in it**. You work in parallel against the contract:
- `Holding.sector`, `Holding.slice`, the `SLICES` labels
- `Portfolio.slice_weights()` and `Portfolio.by_slice()`
- `explain.build(acct)["slices"]`
- the new role `"spend"`

Until her branch lands, use `tests/fund_helpers.make_portfolio_fixture()` (step 0 below) to get a 60-holding Portfolio that matches the contract.

Ground rules:
- **Claude never generates numbers.** Python computes, Jinja renders.
- **Claude never tells a customer which stocks to buy.** The engine picks by rules, and the UI shows which rule picked each one.
- **Be honest:** label approximations ("approximate", "estimated", "assumed returns").
- **"Buy where you shop" is personalization, not a prediction.** The UI must say that wherever the spend slice appears.

## What to build
0. **A contract fixture.** In `tests/fund_helpers.py`, add `make_portfolio_fixture() -> Portfolio`:
   - 50 `us_core` companies, plus BND, VXUS, 3 `spend`, 2 `interest` and 1 `added`;
   - with `sector` and `slice` set, weights summing to 1, and a reason on every holding.

   Add the contract fields to `core/models.py` exactly as the plan's Contract section says, defaulted so the old code keeps working. If Evelyn's commit lands first, use hers.
1. **Fund card and pie** (`app/templates/partials/fund_card.html`, `app/routes/pages.py::_pie`).
   - The pie shows **slices**, six wedges at most, not 60 holdings.
   - Below it, each slice is a row with its weight. Clicking the row expands its holdings. The US index groups by sector.
   - Keep the "Heads up" and override blocks.
2. **Portfolio tab "today" list** (`partials/today.html`, `pages._today`). Show the 10 biggest positions plus a "Show all N" toggle. The day's dollar move still sums every holding.
3. **"How your fund was built" page layout** (`partials/how_built.html`).
   - Render `how.slices`: one card per slice, holdings grouped, each holding's texts underneath.
   - The US index collapses to sectors with a show-all.
   - Keep the rule that templates hold no numbers (`test_templates_hard_code_no_numbers`).
4. **Report** (`core/report.py`, `app/templates/report.html`).
   - `benchmark_weights`: the US stock share is every holding that isn't BND or VXUS, and that share benchmarks to VTI. Today it reads the VTI weight, which no longer exists.
   - The holdings table is grouped by slice, with slice subtotals.
   - "Each pick vs the core it replaced" now covers the spend and interest slices.
   - Look-through: companies held directly count directly (VXUS still looks through `holdings.csv`). Sector exposure uses `Holding.sector`, and drops the `sectors.csv` lookup for directly held companies.
   - The recommendations still fire on the same thresholds. Add a test that a fresh custom fund gives no "diversify" recommendation, since no company is at 8% or more.
5. **Projection** (`core/sim.py`, Saahil's file; tell him).
   - Treat the `us_core` slice as one diversified US basket using the `VTI` row in `data/assumptions.csv`.
   - Spend, interest and added companies keep the `company` row.
   - Without this, 50 single-company volatilities with your correlation table will misstate the fund's spread.
   - Test: the projection for a custom fund matches the old VTI-core fund's, within 1 point of volatility, at the same bond share.
6. **Paycheck buying** (`core/rebalance.py::allocate_new_money`, `core/fund.py::invest`).
   - With 60 holdings, one paycheck would place about 60 tiny orders.
   - Buy only the `NEW_MONEY_PICKS = 10` most underweight positions each paycheck, keeping the $1 broker minimum. The rest catch up on later paychecks.
   - Test: weights converge to target over 26 paychecks, and no order is under $1.
7. **Chat tools** (`agent/tools.py`, `agent/prompts.py`).
   - `get_fund` returns slices with their holdings, not a flat list of 60.
   - `get_fund_explanation(ticker)` already works per holding; add `slice` and `sector` to its output.
   - Update the prompt's tool descriptions and the "don't tell customers which stocks to buy" line. The engine picked these by rule, so Claude explains the rule and doesn't endorse the stock.

## Tests
- Every page renders for all four personas with a 60-holding fund: `/p/{cid}`, `/how`, `/report`, `/chat`, `/account`.
- The pie never has more than 6 wedges.
- Report: the benchmark weights sum to 1 and match the fund's stock/bond split; a fresh custom fund gets no diversify recommendation.
- Projection volatility matches the old VTI-core fund's within 1 point.
- Paycheck buying converges in 26 paychecks, with no order under $1 and no more than 10 orders per paycheck.
- Keep the existing tests passing, or update them deliberately and say why in the commit.

## Constraints
- Plain words; define any jargon in one line. Match the existing theme (`app/static/app.css`) and the HTMX partial patterns.
- Small commits, roughly one per item above.
- `pytest -q` and `bash scripts/git-hooks/pre-commit` must pass before each push. Never commit `.env` or keys.

## When done, report (also in the PR)
- What changed per commit.
- Screenshots: the fund card (collapsed and expanded), the How page and the report, for Sam and Alex.
- The projection before and after for Alex.
- What's still approximate.
- Test count.
