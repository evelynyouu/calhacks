# FirstFund

CalHacks project. Pitch: **"Your own index fund, built around you. Every paycheck invests itself."**

A personalized index fund for new investors. A new customer (or a demo persona) links a mock bank, then one chat asks
a short, exact intake and builds a **custom index**: bonds, international stocks, the customer's own index of big US
companies, plus slices for where they spend, the industries they pick and any companies they add. Every decision
comes with a plain-English reason. The customer picks what percent of each paycheck goes in; it's invested the moment
the paycheck lands. A left sidebar opens Portfolio, Money, Markets, News, Chat, Report, Backtest and Account.
**Advise, don't judge**: no readiness gate, no quizzing; the customer decides.

Team: Evelyn (bank / Nessie, fund engine), Saahil (backtest, broker, spending insights), Lance (rules, prompts, data,
tests, onboarding), Ethan (web app, Claude agent, report, slice UI). The user of this repo's Claude sessions is
usually **Lance** (GitHub `lance06kaye-cloud`).

## Where things stand (2026-10-25)

- **`main`** (`34ce387`) has everything: the pivot, Ethan's report and slice UI, Evelyn's fund builder and custom index
  (PRs #5–#7), Lance's onboarding and polish rounds (`lance/polish`, merged by Saahil in `88d73bf`), and Saahil's
  latest: spending panel on Portfolio, markdown chat, quote prefetch at startup, max-drop bond nudge, a "Simulate an
  event" card, Robinhood "agentic trading" demo, a Robinhood crypto broker, a backtest period picker, the ticker only
  on Portfolio and the landing page, and shorter helper copy. **787 tests pass in both Nessie modes** (run `pytest
  tests`: see Run and test).
- **`lance/skip-year`** (local, not pushed): a "Skip ahead a year" demo button (`fund.skip_ahead`, `POST
  /p/<cid>/skip_year`) so a glide step can be shown. Code and tests are written but uncommitted and not yet run.
- `lance/polish` and `lance/onboarding` are fully in `main`.
- Cleanup waiting on Lance: nine merged agent worktrees under `.claude/worktrees/`; `stash@{0}` ("lance: readiness,
  tilt caps, prompt", old; don't drop without asking); `refs/snapshots/pre-fanout` is a safety snapshot; the untracked
  `test_gemini.py` in the repo root (a Gemini API experiment; it breaks a bare `pytest`).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate   # .python-version says 3.13; this Mac's .venv is 3.14 and works
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload   # http://localhost:8000
pytest -q tests                 # 787 tests; must pass before pushing (plain `pytest` also collects test_gemini.py)
NESSIE_MODE=live pytest -q tests   # same suite, live paths against tests/fake_nessie.py (no network)
```

Tests never touch the network: `tests/conftest.py` forces `NESSIE_MODE=fixture`, `MARKET_DATA=sim`,
`AUTH_DISABLED=1`, temp dirs for caches/DBs, never calls the real Claude API, and clears in-memory sessions, new
customers, the sim broker and market shocks. `tests/fund_helpers.make_fund(cid, **answers)` gives a persona a linked
bank and a built fund without clicking through. Route tests must link a bank first (`tests/test_routes.link(cid)`)
and answer any follow-up (`{"keep": "1"}`). When piping pytest through `tail`, check the pass/fail line yourself:
the pipe hides pytest's exit code.

Env vars (`.env.example`): `ANTHROPIC_API_KEY` (none on Lance's machine yet, so chat, rundown, follow-up rewording
and report narrative run offline), `CLAUDE_MODEL` (default `claude-sonnet-5-5`), `NESSIE_MODE` / `NESSIE_API_KEY`,
`MARKET_DATA` (`live` default, `sim`), `BROKER` (`sim` default, `alpaca`, `robinhood`), `ALPACA_API_KEY` /
`ALPACA_SECRET_KEY` / `ALPACA_PAPER`, Robinhood crypto API key + Ed25519 private key (Saahil's `.env`; see
`core/broker_robinhood.py`), `LOGIN_ENABLED` (off by default), `SESSION_SECRET` (Render generates one; the warning without it is
harmless while sign-in is off), `HTTPS_ONLY`, `TRUST_PROXY`, `PUBLIC_BASE_URL`, `AUTH_DB`,
`GOOGLE_CLIENT_ID/SECRET`, `APPLE_CLIENT_ID/TEAM_ID/KEY_ID/PRIVATE_KEY(_PATH)`.

## Layout

| Path | What |
|---|---|
| `core/intake.py` (Lance) | The 8 questions, parsing, risk score, goal helpers (`goal_label`, `suggested_horizon`, `record`), "Other" interest matching |
| `core/followups.py` (Lance) | Intake follow-ups: 7 rules, at most 2 per intake, each with "Keep my answer" |
| `core/onboarding.py`, `app/routes/onboarding.py` (Lance) | New-customer form (`/start`) and the demo bank link (`/p/<cid>/link`) |
| `core/builder.py` (Evelyn) | Answers + spending → fund: risk score, horizon / max-drop ceilings, override, a `Reason` for every choice |
| `core/portfolio.py`, `core/universe.py` (Evelyn) | The custom index: slices, caps, look-through notes, worst years (docstring is the source of truth); the company universe and US core |
| `core/spending.py` (Evelyn, Saahil) | `spending_profile()` for the "where you spend" slice (merchant → company via `data/merchants.csv`, else `data/category_leaders.csv`); Saahil's insights for the chat tool and the Portfolio spending panel (`partials/spending_panel.html`, incl. a banking-based risk nudge) |
| `core/slices.py` (Ethan) | The fund as six slices for every page (fund card, pie, Portfolio list, report, How page) |
| `core/explain.py` (Evelyn) | "How your fund was built" (`/p/<cid>/how`) and the chat's per-holding explanations |
| `core/fund.py` (Lance) | `FundAccount`: build, start, paycheck → invest %, review / queue / confirm trades, Tier 4+ auto-execution |
| `core/targets.py`, `app/routes/targets.py` | Targets that follow the customer: glide path, pay/spending shifts, changed risk answers; Accept / Not now proposals |
| `core/figures.py`, `core/disclosure.py` | Every return figure on every page (`DEFINITIONS`); the disclosure line and footer state (broker, bank, prices) |
| `core/report.py`, `core/returns.py`, `core/finances.py` (Ethan) | Report: order replay, TWR/IRR, benchmark, attribution, recommendations, the money picture |
| `core/sim.py` | 10-yr p10/p50/p90 from the fund's holdings (J.P. Morgan 2026 LTCMA assumptions + measured correlations) |
| `core/backtest.py` (Saahil) | Auto-trade backtest (`/p/<cid>/backtest`) |
| `core/market.py`, `core/feed.py` | Prices (real closes when available, simulated path otherwise), quotes, shocks, `price_label()`; the Nasdaq client (history, quotes incl. COMP, news RSS, search; cached, fails soft) |
| `core/broker.py`, `core/broker_robinhood.py`, `core/rebalance.py`, `core/policy.py` | `SimBroker` / `AlpacaBroker` / `RobinhoodCryptoBroker` (crypto symbols live, everything else falls through to an embedded `SimBroker`); allocation / drift / rebalance trades; tiers |
| `core/nessie.py`, `core/profile.py`, `core/live.py`, `core/seed.py` (Evelyn) | Bank (fixture + live Nessie, id registry in Nessie); personas and new customers; `SIM_START = 2026-10-23` |
| `core/readiness.py`, `core/payday.py` | Legacy, unused by the app; Evelyn's tests still cover them |
| `agent/prompts.py` (Lance) | System prompt, `SUGGESTED_PROMPTS`, `RUNDOWN_INSTRUCTION` |
| `agent/claude_agent.py`, `agent/tools.py` | Intake script, follow-up wording, fund-ready message, Claude tool loop, offline replies, rundown; tools and the account snapshot |
| `app/routes/pages.py`, `app/routes/money.py`, `app/state.py` | Pages, ticker, Money tab, session state |
| `app/templates/`, `app/static/` | Templates; `app.css` (theme), `nav.css` (sidebar), `tape.css` (ticker), `app.js` |
| `data/` | Funds, companies (sector, market cap, country), merchants, category leaders, look-through weights (approximate), yields, personas, Nessie ids |
| `docs/` | `DEMO_SCRIPT.md` (Ethan; predates onboarding, sidebar and the custom index), `EVELYN_API.md`, `TEAM_INTEGRATION.md`, `data_sources.md`, plans |

## Product rules (current decisions)

- **Onboarding:** landing page → a persona or "Start as a new customer" (first name, take-home pay, pay frequency,
  checking; optional savings, monthly bills; makes a mock bank customer in fixture or live Nessie, id `new-<hex>`;
  their purchases go to the shared grocery merchant, so no company is picked from their spending) → demo bank link
  (Capital One / Nessie sandbox only; other banks greyed out; one-click demo sign-in, never a password field; pick
  checking + FirstFund Investing; will / won't consent list) → intake. Alex skips the link (fund already running).
  `POST /p/<cid>/reset` starts over and un-links.
- **Intake** (one chat; the input matches the question): goal (choice cards `home` / `retirement` / `travel` /
  `safety` / `wealth`, or typed "Something else" → `other` + `answers["goal_text"]`; display with
  `intake.goal_label()`) → starting amount (typed) → % of each paycheck (slider) → what matters most (cards) → $1,000
  one-year range (cards) → biggest drop you could live with (slider) → when you'll need the money (`horizon`: `lt3` /
  `3to7` / `7to15` / `15plus` / `unsure`) → industries (chips + "Other"). Numbers → slider/scale; 5 or fewer options →
  bordered cards (no A/B/C/D); open-ended → typed. With horizon unanswered or "Not sure yet", the goal suggests one
  (safety → lt3, home/travel → 3to7, retirement → 15plus, wealth/other → none); highlighted, never forced.
- **Follow-ups** (`core/followups.py`, 7 rules): unclear typed goal, starting amount over balances, $0 start + 0%,
  paycheck % over what's left after bills, clashing risk answers (spread ≥ 0.6; the other answer moves halfway toward
  the chosen one), unmatched "Other" interest close to a theme, a near-term goal (safety / travel or a dated typed goal)
  with horizon "Not sure yet" at level ≥ 3. Python writes the text; Claude may reword without changing numbers.
- **Risk:** score = average of the two preference answers (0–100). Limits can lower it: horizon (max level 1/2/3/4 for
  lt3/3to7/7to15/15plus) and max drop (riskiest mix whose real 2008/2022 worst year fits). Big disagreement → note and
  lean careful. The customer can override a limit (`answers["override"]`); it's advice, not a gate.
- **Fund = custom index** (six slices, `core/models.SLICES`; `core/portfolio.py` is the source of truth): bonds (BND;
  share follows the score, 65% → 10%, nudged ±5 points by the max-drop answer when it isn't a hard ceiling), international (VXUS, 30% of stocks), the customer's US index (~55 big US
  companies weighted by size, none over `CORE_NAME_CAP` of the slice), where they spend (by dollars, capped
  5/8/10/12% by level), their interests (each theme's `THEME_PICKS` biggest companies, or its fund if we hold none),
  companies they added (5% each, capped 5/8/10/15%). Spend, interest and added come out of the US index. One position
  per ticker, none past `COMPANY_CAP` (8%). ~58 positions. Spending picks are personalization, not predictions.
- **Paycheck:** floor(paycheck × %) moves checking → investing via Nessie and buys the ten most underweight holdings.
- **Targets follow the customer, never prices** (`core/targets.py`; every threshold a named constant): goal date =
  horizon bucket midpoint from the fund start (15plus assumed 20 years); inside the last 7 years bonds glide 30% → 65%,
  at most 5 points a step and one step a year. Pay change ≥ 5% or spending ±20% for 2 months → suggested paycheck %.
  Changed risk answers (Account tab) → rebuild with the builder. Each change is a proposal card on Portfolio with
  before/after and Accept / Not now; Accept records a `Reason` (How page + report) and never trades. Re-checked each
  paycheck, on "Got a raise" and via "Check my targets". Drift stays with the rebalance rules.
- **Report:** returns from the broker's order history (time-weighted and money-weighted); benchmark = the customer's
  plain mix (their US index at normal weights, VXUS, BND at the fund's stock/bond split), S&P 500 shown too; dollar
  attribution; holdings grouped by slice; estimated fund income. Recommendations show what triggered them.
- **Return figures:** one helper (`core/figures.py`); templates only format. "Total" = current value − money put in
  (`acct.contributed`), labeled "since you started"; every figure names its period (Today / Since you started / review
  dates). Portfolio leads with the total, today second. Definitions in `figures.DEFINITIONS` (Jinja `defs`).
- **Tiers:** review every 30/30/14/7/7 days; auto-prepared trades from $5K. **Tier 4+ with auto trade on executes
  review trades without Confirm** (`AUTO_EXECUTE_TIER`, Saahil). This contradicts the old "nothing trades without
  Confirm" rule; the team should confirm it's intended. Otherwise trades need Confirm.
- **Robinhood "agentic trading" (Saahil, demo):** Account → Connect Robinhood sets `Session.robinhood_connected` and
  `BROKER=robinhood`; then "Paycheck arrives" confirms review trades automatically and says "Claude … placed N trades
  via Robinhood". Only crypto symbols reach Robinhood (and only with real keys); the fund's stocks and ETFs still go to
  the sim broker. As worded, this clashes with two ground rules (Claude places no trades; never fake a working
  feature) and with "nothing trades without Confirm". See Open items.
- **Market data:** Nasdaq public API (no key): quotes, ~2 years of daily closes, RSS news, autocomplete search. Past the
  last real close (paydays fast-forward the demo clock) and offline, prices follow a seeded simulated path joined onto
  the last close. Every screen uses `market.price_label()`. NTDOY (OTC) stays simulated.
- **Partial shares:** supported (the sim broker buys by the dollar).
- **Personas:** Maria ($1,600), Jordan ($1,400) and Sam ($2,050) start at the bank link; Alex ($4,500 paychecks) has a
  year-old $52K fund, tier 4, auto trade on, goal retirement ("Retire early").

## UI

- **Sidebar** (`partials/nav.html`, rendered by `base.html` for any page with a customer; pages must not include it):
  dark column under the ticker; three states, open / collapsed to icons (tooltips on hover and focus, `aria-label` on
  every link) / hidden (a "Sidebar" button in the header brings it back), saved in localStorage `ff-nav` and restored
  before first paint; never overlaps content; a drawer behind "Menu" below 768px. During the intake only "Build my
  fund" (and Money once linked); refreshed as `#nav` out of band when the intake finishes. **Switch customer** is a
  separate pill at the right of the header, not in the sidebar.
- **Tabs:** Portfolio (fund card with slices, spending panel, the ten biggest positions + "Show all", target card),
  Money (accounts,
  next paycheck, money flow, bills before payday, bank activity, investing summary), Markets, News, Chat, Report,
  Backtest (period picker 1m–2y, `?days=14..730`), Account (incl. risk answers, linked accounts, Robinhood connect).
  Demo buttons live in a "Simulate an event" card (Paycheck arrives, Market drops 10%, Got a raise). "How your fund was built" (`/p/<cid>/how`, one card per slice) is linked
  from the fund-ready message and Portfolio.
- **Ticker:** dark Yahoo-style strip pinned above everything, **only on the Portfolio tab and the landing page**
  (`base.html` block `tape`, `.tape-dock`; other pages set `--tape-h:0px`), 52px, scrolling loop that pauses on hover,
  sparklines. On a customer's pages: the fund's `TAPE_HOLDINGS` (10) biggest positions,
  then anything the broker holds outside the targets. Before a fund, and on the landing page: S&P 500 (SPY), Nasdaq
  (real Nasdaq Composite, COMP, opening QQQ; QQQ itself offline) and Dow (DIA). Cards repeat to `TAPE_MIN_CARDS` (14)
  per copy so a short list fills the width (repeats hidden from screen readers, skipped by Tab). `GET /tape` refreshes
  every 60s without restarting the scroll; finishing the intake reloads the strip out of band. Keep `id="tape"`.
- **Disclosures:** one slim line under the header (`partials/disclosure.html`, `id="disclosure"`, re-sent out of band
  by `partials/panels.html`) with the real state (broker sim/Alpaca, bank demo/live Nessie, `price_label()`), and the
  full text in the footer (`id="disclosures"`). Pages don't add their own bank/broker/price pills; inline labels only
  where one number is simulated and its neighbours aren't.
- **Chat:** replies render markdown (`data-md`, a small parser in `app.js`). The system prompt is two blocks: the fixed prompt (cached) then a Python-built account snapshot
  (`agent/tools.py` `account_facts` / `account_snapshot`, ≤ 6,000 chars). Claude describes the fund by slice ("your US
  index"), never lists all sixty names and never endorses a company. Offline replies cover value, holdings, paychecks,
  bank activity, next review, goal/horizon and "why do I own X" (with its slice). Auto-trade changes only on an exact
  command ("turn on auto-trade"), never on a question. Suggested prompts from other tabs go through
  `POST /p/<cid>/chat/ask` (asked once, survives reloads).
- **Theme:** white / near-black `#0b0f0d` / green `#0b7a3e` (hover `#075c2e`, tint `#e8f6ee`), red `#d93025` for
  losses. No beige. Tailwind names: `paper` = light gray, `moss` = green, `clay` = red, `sun` = neutral. Expandable
  rows use `summary.chev`.
- HTMX: posts return partials; other panels refresh via `hx-swap-oob`; app events post a line into the chat with
  `hx-swap-oob="beforeend:#chat-messages"`. Charts: `<canvas data-chart="pie|fan|history|price">`.

## Sign-in (built, switched off)

Email + password (argon2id) with mandatory TOTP 2FA, Sign in with Apple / Google (OIDC; buttons disabled until
credentials exist), signed HttpOnly session cookie (2h idle / 12h max), CSRF, throttling, SQLite `data/users.db`
(gitignored). Off by default at Lance's request; `LOGIN_ENABLED=1` turns it on (every page then requires sign-in).
While off, `/login` etc. return 404. Redirect URIs: `{PUBLIC_BASE_URL}/auth/google/callback`, `/auth/apple/callback`.

## Ground rules

- **Claude never generates numbers.** Python computes every figure; Jinja renders it; Claude explains. Claude can't
  place trades, doesn't tell customers which stocks to buy, and never says a company is a good buy.
- Be honest in the UI: label simulated data, the mock bank, unconfigured features, and approximations (look-through
  weights, market caps, assumptions). Never fake a working feature (no fake banks, no password fields in the demo link).
- Session state (chat, fund, trades, watchlist, prefs, link) is in memory; resets on restart.

## Git and workflow

- Repo `github.com/evelynyouu/calhacks`. **Ask before pushing.** Teammates merge PRs into `main`.
- HTTPS credentials are in the macOS keychain; `gh` and Homebrew aren't installed (open PRs from the link GitHub
  prints). In Claude Code, `git push`/`pull`/`fetch` and network calls (pip, Nasdaq) need the sandbox disabled;
  set `GIT_TERMINAL_PROMPT=0`.
- Pull with `git pull --ff-only`. A local-only branch (like `lance/polish`) has nothing to pull: fetch and merge
  `origin/main` instead. Never stash/reset/overwrite teammates' or local work without asking.
- Parallel agents: give each its own worktree and file boundaries (new modules and route files registered in
  `app/main.py`, additive fields on `Session` / `FundAccount`), then merge on one branch and run both test modes.
- `scripts/git-hooks/pre-commit` blocks secrets (`core.hooksPath` isn't set on this machine; run
  `sh scripts/git-hooks/pre-commit` before committing).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Note:
  `docs/TEAM_INTEGRATION.md` asks the team to drop Claude co-author lines (`main` was rewritten twice to remove them),
  yet teammates' own recent commits carry them; Lance should settle it.
- Headless screenshots: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new
  --window-size=W,H --virtual-time-budget=6000 --screenshot=...` against a running server (min width ~500px). If a
  full page comes out blank, screenshot a standalone HTML file of the partial or check the HTML with `TestClient`.

## Open items

- Robinhood demo honesty: the message says Claude placed trades via Robinhood while equities fill in the sim broker.
  Either label it ("simulated; Robinhood would place these") or route only what Robinhood really trades. Also decide
  whether auto-execution (Robinhood mode and Tier 4+) is intended, since it skips Confirm.
- Finish `lance/skip-year` (run the tests, commit, PR). Decide the co-author question first (below).
- Ethan: one-page fund PDF (`/p/<cid>/fund.pdf`, pure-Python library).
- Update `docs/DEMO_SCRIPT.md` for the bank link, sidebar, Money tab, custom index, target cards, the Simulate card and
  the Robinhood demo.
- New customers have no "where you spend" slice (their purchases go to a generic grocery merchant): a "Where do you
  shop?" chip question would fill it honestly. Small funds hold ~58 tiny positions: consider one US-index fund (VTI)
  until the balance passes a threshold. Sessions live in memory, so a Render restart mid-judging wipes a judge's fund.
- Polish leftovers: change risk answers from chat (a tool calling `targets.propose_answers`); quick-pick boxes for the starting amount; whether "Growing my wealth" should
  suggest a horizon; Claude refusal fallbacks once a key exists.
- No `.env` yet: live Claude chat, rundown, follow-up rewording and report narrative are untested. Prompts to try with
  a key: "How much is my fund worth, and how much of that did I put in?" · "Why do I own Costco?" · "What happened
  with my last paycheck, and where did the money go?" · "What's in my US index?" · "When's my next review, and would
  anything trade without me pressing Confirm?"
- Google/Apple credentials if sign-in is turned back on; password reset and 2FA re-enroll don't exist yet.
- Real trading needs real prices in reports (Alpaca fills at real prices; one Alpaca account backs all personas, so
  broker positions can mix personas).
- Irregular income and 401(k) match aren't modeled. Link state doesn't survive a restart.
