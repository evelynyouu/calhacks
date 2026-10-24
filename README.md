# FirstFund

Every paycheck invests itself, but never before rent is paid.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY (optional: works offline without it)
git config core.hooksPath scripts/git-hooks   # blocks commits containing .env or any key from it
uvicorn app.main:app --reload
pytest -q                     # readiness answer key
```

**Bank side / Nessie:** how to plug in, demo vs. live mode, and the reset rule are in
[`docs/TEAM_INTEGRATION.md`](docs/TEAM_INTEGRATION.md). Never commit `.env`: keys go there and in
Render's Environment settings only.

Open http://localhost:8000. With no API key, chat runs a scripted intake so the demo never dies on wifi.

## Who owns what

| Path | Owner | Status |
|---|---|---|
| `core/models.py` | **Everyone**: change only after telling the team | Shared contract |
| `core/nessie.py`, `core/profile.py`, `scripts/seed_nessie.py`, `core/payday.py` (flow) | Evelyn | Fixture + live Nessie mode; see `docs/EVELYN_API.md` |
| `core/portfolio.py`, `core/sim.py`, `core/rebalance.py`, `core/broker.py`, `scripts/cache_prices.py` | Saahil | Stub: placeholder numbers |
| `core/readiness.py`, `core/policy.py`, `agent/prompts.py`, `data/personas.json`, `data/etfs.csv`, `tests/` | Lance | v1 rules from the plan |
| `app/`, `agent/claude_agent.py`, `agent/tools.py` | Ethan | Working |

**Replacing a stub:** keep the function signature and return type from `core/models.py`. The web layer and Claude tools call these directly, so if the signature holds, nothing else changes.

## Function contract (what the web layer calls)

| Function | Returns |
|---|---|
| `profile.get_profile(cid)` | `FinancialProfile` |
| `readiness.check_readiness(profile)` | `Readiness` |
| `policy.tier_for(balance)` | `TierPolicy` |
| `portfolio.risk_from_answers(timeline_years, age, loss_reaction)` | `int` 1–4 |
| `portfolio.build_portfolio(risk_level, interests)` | `Portfolio` |
| `sim.simulate(portfolio, start, monthly, years=10)` | `SimResult` (p10/p50/p90 per year) |
| `sim.crash_test(portfolio, balance, choice, interests)` | `CrashTestResult` |
| `payday.run_payday(cid, portfolio)` | `PaydayResult` |
| `rebalance.allocate_new_money(portfolio, current, amount)` | `{ticker: dollars}` |
| `broker.buy_notional(symbol, dollars)` | `Trade` |
| `nessie.deposit_paycheck / advance_days / record_purchase / apply_raise / apply_market_move / transfer_to_investing` | life-event buttons |

## Ground rule

Claude never generates numbers. Every number on screen is rendered by Jinja from a Python result. Claude's text sits beside the numbers and explains them (`agent/claude_agent.explain` is told to add no new numbers).

## How the UI updates

- HTMX posts return HTML partials from `app/templates/partials/`.
- Other panels refresh with `hx-swap-oob` (e.g. building a fund in chat also refreshes the fund card and crash test).
- Charts are `<canvas data-chart="pie|fan" data-payload='…'>`; `app/static/app.js` initializes them after every swap.

## Demo personas

| Persona | Expected |
|---|---|
| Maria | Not yet: pay the $1,200 card first |
| Jordan | Start with $25/month |
| Sam | Ready; payday invests $50 instead of $150 because rent is due in 4 days |
| Alex | Tier 4 ($65K), starts with a fund preloaded |

## Deploy (Render)

1. Push this repo to GitHub (see Git steps below).
2. In Render: **New → Blueprint**, connect the repo. Render reads `render.yaml` and creates the `firstfund` web service automatically (free plan, Python runtime, health check at `/health`).
3. After the service is created, open it in the Render dashboard → **Environment** and fill in the `sync: false` secrets: `ANTHROPIC_API_KEY`, `NESSIE_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`. (`CLAUDE_MODEL` and `ALPACA_BASE_URL` already have values from `render.yaml`.) For real Nessie data also add `NESSIE_MODE=live`; without it the site runs on demo data.
4. Trigger a deploy (push to the connected branch, or **Manual Deploy** in the dashboard).
5. Visit the service URL once it's live to confirm `/` loads.

**Cold start warning:** Render's free tier sleeps the service after inactivity, and the first request after sleeping can take 30–60s to wake up. Open the site yourself a minute or two before judging walks over, so it's warm when they see it.

## Known gaps

- Session state is in memory: one demo user at a time, resets on server restart.
- Alpaca paper orders placed on a weekend stay "accepted" (markets closed); the UI shows `est_fill_price` from the cached last close.
- Tailwind runs from its CDN script; fine for a hackathon, needs internet (as does Claude).
