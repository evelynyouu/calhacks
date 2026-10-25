# FirstFund

CalHacks project. Current pitch: **"Your own index fund, built around you. Every paycheck invests itself."**

A personalized index fund generator for new investors. A new customer (or a demo persona) links a mock bank, then a
continuous chat asks a short, exact intake and builds a fund: a broad core (US stocks, international stocks, bonds,
mixed by risk) plus a slice for the industries the customer picks plus optional single companies. Every decision
comes with a plain-English reason. The customer chooses what percent of each paycheck goes in; it's invested the
moment the paycheck lands. A left sidebar then opens Portfolio, Money (bank + investing), Markets (real prices),
News (Claude rundown), Chat, Report, Backtest and Account. **Advise, don't judge**: no readiness gate, no quizzing;
the customer decides how much.

Team: Evelyn (bank / Nessie, fund builder), Saahil (portfolio engine, backtest), Lance (rules, prompts, data, tests,
onboarding), Ethan (web app, Claude agent, report). The user of this repo's Claude sessions is usually **Lance**
(GitHub `lance06kaye-cloud`).

## Where things stand (2026-10-24, night)

- **`main`** (`6e3168c`) = the pivot + Ethan's report-accuracy PR #4, his demo script and Render `SESSION_SECRET`, and
  Saahil's backtest / spending insights / Tier 4+ auto-execution, plus `257658d` (Saahil, 2026-10-24 19:49): a
  spending-driven fund (Nessie merchants → single stocks, e.g. Costco → COST) **made the default**, and the same backtest
  `{% endif %}` fix.
- **`origin/lance/fund-builder`** (`d9e4f6d`) = the pivot + Evelyn's fund builder (PR #5: horizon question, max drop as a
  ceiling, smooth bond share, interest sizing, override, "How your fund was built" page). **`main` doesn't have PR #5,
  and `lance/fund-builder` doesn't have Saahil's or Ethan's latest: the two have diverged.**
- **`lance/polish`** (local, not pushed) = `lance/onboarding` + `257658d` merged (`445b8b6`), then the polish round
  (six parallel agents, one branch each, merged here; 677 tests pass in both Nessie modes). Screenshots of the merged
  app: `~/calhacks-shots/final/` (1280 and 500px). Merge decision: Evelyn's answer-driven builder stays the default;
  Saahil's `build_spending_portfolio` / `build_custom_portfolio` are opt-in via `answers["spending_mode"]` /
  `answers["custom_mode"]` (`etf_mode` dropped). The team should confirm.
- **`lance/onboarding`** (local only, **not pushed**) = both of the above merged, plus the onboarding round: new
  customers + demo bank link, left sidebar + Money tab, intake follow-up questions, the dark ticker with the real
  Nasdaq Composite, one shared price label. 533 tests pass in both Nessie modes. Pushing it (or opening a PR into
  `main`) would also bring PR #5 to `main`. Ask Lance first.
- Leftover agent worktrees under `.claude/worktrees/` (nine, all merged: three onboarding, six polish) can be removed.
- Local `main` is stale (old unpushed `db94d83`, already contained upstream). `stash@{0}` ("lance: readiness, tilt
  caps, prompt") is old; don't drop it without asking. `refs/snapshots/pre-fanout` is a safety snapshot.

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate   # .python-version says 3.13; this Mac's .venv is 3.14 and works
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload   # http://localhost:8000
pytest -q                       # 533 tests; must pass before pushing
NESSIE_MODE=live pytest -q      # same suite, live paths against tests/fake_nessie.py (no network)
```

Tests never touch the network: `tests/conftest.py` forces `NESSIE_MODE=fixture`, `MARKET_DATA=sim`,
`AUTH_DISABLED=1`, temp dirs for caches/DBs, never calls the real Claude API, and clears in-memory sessions, new
customers, the sim broker and market shocks. `tests/fund_helpers.make_fund(cid, **answers)` gives a persona a linked
bank and a built fund without clicking through. Route tests must link a bank first (`tests/test_routes.link(cid)`)
and answer any follow-up (`{"keep": "1"}`). When piping pytest through `tail`, check for failures yourself: the pipe
hides pytest's exit code.

Env vars (`.env.example`): `ANTHROPIC_API_KEY` (none on Lance's machine yet, so chat/rundown/follow-up rewording run
offline), `CLAUDE_MODEL` (default `claude-sonnet-5-5`), `NESSIE_MODE` / `NESSIE_API_KEY`, `MARKET_DATA` (`live`
default, `sim`), `BROKER` (`sim` default, `alpaca`), `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_PAPER`,
`LOGIN_ENABLED` (off by default), `SESSION_SECRET` (Render generates one), `HTTPS_ONLY`, `TRUST_PROXY`,
`PUBLIC_BASE_URL`, `AUTH_DB`, `GOOGLE_CLIENT_ID/SECRET`, `APPLE_CLIENT_ID/TEAM_ID/KEY_ID/PRIVATE_KEY(_PATH)`.

## Layout

| Path | What |
|---|---|
| `core/intake.py` (Lance) | The 8 questions, parsing, risk score, "Other" interest keyword matching |
| `core/followups.py` (Lance) | Intake follow-ups: 8 rules, at most 2 per intake, each with "Keep my answer" |
| `core/onboarding.py`, `app/routes/onboarding.py` (Lance) | New-customer form (`/start`) and the demo bank link (`/p/<cid>/link`) |
| `core/builder.py`, `core/explain.py` (Evelyn) | Answers → fund with a `Reason` for every choice; "How your fund was built" content (`/p/<cid>/how`) |
| `core/portfolio.py` (Saahil/Evelyn) | Score → weights: smooth bond share, interest sizing by swing, single companies, look-through notes, worst years |
| `core/fund.py` (Lance) | `FundAccount`: start, paycheck → invest % (`invest_amount`, `next_paycheck`), review/queue/confirm trades, Tier 4+ auto-execution |
| `core/report.py`, `core/returns.py`, `core/finances.py` (Ethan) | Report: order replay, TWR/IRR, blended benchmark, attribution, recommendations, the customer's money picture |
| `core/figures.py`, `core/disclosure.py` | Every return figure on every page (value, total since you started, today, since you bought; `DEFINITIONS`); the disclosure line + footer state (broker, bank, prices) |
| `core/targets.py`, `app/routes/targets.py` | Targets that follow the customer: goal date + glide path, pay/spending shifts → paycheck %, changed risk answers; proposals with Accept / Not now |
| `core/sim.py` | 10-yr p10/p50/p90 from the fund's holdings (J.P. Morgan 2026 LTCMA assumptions + measured correlations) |
| `core/backtest.py`, `core/spending.py` (Saahil) | Auto-trade backtest (`/p/<cid>/backtest`); spending-based theme/risk insights (not wired into a page yet) |
| `core/market.py` | Prices: real closes when available, simulated path otherwise; quotes, series, shocks; `price_label()` |
| `core/feed.py` | Nasdaq client: history, quotes (incl. the COMP index), news RSS, symbol search; cached, fails soft |
| `core/broker.py`, `core/rebalance.py`, `core/policy.py` | `SimBroker` / `AlpacaBroker`; allocation/drift/rebalance trades; tiers |
| `core/nessie.py`, `core/profile.py`, `core/live.py`, `core/seed.py` (Evelyn) | Bank (fixture + live Nessie, id registry in Nessie); personas and new customers; `SIM_START = 2026-10-23` |
| `core/readiness.py`, `core/payday.py` | Legacy, unused by the app; Evelyn's tests still cover them |
| `agent/prompts.py` (Lance) | System prompt, `SUGGESTED_PROMPTS`, `RUNDOWN_INSTRUCTION` |
| `agent/claude_agent.py`, `agent/tools.py` | Intake script, follow-up wording, fund-ready message, Claude tool loop, offline replies, rundown; tools incl. `get_fund_explanation` |
| `app/routes/pages.py`, `app/routes/money.py`, `app/state.py` | Pages, Money tab, session state (`linked`, `bank_linked`, `followup`) |
| `app/templates/`, `app/static/` | Templates; `app.css` (theme), `nav.css` (sidebar), `tape.css` (ticker), `app.js` |
| `data/*.csv`, `data/personas.json`, `data/nessie_ids.json` | Funds, companies, look-through weights (approximate), yields, personas, Nessie ids |
| `docs/DEMO_SCRIPT.md` (Ethan) | 4-minute demo (written against `lance/fund-builder`; predates onboarding + sidebar) |

## Product rules (current decisions)

- **Onboarding:** landing page → a persona or "Start as a new customer" (first name, take-home pay, pay frequency,
  checking; optional savings, monthly bills; makes a mock bank customer in fixture or live Nessie, id `new-<hex>`)
  → demo bank link (Capital One / Nessie sandbox only; other banks greyed out; one-click demo sign-in, never a
  password field; pick checking + FirstFund Investing; will / won't consent list) → intake. Alex skips the link
  (fund already running). `POST /p/<cid>/reset` starts over and un-links.
- **Intake** (one chat; input matches the question): goal (choice cards: `home`/`retirement`/`travel`/`safety`/
  `wealth`, or a typed "Something else" → `other` + `answers["goal_text"]`; show it with `intake.goal_label()`) → starting amount → % of each paycheck (slider) →
  what matters most (choice) → $1,000 one-year range (choice) → biggest drop you could live with (slider) → when you'll
  need the money (`horizon`: `lt3`/`3to7`/`7to15`/`15plus`/`unsure`) → industries (chips + "Other").
  Numbers → slider/scale; 5 or fewer options → bordered boxes (no A/B/C/D); open-ended → typed. With horizon
  unanswered, the goal suggests one (`intake.suggested_horizon`: safety → lt3, home/travel → 3to7, retirement →
  15plus, wealth/other → none); it's highlighted, never forced.
- **Follow-ups** (`core/followups.py`): unclear goal, starting amount over balances, $0 start + 0%, paycheck % over
  what's left after bills, clashing risk answers (spread ≥ 0.6; the other answer moves halfway toward the chosen one),
  unmatched "Other" interest close to a theme; a near-term goal (safety/travel or a dated typed goal) with horizon
  "Not sure yet" and level ≥ 3. 7 rules (`goal_no_timeline` was removed: the cards + horizon question cover it).
  Python writes the text; Claude may reword without changing numbers.
- **Risk:** score = average of the two preference answers (0–100). Limits can lower it: horizon (max level 1/2/3/4 for
  lt3/3to7/7to15/15plus) and max drop (riskiest mix whose real 2008/2022 worst year fits). Big disagreement → note and
  lean careful. The customer can override a limit (`answers["override"]`); it's advice, not a gate.
- **Fund:** bond share follows the score smoothly (65% → 10%), stocks ~70/30 US/international. Interests: up to 8%
  each, less for funds that swing more (never under 2%), total capped 10/15/20/25% by level. Single companies 5% each,
  total cap 5/8/10/15%. Look-through notes for one company ≥ 8% or a sector far above the market.
- **Paycheck:** floor(paycheck × %) moves checking → investing via Nessie and buys the most underweight holdings.
- **Targets follow the customer, never prices** (`core/targets.py`, every threshold a named constant): goal date =
  horizon bucket midpoint from the fund start (15plus assumed 20 yrs); inside the last 7 years bonds glide 30% → 65%,
  at most 5 points per step and one step a year. Pay change ≥ 5% or spending ±20% for 2 pay-period months → suggested
  paycheck %. Changed risk answers (Account tab) → rebuild with `core/builder.build`. Each change is a proposal card
  (Portfolio tab) with before/after and Accept / Not now; Accept records a `Reason` (How page + report) and never
  trades. Re-checked each paycheck, on "Got a raise" and via "Check my targets". Drift stays with the rebalance rules.
- **Report:** returns from the broker's order history (time-weighted and money-weighted), benchmark = the fund's own
  core at its stock/bond split (S&P shown too), dollar attribution per holding, simulated-price labels, estimated fund
  income. Recommendations show what triggered them; trades need Confirm.
- **Return figures:** one helper (`core/figures.py`); templates only format. "Total" = current value − money put in
  (`acct.contributed`), labeled "since you started"; every figure names its period (Today / Since you started / the
  review dates). Portfolio leads with the total, today second. Definitions in `figures.DEFINITIONS` (Jinja `defs`).
- **Tiers:** review every 30/30/14/7/7 days; auto-prepared trades from $5K. Saahil's change: **Tier 4+ with auto trade
  on executes review trades without Confirm** (`AUTO_EXECUTE_TIER` in `core/fund.py`). This contradicts the old
  "nothing trades without Confirm" rule; the team should confirm it's intended.
- **Market data:** Nasdaq public API (no key): quotes, ~2 years of daily closes, RSS news, autocomplete search. Past the
  last real close (paydays fast-forward the demo clock) and offline, prices follow a seeded simulated path joined onto
  the last close. Every screen uses `market.price_label()` ("Nasdaq prices · delayed", "Simulated prices", or
  "Simulated prices · demo date …"). NTDOY (OTC) isn't on Nasdaq and stays simulated.
- **Partial shares:** supported (sim broker buys by the dollar).
- **Personas:** Maria, Jordan, Sam start at the bank link (Sam's paycheck $2,050); Alex has a year-old $52K fund,
  tier 4, auto trade on.

## UI

- **Sidebar** (`partials/nav.html`, rendered by `base.html` for any page with a customer): dark column under the
  ticker; three states, open / collapsed to icons (tooltips on hover and focus, `aria-label` on every link) /
  hidden (a "Sidebar" button in the header brings it back), saved in localStorage key `ff-nav` and restored before
  first paint; never overlaps content; a drawer behind a "Menu" button below 768px. **Switch customer** is a separate
  pill at the right of the header (`base.html`), not in the sidebar. During the
  intake only "Build my fund" (and Money once linked). Refreshed as `#nav` out of band when the intake finishes.
  Pages must not include `partials/nav.html` themselves.
- **Tabs:** Portfolio, Money (accounts, next paycheck and what it invests, money flow by month, bills before payday,
  bank activity, investing summary), Markets, News, Chat, Report, Backtest, Account. "How your fund was built"
  (`/p/<cid>/how`) is linked from the fund-ready message and the Portfolio tab.
- **Ticker:** dark Yahoo-style strip pinned above everything (`base.html` block `tape`, `.tape-dock`), 52px, scrolling
  loop that pauses on hover, sparklines. On a customer's pages it shows only what they hold: the fund's holdings in
  weight order (single companies included), then any other broker positions by value. No default stocks, watchlist or
  BND/GLD. Before a fund is built, and on the landing page, it shows the major indexes: S&P 500 (SPY), Nasdaq (the real
  Nasdaq Composite, COMP, which opens QQQ; QQQ itself offline) and Dow (DIA). Each scrolling copy repeats its cards to
  at least `TAPE_MIN_CARDS` (14) so a short list fills the width (repeats hidden from screen readers, skipped by Tab;
  speed constant). `GET /tape` refreshes every 60s without restarting the scroll; finishing the intake reloads the
  whole strip out of band. Keep `id="tape"`.
- **Disclosures:** one slim line under the header (`partials/disclosure.html`, `id="disclosure"`, re-sent out of band by
  `partials/panels.html`) with the real state (broker sim/Alpaca, bank demo/live Nessie, `price_label()`), and the full
  text in the footer (`id="disclosures"`). Pages don't add their own bank/broker/price pills; inline labels only where
  one number is simulated and its neighbours aren't.
- **Chat:** the system prompt is two blocks: the fixed prompt (cached) then a Python-built account snapshot
  (`agent/tools.py` `account_facts` / `account_snapshot`, ≤ 6,000 chars). Offline replies cover value, holdings,
  paychecks, bank activity, next review, goal/horizon and "why do I own X". Fund-mode and auto-trade switches only on
  an exact command ("switch to custom fund", "turn on auto-trade"), never on a question. Suggested prompts from other
  tabs go through `POST /p/<cid>/chat/ask` (asked once, survives reloads).
- **Theme:** white / near-black `#0b0f0d` / green `#0b7a3e` (hover `#075c2e`, tint `#e8f6ee`), red `#d93025` for
  losses. No beige. Tailwind names: `paper` = light gray, `moss` = green, `clay` = red, `sun` = neutral.
- HTMX: posts return partials; other panels refresh via `hx-swap-oob`; app events post a line into the chat with
  `hx-swap-oob="beforeend:#chat-messages"`. Charts: `<canvas data-chart="pie|fan|history|price">`.

## Sign-in (built, switched off)

Email + password (argon2id) with mandatory TOTP 2FA, Sign in with Apple / Google (OIDC; buttons disabled until
credentials exist), signed HttpOnly session cookie (2h idle / 12h max), CSRF, throttling, SQLite `data/users.db`
(gitignored). Off by default at Lance's request; `LOGIN_ENABLED=1` turns it on (every page then requires sign-in).
While off, `/login` etc. return 404. Redirect URIs: `{PUBLIC_BASE_URL}/auth/google/callback`, `/auth/apple/callback`.

## Ground rules

- **Claude never generates numbers.** Python computes every figure; Jinja renders it; Claude explains. Claude can't
  place trades and doesn't tell customers which stocks to buy.
- Be honest in the UI: label simulated data, the mock bank, unconfigured features, and approximations (look-through
  weights, assumptions). Never fake a working feature (no fake banks, no password fields in the demo link).
- Session state (chat, fund, trades, watchlist, prefs, link) is in memory; resets on restart.

## Git and workflow

- Repo `github.com/evelynyouu/calhacks`. **Ask before pushing.** Teammates merge PRs into `main` (and Evelyn's into
  `lance/fund-builder`).
- HTTPS credentials are in the macOS keychain; `gh` and Homebrew aren't installed (open PRs from the link GitHub
  prints). In Claude Code, `git push`/`pull`/`fetch` and network calls (pip, Nasdaq) need the sandbox disabled;
  set `GIT_TERMINAL_PROMPT=0`.
- Pull with `git pull --ff-only` (a local-only branch has nothing to pull: fetch and merge `origin/main` instead); never
  stash/reset/overwrite teammates' or local work without asking. A session cron checks for new commits until
  2026-10-25 09:43 PDT.
- Parallel agents: give each its own worktree and file boundaries (new route modules registered in `app/main.py`,
  additive fields on `Session`), then merge on one branch and run both test modes.
- `scripts/git-hooks/pre-commit` blocks secrets (`git config core.hooksPath scripts/git-hooks` isn't set on this
  machine; run `sh scripts/git-hooks/pre-commit` before committing).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Headless screenshots: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new
  --window-size=W,H --virtual-time-budget=5000 --screenshot=...` (min width ~500px). Full app pages sometimes come out
  blank; screenshot a standalone HTML file of the partial instead, or check the rendered HTML with `TestClient`.

## Open items

- Decide how `lance/onboarding` reaches `main` (PR), so `main` gets Evelyn's PR #5 and the onboarding round.
- Confirm Tier 4+ auto-execution is intended (it skips Confirm).
- Decide the default fund: answer-driven (CLAUDE.md rules, current `lance/polish`) or Saahil's spending-driven single
  stocks (his `257658d` on `main`). Spending mode picks individual companies by default, which sits awkwardly with
  "doesn't tell customers which stocks to buy" and the 5%-per-company / level caps.
- Ethan: one-page fund PDF (`/p/<cid>/fund.pdf`, pure-Python library) per the prompt Lance sent.
- Polish round leftovers: changing risk answers from chat (a tool calling `targets.propose_answers`); a "skip ahead a
  year" demo control so a glide step can be shown; quick-pick boxes for the starting amount; whether "Growing my
  wealth" should suggest a horizon; Claude refusal fallbacks once a key exists. Live prompts to try with a key:
  "How much is my fund worth, and how much of that did I put in?" · "Why do I own XLV, and why is it smaller than
  VTI?" · "What happened with my last paycheck, and where did the money go?" · "Is my fund custom? What would a custom
  fund look like?" (must not switch) · "When's my next review, and would anything trade without me pressing Confirm?"
- Update `docs/DEMO_SCRIPT.md` for the bank link, sidebar and Money tab.
- No `.env` yet: live Claude chat, rundown, follow-up rewording and report narrative are untested.
- Google/Apple credentials if sign-in is turned back on; password reset and 2FA re-enroll don't exist yet.
- Real trading needs real prices in reports too (Alpaca fills at real prices; one Alpaca account backs all personas).
- Irregular income and 401(k) match aren't modeled. Link state doesn't survive a restart.
