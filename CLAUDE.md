# FirstFund

CalHacks project. Current pitch: **"Your own index fund, built around you. Every paycheck invests itself."**

A personalized index fund generator for new investors. A continuous chat asks a short, exact intake and builds
a fund: a broad core (US stocks, international stocks, bonds, mixed by risk score) plus a slice for the
industries the customer picks plus optional single companies. The customer chooses what percent of each
paycheck goes in; it's invested the moment the paycheck lands. Tabs then show the portfolio, markets (real
prices), news with a Claude rundown, chat, a report with recommendations (trades the customer confirms), and
account settings. **Advise, don't judge**: no readiness gate, no quizzing; the customer decides how much.

Team: Evelyn (bank / Nessie), Saahil (portfolio engine), Lance (rules, prompts, data, tests), Ethan (web app, Claude agent).
The user of this repo's Claude sessions is usually **Lance** (GitHub `lance06kaye-cloud`).

## Where things stand (2026-10-24, evening)

- The pivot lives on branch **`lance/fund-builder`**, pushed (latest `a90e2ad`; Lance's own commit `56bf387` before it).
  It tracks `origin/lance/fund-builder`. **Not merged into `main`; teammates haven't reviewed it.**
- It removes from the app (code still in repo): Lance's readiness check (`core/readiness.py`), Evelyn's bills-first
  payday flow (`core/payday.py`: safety check, breakdown bar, car-repair button), the crash test. It rewrote Ethan's
  `app/` and Saahil's `core/portfolio.py`, `core/broker.py`, `core/rebalance.py`, trimmed `core/sim.py`, and added
  fields to `core/models.py` (`TierPolicy.review_every_days`, `.auto_trades`; `HoldingRole` now core/tilt/stock).
- **Conflict to resolve before merging:** Evelyn rewrote `README.md` on `main` as a "Best Use of Nessie" prize
  write-up (with screenshots) describing the OLD product (rent-first payday, car repair, crash test). Merging
  `main` into the branch conflicts only in `README.md`; code merges cleanly (trial merge: all tests pass).
  The team must decide which story the README/prize submission tells. `main` also has Evelyn's `2ad4bf1`
  (Nessie ids stored in Nessie) not yet merged into the branch.
- Local `main` still has an old unpushed commit (`db94d83`, tiers in plain words), 30+ behind origin; the branch
  already contains it (cherry-picked). `stash@{0}` ("lance: readiness, tilt caps, prompt") is old; don't drop it
  without asking. `refs/snapshots/pre-fanout` is a safety snapshot from before the parallel-agent round.
- CLAUDE.md itself is untracked (never committed).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate   # .python-version says 3.13; this Mac's .venv is 3.14 and works
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload   # http://localhost:8000
pytest -q                       # 286 tests; must pass before pushing
```

Tests never touch the network: `tests/conftest.py` forces `NESSIE_MODE=fixture`, `MARKET_DATA=sim`,
`AUTH_DISABLED=1`, temp dirs for caches/DBs, and clears in-memory sessions, the sim broker and market shocks.
`tests/fund_helpers.make_fund(cid, **answers)` gives a persona a built fund without clicking through the chat.

Env vars (`.env.example`): `ANTHROPIC_API_KEY` (none on Lance's machine yet, so chat/rundown run offline),
`CLAUDE_MODEL` (default `claude-sonnet-5-5`), `NESSIE_MODE` / `NESSIE_API_KEY`, `MARKET_DATA` (`live` default,
`sim`), `BROKER` (`sim` default, `alpaca`), `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_PAPER`,
`LOGIN_ENABLED` (off by default), `SESSION_SECRET`, `HTTPS_ONLY`, `TRUST_PROXY`, `PUBLIC_BASE_URL`, `AUTH_DB`,
`GOOGLE_CLIENT_ID/SECRET`, `APPLE_CLIENT_ID/TEAM_ID/KEY_ID/PRIVATE_KEY(_PATH)`.

## Layout

| Path | What |
|---|---|
| `core/intake.py` (Lance) | The 8 questions, how each answer parses, risk score, "Other" interest keyword matching |
| `core/portfolio.py` (Saahil/Lance) | Core mix by risk + interest slice + single companies; overlap warnings from look-through |
| `core/fund.py` (Lance) | `FundAccount`: start, paycheck → invest %, review/queue/confirm trades, set interests/stocks/% |
| `core/report.py` (Lance) | Report numbers and rule-based recommendations |
| `core/market.py` | Prices: real closes when available, simulated path otherwise; quotes, series, shocks |
| `core/feed.py` | Nasdaq client: history, quotes, news RSS, symbol search; cached, short timeouts, fails soft |
| `core/broker.py` | `SimBroker` (fractional, default) and `AlpacaBroker`; legacy stubs kept for `core/payday.py` |
| `core/rebalance.py`, `core/sim.py`, `core/policy.py` | Allocation/drift/rebalance trades; 10-yr p10/p50/p90 (placeholder params); tiers |
| `core/auth.py`, `app/routes/auth.py`, `app/templates/auth/` | Sign-in (switched off; see below) |
| `core/nessie.py`, `core/profile.py`, `core/live.py`, `core/seed.py`, `core/state.py` (Evelyn) | Bank (fixture + live Nessie); `SIM_START = 2026-10-23` |
| `core/readiness.py`, `core/payday.py` | Legacy, unused by the app; Evelyn's tests still cover them |
| `agent/prompts.py` (Lance) | System prompt, `SUGGESTED_PROMPTS`, `RUNDOWN_INSTRUCTION` |
| `agent/claude_agent.py`, `agent/tools.py` | Intake script, Claude tool loop, offline replies, rundown; tools: get_fund, get_report, get_market_today, get_news, update_interests, add_stock, remove_stock, set_paycheck_percent |
| `app/routes/pages.py`, `app/state.py`, `app/templates/`, `app/static/` | Pages, HTMX partials, session state, `app.css` (theme), `tape.css` (ticker), `app.js` |
| `data/etfs.csv`, `data/companies.csv`, `data/holdings.csv`, `data/personas.json` | Funds (role core/tilt/benchmark, theme, label, about, beta, resid vol, anchor price), companies, look-through weights (approximate), personas |

## Product rules (current decisions)

- **Intake** (one continuous chat; input matches the question): goal (typed text) → starting amount (typed USD,
  mock) → % of each paycheck (slider + number box, live "$X of your $Y paycheck") → risk questionnaire about
  willingness, no right answers: what matters most (choice), $1,000 one-year range (choice), max drop (slider),
  swing comfort (1–10 scale) → industries (22 chips + "Other" typed, keyword-matched; unmatched noted).
  Rules: numbers → slider/scale; <5 options → bordered multiple-choice boxes (no A/B/C/D); open-ended → typed.
  Lance will revisit how many risk questions to keep.
- **Risk:** score = average of the 4 risk answers, 0–100; level 1–4 in bands of 25.
- **Fund:** core VTI/VXUS/BND (bonds 65/45/25/10% by level); interest slice 10/15/20/25%, split evenly, min 2% each;
  single companies 5% each, total cap 5/8/10/15%; slices come out of stocks so the bond share never changes.
- **Paycheck:** floor(paycheck × %) moves checking → investing via Nessie and buys the most underweight holdings.
- **Report recommendations:** rebalance (any holding ≥5 pts off; trades only at ≥$5K, else steer new money),
  invest more on a dip (S&P −7% in a month or −10% from 1-yr high), hold a lagging interest (≥10 pts behind over
  3 months), diversify (one company ≥8% via look-through), explore steadier sectors (≥60% tech-like picks),
  invest less (≥50% of paycheck), else stay the course. Each can carry trades or set-%/add/remove buttons.
- **Tiers:** review every 30/30/14/7/7 days; auto-prepared trades from $5K; nothing trades without Confirm.
- **Market data:** Nasdaq public API (no key): quotes, ~2 years of daily closes, RSS news, autocomplete search.
  Yahoo returns 429 from this IP and Stooq needs JavaScript, so Nasdaq is the source. Past the last real close
  (paydays fast-forward the demo clock) and offline, prices follow a seeded simulated path joined onto the last
  close; every screen labels live vs simulated. NTDOY (OTC) isn't on Nasdaq and stays simulated.
- **Partial shares:** supported (sim broker buys by the dollar); the Account page explains the logistics.
- **Personas:** Maria, Jordan, Sam start at the intake (Sam's paycheck $2,050); Alex has a year-old $52K fund,
  tier 4, auto trade prep on. `POST /p/<cid>/reset` starts a persona over.

## UI

- After the intake, tabs: **Portfolio** (value + each holding's day change, mix, paycheck autopilot, "From FirstFund"
  feed), **Markets** (Apple-Stocks-style list of indexes / your fund / watchlist with search; detail with 1W–2Y chart,
  52-wk range, "$25 buys", position, news, add to watchlist / fund), **News** (Claude rundown "The market / Your fund /
  Worth watching", headlines with links, chat + suggested prompts), **Chat**, **Report**, **Account** (profile, linked
  accounts, investing settings, notifications, security checklist, partial shares, data & privacy).
- **Ticker:** Yahoo-Finance-style white strip pinned to the top of every page (`base.html` block `tape`, sticky
  `.tape-dock`), fixed 52px height, self-scrolling loop that pauses on hover, green up / red down by the day's move,
  month sparkline with previous-close baseline; `GET /tape` (works without a persona) refreshes cards every 60s
  without restarting the scroll. Keep `id="tape"`: `app.js` skips focus/scroll handling for tape swaps.
- **Theme:** white / near-black `#0b0f0d` / green `#0b7a3e` (hover `#075c2e`, tint `#e8f6ee`), red `#d93025` for
  losses. No beige. Tailwind names kept: `paper` = light gray, `moss` = green, `clay` = red, `sun` = neutral.
- HTMX: posts return partials; other panels refresh via `hx-swap-oob`; app events post a line into the chat with
  `hx-swap-oob="beforeend:#chat-messages"`. Charts: `<canvas data-chart="pie|fan|history|price">`.

## Sign-in (built, switched off)

Email + password (argon2id) with mandatory TOTP 2FA (QR + manual key, 8 hashed recovery codes), Sign in with
Apple / Google (OIDC with state, nonce, PKCE for Google; ID tokens verified against provider JWKS; buttons disabled
until credentials exist), signed HttpOnly session cookie (2h idle / 12h max), CSRF on forms, throttling (in memory),
SQLite users DB `data/users.db` (gitignored). **Lance asked to remove the login page for now**: off by default;
`LOGIN_ENABLED=1` turns it on (then every page requires sign-in and the header shows email + Sign out); while off,
`/login` etc. return 404 and the Account checklist says sign-in is off. Redirect URIs: `{PUBLIC_BASE_URL}/auth/google/callback`,
`/auth/apple/callback` (Apple needs HTTPS). Render's free disk wipes `users.db` on redeploy.

## Ground rules

- **Claude never generates numbers.** Python computes every figure; Jinja renders it; Claude explains. Claude can't
  place trades (only the Confirm button does) and doesn't tell customers which stocks to buy.
- Be honest in the UI: label simulated data, unconfigured features (Apple/Google, email notifications), and
  approximations (look-through weights). Never fake a working feature.
- Session state (chat, fund, trades, watchlist, prefs) is in memory; resets on restart.

## Git and workflow

- Repo `github.com/evelynyouu/calhacks`. Ask before pushing. Lance's pushes used to go straight to `main`; the
  pivot is on `lance/fund-builder`.
- HTTPS credentials are in the macOS keychain; `gh` and Homebrew aren't installed (open PRs from the link GitHub
  prints). In Claude Code, `git push`/`pull`/`fetch` and network calls (pip, Nasdaq) need the sandbox disabled;
  set `GIT_TERMINAL_PROMPT=0`.
- Pull with `git pull --ff-only`; never stash/reset/overwrite teammates' or local work without asking. A session cron
  checks for new commits until 2026-10-25 09:43 PDT.
- `scripts/git-hooks/pre-commit` blocks secrets (`git config core.hooksPath scripts/git-hooks`, not set on this
  machine; run `sh scripts/git-hooks/pre-commit` before committing).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Headless screenshots: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new
  --window-size=W,H --virtual-time-budget=5000 --screenshot=...` (min width ~500px).

## Open items

- Team review of the pivot, then merge `main` into the branch (resolve `README.md`) and open a PR.
- README for the new product (pitch, tabs, env vars, live data, sign-in) once the team picks the story.
- No `.env` yet: live Claude chat, rundown and report narrative are untested. Then live-test prompts.
- Google/Apple credentials if sign-in is turned back on; password reset and 2FA re-enroll don't exist yet.
- Real trading needs real prices in reports too (Alpaca fills at real prices; one Alpaca account backs all personas).
- Placeholders: sim.py return/vol params and worst-year numbers; csv beta/vol are rough.
- Irregular income and 401(k) match aren't modeled.
