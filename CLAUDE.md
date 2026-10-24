# FirstFund

CalHacks project. Pitch: **"Your own index fund, built around you. Every paycheck invests itself."**
(Pivot on 2026-10-24, branch `lance/fund-builder`; the old "never before rent is paid" readiness pitch is retired.)

A personalized index fund generator. A continuous chat asks exact questions (each with the right input:
typed text, dollars, slider + number box, 1–10 scale, multiple choice when ≤4 options, chips + Other),
builds a core + interests fund, invests the customer's chosen % of every paycheck the moment it lands,
and produces a report (performance, holdings, look-through companies, interests, broad market and what it
means for them, recommendations with trades the customer confirms). Advise, don't judge: no readiness gate,
no quizzing; the customer decides how much.

Team: Evelyn (bank / Nessie), Saahil (portfolio engine), Lance (rules, prompts, data, tests), Ethan (web app, Claude agent).
The user of this repo's Claude sessions is usually **Lance**.

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.13 (.python-version)
pip install -r requirements.txt
cp .env.example .env        # optional: ANTHROPIC_API_KEY, NESSIE_API_KEY, ALPACA_* keys
uvicorn app.main:app --reload   # http://localhost:8000
pytest -q                   # must pass before pushing
```

- No `ANTHROPIC_API_KEY`: chat runs a scripted intake (`agent/claude_agent.py`), so the demo works offline.
- `NESSIE_MODE=fixture` (default): in-memory personas from `data/personas.json`. `live`: real Nessie,
  seeded with `python -m scripts.seed_nessie`. See `docs/EVELYN_API.md`.
- Model is set by `CLAUDE_MODEL` (default `claude-sonnet-5-5`).

## Layout and owners

| Path | Owner | What |
|---|---|---|
| `core/models.py` | Everyone | Shared contract (dataclasses). **Don't change without telling the team.** |
| `core/readiness.py`, `core/policy.py`, `agent/prompts.py`, `data/personas.json`, `data/etfs.csv`, `tests/test_readiness.py`, `tests/test_data.py` | Lance | Readiness rules, balance tiers, Claude system prompt, demo data, answer-key tests |
| `core/nessie.py`, `core/profile.py`, `core/payday.py`, `core/seed.py`, `core/live.py`, `core/state.py`, `scripts/seed_nessie.py` | Evelyn | Capital One Nessie bank API (fixture + live mode), payday safety check, demo life-event buttons |
| `core/portfolio.py`, `core/sim.py`, `core/rebalance.py`, `core/broker.py`, `scripts/cache_prices.py` | Saahil | Risk score, fund building, 10-year p10/p50/p90 sim, crash test, allocation, Alpaca paper trades. Sim and worst-year numbers are still placeholders. |
| `app/` (FastAPI + Jinja + HTMX), `agent/claude_agent.py`, `agent/tools.py` | Ethan | Pages, partials in `app/templates/partials/`, charts in `app/static/app.js`, Claude tool loop |
| `render.yaml` | Ethan | Render deploy (see README) |

To replace a stub: keep its signature and return type from `core/models.py`; the web layer and Claude
tools call these directly. The full function contract table is in `README.md`.

## Ground rules

- **Claude never generates numbers.** Python computes every figure; Jinja renders it; Claude only explains.
  `agent/prompts.py` enforces this in the prompt, and `explain()` is told to add no new numbers.
- UI updates: HTMX posts return partials; other panels refresh via `hx-swap-oob`; charts are
  `<canvas data-chart="pie|fan" data-payload='…'>` initialized by `app.js` after each swap.
- Session state is in memory: one demo user, resets on restart.

## Lance's rules (current decisions, branch `lance/fund-builder`)

- Intake (`core/intake.py`): goal (text), starting amount (typed USD, mock), paycheck % (slider 0–100),
  risk questionnaire (priority, $1,000 one-year range, max drop slider, 1–10 comfort scale), interests (22 chips + Other
  matched by keywords). Risk score = average of the 4 risk answers, 0–100; levels in bands of 25.
- Fund (`core/portfolio.py`): core VTI/VXUS/BND mix by risk level; interest slice 10/15/20/25% (`TILT_CAP_BY_RISK`),
  split evenly, min 2% each; overlap warnings come from look-through holdings.
- Paycheck (`core/fund.py`): floor(paycheck × %) moves checking → investing via Nessie and buys the most underweight funds.
- Report (`core/report.py`): rule-based recommendations: rebalance (drift ≥5 pts, trades only at ≥$5K, else steer
  new money), invest more on a dip (−7% month or −10% from 1-yr high), hold lagging interests, diversify (one
  company ≥8%), explore steadier sectors (≥60% tech-like picks), invest less (≥50% of paycheck), else stay the course.
- Tiers (`core/policy.py`): review every 30/30/14/7/7 days; auto-prepared trades from $5K, always confirmed by the customer.
- Market data (`core/feed.py` → `core/market.py`): real Nasdaq quotes, ~2y daily closes and news RSS (no key;
  Yahoo rate-limits this IP, Stooq needs JS). Past the last real close (paydays fast-forward) and offline, prices
  follow a seeded simulated path joined onto the last close. `MARKET_DATA=sim` turns the feed off (tests force it).
  Broker: `SimBroker` default (fractional shares), `AlpacaBroker` with `BROKER=alpaca`.
- Single companies: added from the Markets tab, chat, or Account; 5% each, capped 5/8/10/15% total by risk level.
- After the intake, tabs: Portfolio, Markets (Apple-Stocks-style watchlist + detail), News (Claude rundown +
  headlines + chat with suggested prompts), Chat, Report, Account (settings, security checklist, partial shares).
  Yahoo-Finance-style ticker strip with month sparklines scrolls across the top.
- Legacy, unused by the app: `core/readiness.py`, old `core/payday.py` flow (Evelyn's tests still cover them).

Demo personas: Maria, Jordan, Sam start at the intake (Sam's $2,050 paycheck); Alex has a year-old $52K fund.

## Git and workflow

- Repo: `github.com/evelynyouu/calhacks`, branch `main`. Lance's GitHub account: `lance06kaye-cloud`.
- Lance's pushes have gone straight to `main`; still ask before pushing.
- HTTPS credentials are in the macOS keychain (personal access token). `gh` and Homebrew are **not installed**,
  so open PRs via the link GitHub prints after a push. In Claude Code, keychain access needs the sandbox
  disabled for `git push` / `git pull`; set `GIT_TERMINAL_PROMPT=0` so a missing credential fails fast.
- Pull with `git pull --ff-only`; if teammates touched files with local edits, don't stash or overwrite
  without asking.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Open items (as of 2026-10-24)

- `lance/fund-builder` is uncommitted/unpushed; teammates haven't reviewed the pivot (it removes readiness and the
  bills-first payday from the app, and edits Ethan's `app/` and Saahil's engine files).
- Local `main` has an unpushed commit (`db94d83`, tiers in plain words) and is 30 behind origin; the branch already includes it.
- No `.env` yet: live Claude chat and narrative untested.
