# FirstFund

Your own index fund, built around you. Every paycheck invests itself.

A short chat builds a personal fund: a broad core (US stocks, international stocks, bonds, mixed by how much
risk you're comfortable with) plus a slice for the industries you pick. You choose what percent of each
paycheck goes in; it's invested the moment the paycheck lands. A report covers performance, every holding, the
companies inside your funds, your industries, the broad market, and recommendations, with trades you confirm.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY (optional: works offline without it)
git config core.hooksPath scripts/git-hooks   # blocks commits containing .env or any key from it
uvicorn app.main:app --reload
pytest -q
```

**Bank side / Nessie:** how to plug in, demo vs. live mode, and the reset rule are in
[`docs/TEAM_INTEGRATION.md`](docs/TEAM_INTEGRATION.md). Never commit `.env`: keys go there and in
Render's Environment settings only.

Open http://localhost:8000. With no API key the intake still works (its questions are fixed) and chat after it
answers from the report offline, so the demo never dies on wifi. Market prices are simulated and labeled as such
(`core/market.py`); trades go to a simulated broker unless `BROKER=alpaca` plus Alpaca keys are set
(`ALPACA_PAPER=false` for real money; `pip install alpaca-py`).

## Who owns what

| Path | Owner | Status |
|---|---|---|
| `core/models.py` | **Everyone**: change only after telling the team | Shared contract |
| `core/nessie.py`, `core/profile.py`, `scripts/seed_nessie.py`, `core/payday.py` (flow) | Evelyn | Fixture + live Nessie mode; see `docs/EVELYN_API.md` |
| `core/portfolio.py`, `core/sim.py`, `core/rebalance.py`, `core/broker.py`, `scripts/cache_prices.py` | Saahil | Working; sim and worst-year numbers are placeholders |
| `core/intake.py`, `core/fund.py`, `core/report.py`, `core/market.py`, `core/policy.py`, `agent/prompts.py`, `data/*.csv`, `data/personas.json`, `tests/` | Lance | Questions + risk score, paycheck autopilot, report + recommendations, simulated market |
| `core/readiness.py` | Lance | Legacy: only the old `core/payday.py` flow uses it; the app no longer does |
| `app/`, `agent/claude_agent.py`, `agent/tools.py` | Ethan | Working |

**Replacing a stub:** keep the function signature and return type from `core/models.py`. The web layer and Claude tools call these directly, so if the signature holds, nothing else changes.

## Function contract (what the web layer calls)

| Function | Returns |
|---|---|
| `profile.get_profile(cid)` | `FinancialProfile` |
| `policy.tier_for(balance)` | `TierPolicy` |
| `intake.parse(question, value, values, other)` | `(answer, text shown in chat)` or `AnswerError` |
| `intake.risk_score(answers)` / `intake.risk_level(score)` | `int` 0–100 / `int` 1–4 |
| `portfolio.build_portfolio(risk_level, interests)` | `Portfolio` |
| `sim.simulate(portfolio, start, monthly, years=10)` | `SimResult` (p10/p50/p90 per year) |
| `fund.start(account)` / `fund.paycheck(account)` / `fund.confirm_pending(account)` | trades placed |
| `report.build_report(account)` | report dict (summary, holdings, companies, interests, market, recommendations, …) |
| `broker.get_broker()` | `SimBroker` or `AlpacaBroker` (`submit`, `positions`, `cost_basis`, `orders`) |
| `rebalance.allocate_new_money(portfolio, current, amount)` | `{ticker: dollars}` |
| `nessie.deposit_paycheck / advance_days / apply_raise / transfer_to_investing` | paycheck and raise buttons |

## Ground rule

Claude never generates numbers. Every number on screen is rendered by Jinja from a Python result. Claude's text sits beside the numbers and explains them (`agent/claude_agent.explain` is told to add no new numbers).

## How the UI updates

- HTMX posts return HTML partials from `app/templates/partials/`.
- Other panels refresh with `hx-swap-oob` (answering a question swaps in the next question's input; building the fund also refreshes the fund card). Side-panel actions post a line into the chat with `hx-swap-oob="beforeend:#chat-messages"`.
- The chat input (`partials/composer.html`) matches the question: text, dollars, slider + number box, 1–10 scale, multiple choice (≤4 options), or chips with Other.
- Charts are `<canvas data-chart="pie|fan|history" data-payload='…'>`; `app/static/app.js` initializes them after every swap.

## Demo personas

| Persona | Expected |
|---|---|
| Maria, Jordan, Sam | Start at the chat intake; Sam's $2,050 paycheck at 10% invests $205 |
| Alex | Fund started a year ago with $52K; opens on a full report, tier 4, auto trade prep on |

## Deploy (Render)

1. Push this repo to GitHub (see Git steps below).
2. In Render: **New → Blueprint**, connect the repo. Render reads `render.yaml` and creates the `firstfund` web service automatically (free plan, Python runtime, health check at `/health`).
3. After the service is created, open it in the Render dashboard → **Environment** and fill in the `sync: false` secrets: `ANTHROPIC_API_KEY`, `NESSIE_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`. (`CLAUDE_MODEL` and `ALPACA_BASE_URL` already have values from `render.yaml`.) For real Nessie data also add `NESSIE_MODE=live`; without it the site runs on demo data.
4. Trigger a deploy (push to the connected branch, or **Manual Deploy** in the dashboard).
5. Visit the service URL once it's live to confirm `/` loads.

**Cold start warning:** Render's free tier sleeps the service after inactivity, and the first request after sleeping can take 30–60s to wake up. Open the site yourself a minute or two before judging walks over, so it's warm when they see it.

## Known gaps

- Session state is in memory: one demo user at a time, resets on server restart.
- Market data is simulated; real prices would plug in behind `core/market.price()`. With `BROKER=alpaca`, one Alpaca account backs every demo customer and fills at real prices while the report still uses simulated ones.
- Company weights inside each fund (`data/holdings.csv`) are approximate.
- Tailwind runs from its CDN script; fine for a hackathon, needs internet (as does Claude).
