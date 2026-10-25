# Custom Index Fund (engine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three-fund core with the customer's own index: about 50 large US companies weighted by size, plus picks from where they spend money (Nessie purchases) and what interests them, with a bond share and an international fund sized by risk.

**Architecture:** `core/portfolio.py` builds a fund from a level, a bond share, interests, added companies and a spending profile. `core/spending.py` turns Nessie purchases into companies. `core/builder.py` keeps choosing the score and limits (PR #5) and passes the spending profile through. Every decision is a `Reason`. Ethan's half (UI, report, projection, paycheck buying) builds against the **Contract** section below; the two halves are developed in parallel.

**Tech Stack:** Python 3.9, FastAPI/Jinja (unchanged), pytest. Data in `data/*.csv`.

**Spec:** No separate spec file: this plan is the spec, from Evelyn's decisions on 2026-10-24.
- The core is about 50 individual stocks plus bonds.
- The plan covers the whole feature, split between Evelyn (this plan) and Ethan (`2026-10-24-custom-index-ethan-prompt.md`).
- Spending counts the last ~90 days, weighted by dollars, excluding bills and one-off events.
- Focus on the fund building.

**Base:** branch `evelyn/custom-index`, cut from `evelyn/fund-explainer` (PR #5). Merge `lance/fund-builder` in after PR #5 lands. The PR goes into `lance/fund-builder`.

## Global Constraints

- Ground rules (unchanged): advise, don't judge.
  - Python computes every number; Claude only explains.
  - Claude never tells a customer which stocks to buy. The engine picks by stated rules, and each pick's reason says which rule.
  - Label anything approximate or simulated.
- "Buy where you shop" is personalization, not a prediction. The spend slice's reasons and the page must say so.
- Only US-listed tickers. The price feed (Nasdaq) and the broker (Alpaca) only cover US listings, so foreign companies enter through their US listings (TSM, ASML, NVO, TM, BABA, NTDOY).
- Keep `build_portfolio(risk_level, interests=None, stocks=None, *, ...)` callable positionally. Payday and readiness tests call it.
- No company ends up at 8% or more of a fresh fund (`COMPANY_CAP = 0.08`). This matches `report.CONCENTRATION_TRIGGER`, so a new fund never triggers "diversify".
- Market caps are approximate and dated. Each data file states its source and date in a header comment, or in a `docs/data_sources.md` row if the csv can't hold comments.
- Tell Lance before changing `data/personas.json`. Tell Saahil before changing `core/portfolio.py` (his engine).

## Contract with Ethan (both halves build against this)

1. **`Holding` gains two defaulted fields** in `core/models.py`: `sector: str = ""` and `slice: str = ""`.
   - `sector` is one of the `SECTORS` names in `core/universe.py`, or `""` for funds.
   - `slice` is one of `SLICES`.
   - `role` keeps its old meaning per slice:

     | slice | role |
     |---|---|
     | `us_core` | `"core"` |
     | `intl` | `"core"` (VXUS) |
     | `bonds` | `"core"` (BND) |
     | `spend` | `"spend"` (new value, added to `HoldingRole`) |
     | `interest` | `"tilt"` (a company or a theme fund) |
     | `added` | `"stock"` |
2. **`SLICES`** is an ordered tuple in `core/models.py`, each a (key, display label) pair: `("bonds", "Bonds")`, `("intl", "International")`, `("us_core", "Your US index")`, `("spend", "Where you spend")`, `("interest", "Your interests")`, `("added", "Companies you added")`.
3. **`Portfolio.slice_weights() -> dict[str, float]`** gives each slice's total weight, and **`Portfolio.by_slice() -> dict[str, list[Holding]]`** gives its holdings. Both are new methods, never empty keys.
4. **Funds that stay funds:** `BND` (bonds) and `VXUS` (international). Everything else in `us_core`, `spend` and `added`, and most of `interest`, is a single company. Themes with no companies in the universe (gold, emerging, small caps, dividends) keep their theme fund.
5. **`explain.build(acct)`** (Evelyn) adds `"slices": [{"key", "label", "weight", "holdings": [{"ticker", "name", "weight", "sector", "texts"}]}]` in `SLICES` order. Ethan's templates render it. The existing keys stay.
6. **A Spend record** (`core/spending.py`) has these fields: `merchant: str`, `category: str`, `dollars: float`, `ticker: str | None`, `via: "brand" | "category" | "none"`.

## Review Focus

1. **No purchase history** (a new customer, or a persona with no `[spend]` purchases in 90 days): there's no spend slice, the core gets that share, and a reason says so.
2. **Every merchant is private or local** (Berkeley Bowl, Sliver Pizzeria): the category fallback picks the category's leaders, and the reason names the merchant and the leaders.
3. **A merchant's company is also a big core holding** (Costco, Amazon, Apple): it's counted once across slices and stays under `COMPANY_CAP`, and the excess goes back to the rest of the core.
4. **A tiny balance** ($0, or $50 spread over ~60 names): weights still sum to 1. The page uses the $1,000 example. Nothing divides by zero.
5. **Live Nessie is offline:** `spending_profile` falls back to demo data like the rest of the app, and the fund still builds.

---

## File structure

| File | Responsibility |
|---|---|
| `data/companies.csv` (modify, Lance's data) | add `sector`, `market_cap_b`, `country`, `core` (1 = in the US core) columns. About 55 US core names plus brand tickers. |
| `data/merchants.csv` (new) | `pattern,ticker,category`: a brand regex mapped to a ticker, or `ticker` left empty for "private". |
| `data/category_leaders.csv` (new) | `category,tickers`: the fallback picks per Nessie merchant category (for example Groceries -> WMT;COST;KR). |
| `core/universe.py` (new) | loads the company universe; `SECTORS`, `core_companies()`, `theme_companies(theme, n)`, `market_cap(ticker)`. |
| `core/spending.py` (new) | `spending_profile(cid, days=90) -> list[Spend]` from Nessie (live) or the seed plan (fixture). |
| `core/portfolio.py` (modify) | builds the US core, spend slice, interest companies, per-company cap; reasons. |
| `core/builder.py` (modify) | passes `spending` through; ceilings unchanged. |
| `core/fund.py` (modify) | `build()` passes `spending_profile(cid)`. |
| `core/explain.py` (modify) | `"slices"` key; full-version text for "your own index" and "where you spend". |
| `core/seed.py`, `data/personas.json` | per-persona merchants with real brands; re-seed live. |
| tests | `test_universe.py`, `test_spending.py`, `test_custom_index.py`, updates to the property and golden tests. |

---

### Task 1: The company universe

**Files:**
- Modify `data/companies.csv`.
- Create `core/universe.py`.
- Test `tests/test_universe.py`.

**Interfaces:**
- Produces:
  - `SECTORS: tuple[str, ...]`, the 11 GICS sectors in plain words: Technology, Healthcare, Financials, Consumer, Everyday essentials, Communication, Industrials, Energy, Utilities, Real estate, Materials.
  - `core_companies() -> list[str]`, sorted by market cap, largest first.
  - `market_cap(t) -> float` (billions), `sector(t) -> str`, `country(t) -> str`.
  - `theme_companies(theme: str, n: int) -> list[str]`, the theme's largest `n` companies.
- Theme membership comes from the existing `etf` column through `ETFS[etf]["theme"]`.

- [ ] **Step 1: Write the failing test** `tests/test_universe.py`:

```python
def test_core_is_about_50_us_companies_covering_every_sector():
    core = universe.core_companies()
    assert 45 <= len(core) <= 60
    assert all(universe.country(t) == "US" for t in core)
    assert {universe.sector(t) for t in core} == set(universe.SECTORS)
    assert core == sorted(core, key=universe.market_cap, reverse=True)

def test_every_company_has_a_price_cap_sector_and_a_known_home_fund():
    for t, row in market.COMPANIES.items():
        assert float(row["price"]) > 0 and universe.market_cap(t) > 0
        assert universe.sector(t) in universe.SECTORS and row["etf"] in market.ETFS

def test_theme_companies_are_the_themes_biggest():
    two = universe.theme_companies("tech", 2)
    assert set(two) == {"AAPL", "MSFT"} and universe.market_cap(two[0]) >= universe.market_cap(two[1])
```

- [ ] **Step 2: Run it** with `pytest tests/test_universe.py -v`. Expected: it fails on importing `core.universe`.
- [ ] **Step 3: Fill in the data.**
  - Add the four columns to `data/companies.csv`.
  - Add US companies until every sector has at least 3, with about 55 core rows.
  - Add these brand tickers with `core=0` unless they're among the largest: TGT, SBUX, MCD, CMG, UBER, KR, NKE, DIS, VZ, T, LIN, SHW.
  - Caps come from companiesmarketcap.com (or similar) on one date, rounded to $1B. Prices for new rows are the last Nasdaq close.
  - Put the source and date in `docs/data_sources.md`.
- [ ] **Step 4: Implement `core/universe.py`** to read `market.COMPANIES`. There's no second csv reader.
- [ ] **Step 5: Run `pytest -q`.** Expected: all pass, including `tests/test_data.py`. Update `test_data` only for the new columns, and say why in the commit.
- [ ] **Step 6: Commit** with the message "Company universe: sector, market cap, country, core flag".

### Task 2: Spending profile from Nessie

**Files:**
- Create `data/merchants.csv`, `data/category_leaders.csv` and `core/spending.py`.
- Modify `core/seed.py` and `data/personas.json`.
- Test `tests/test_spending.py`.

**Interfaces:**
- Produces:
  - `Spend` (Contract 6).
  - `spending_profile(cid: str, days: int = 90) -> list[Spend]`, sorted by dollars, largest first. Each merchant appears once.
  - `company_spend(profile) -> dict[str, float]`, ticker to dollars, with the category fallback applied.
- Consumes: `nessie.get_purchases`, `ids["_merchants"]`, `live_clock` (live mode); `seed.plan_persona` (fixture mode).

- [ ] **Step 1: Write the failing tests:**

```python
def test_brand_purchases_map_to_their_company(fake):            # live, FakeNessie
    p = spending_profile("sam")
    target = next(s for s in p if s.merchant == "Target")
    assert (target.ticker, target.via) == ("TGT", "brand") and target.dollars > 0

def test_private_merchants_fall_back_to_category_leaders():
    spend = company_spend([Spend("Berkeley Bowl", "Groceries", 300.0, None, "category")])
    assert set(spend) == set(CATEGORY_LEADERS["Groceries"]) and sum(spend.values()) == pytest.approx(300)

def test_bills_and_one_off_events_dont_count(fake):
    apply_life_event("sam", "car_repair")
    assert all(s.merchant != "Mike's Auto" for s in spending_profile("sam"))

def test_only_the_last_90_days_count(fake): ...                 # purchases dated before as_of - 90 days are ignored

def test_live_and_fixture_profiles_match(fake, monkeypatch):    # same parity rule as tests/test_live.py
    assert in_mode(monkeypatch, "fixture", spending_profile, "sam") == spending_profile("sam")

def test_offline_falls_back_like_the_profile(fake, monkeypatch): ...   # outage -> fixture profile, no exception
```

- [ ] **Step 2: Run the tests.** Expected: import errors.
- [ ] **Step 3: Give each persona merchants in `data/personas.json`** (tell Lance), as a list of (name, category, share of spending):
  - Maria: Walmart, Starbucks, Uber.
  - Jordan: Target, Chipotle, a local cafe.
  - Sam: Trader Joe's (private), Target, Netflix.
  - Alex: Costco, Amazon, Tesla Supercharger.

  `seed.py` creates the merchants once per name and spreads `PURCHASES` across them by share.
- [ ] **Step 4: Implement `core/spending.py`.**
  - Purchases are the `[spend]`-tagged ones in the persona's checking and card accounts. Ignore `[event]`.
  - The window is `as_of - days` to `as_of`. `as_of` is the sim clock: `live_clock` in live mode, `SIM_START` plus fixture moves in fixture mode.
  - The merchant comes from the purchase's `merchant_id`, through the ids file.
  - Brand matching uses `data/merchants.csv` patterns, case-insensitive, first match wins.
  - With no match, `via="category"` uses `data/category_leaders.csv`; if the category has no row, `via="none"`.
- [ ] **Step 5: Re-seed the live personas** with `NESSIE_MODE=live python -m scripts.seed_nessie --reset <cid>` for each. Check with `python -m core.profile --diff`; expected "OK" for all four.
- [ ] **Step 6: Run `pytest -q`; all pass. Commit** with the message "Spending profile: Nessie purchases -> companies (brand, else category leaders)".

### Task 3: The US core of individual companies

**Files:**
- Modify `core/models.py` (Contract 1–3) and `core/portfolio.py`.
- Test `tests/test_custom_index.py`.

**Interfaces:**
- Produces:
  - `build_portfolio(level, interests=None, stocks=None, *, bond_share=None, score=None, spending=None) -> Portfolio`.
  - Constants: `INTL_SHARE = 0.30` (of the stock part, held as VXUS), `CORE_NAME_CAP = 0.06` (of the US core), `COMPANY_CAP = 0.08` (of the fund).
- Consumes: `universe.core_companies()` and `universe.market_cap()`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_core_is_individual_companies_weighted_by_size_and_capped():
    p = build_portfolio(3, [])
    core = [h for h in p.holdings if h.slice == "us_core"]
    assert len(core) == len(universe.core_companies()) and all(h.sector for h in core)
    us = sum(h.weight for h in core)
    assert max(h.weight for h in core) <= CORE_NAME_CAP * us + 1e-4
    big, small = core[0], core[-1]
    assert big.weight > small.weight                              # by market cap

def test_bonds_and_international_stay_funds_at_their_shares():
    p = build_portfolio(2, [])
    w = p.slice_weights()
    assert w["bonds"] == pytest.approx(0.45) and w["intl"] == pytest.approx(0.55 * INTL_SHARE, abs=1e-3)
    assert [h.ticker for h in p.by_slice()["intl"]] == ["VXUS"]

def test_every_core_company_has_a_reason_naming_its_size_rank_and_sector():
    p = build_portfolio(3, [])
    for h in p.by_slice()["us_core"]:
        r = next(r for r in p.reasons if r.ticker == h.ticker and r.kind == "core")
        assert h.sector in r.text
```

- [ ] **Step 2: Run the tests.** Expected: they fail on the missing `slice` attribute.
- [ ] **Step 3: Implement.**
  - US core weight = (1 − bonds) × (1 − `INTL_SHARE`) minus the spend, interest and added slices.
  - Within the core, weights are proportional to market cap, capped at `CORE_NAME_CAP` of the core. Reuse the bisection approach from `_fit_scale`: cap, then spread the excess proportionally until nothing passes the cap.
  - Remove `RETURNS["VTI"]` as a holding, but keep it as the 2008/2022 reference for `worst_year`. Companies count as beta × VTI's return (approximate, labeled), VXUS and BND keep their real returns.
  - Reason text: "{name} ({ticker}): the {rank}th biggest US company we hold, in {sector}. Bigger companies get more, like an index fund; none gets more than 6% of your US index."
- [ ] **Step 4: Run `pytest tests/test_custom_index.py -v`;** the tests pass. Then run `pytest -q`. Payday and readiness tests that read `VTI` weights fail. Update each one deliberately to read `slice_weights()["us_core"]`, and record why in the commit.
- [ ] **Step 5: Commit** with the message "Your own index: about 50 US companies by size, VXUS and BND".

### Task 4: Spend slice, interest companies, one cap per company

**Files:**
- Modify `core/portfolio.py`, `core/builder.py` and `core/fund.py`.
- Test `tests/test_custom_index.py`.

**Interfaces:**
- Produces: `SPEND_CAP_BY_RISK = {1: 0.05, 2: 0.08, 3: 0.10, 4: 0.12}`, `MIN_PICK = 0.005`, `SPEND_PICKS = 8`, `THEME_PICKS = 2`.
- `builder.build(answers, spending: list[Spend] | None = None)`. `fund.build(acct)` passes `spending.spending_profile(acct.customer_id)`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_spend_slice_follows_dollars_within_its_cap():
    spend = [Spend("Target", "Shopping", 600, "TGT", "brand"), Spend("Starbucks", "Restaurants", 200, "SBUX", "brand")]
    p = build_portfolio(3, [], spending=spend)
    s = {h.ticker: h.weight for h in p.by_slice()["spend"]}
    assert sum(s.values()) == pytest.approx(SPEND_CAP_BY_RISK[3]) and s["TGT"] == pytest.approx(3 * s["SBUX"])
    assert "personaliz" in next(r for r in p.reasons if r.ticker == "TGT").text.lower()

def test_no_spending_means_no_spend_slice_and_says_so():
    p = build_portfolio(3, [], spending=[])
    assert not p.by_slice()["spend"] and any(r.kind == "spend" and r.ticker is None for r in p.reasons)

def test_interest_becomes_its_biggest_companies_or_its_fund_if_none():
    p = build_portfolio(4, ["clean_energy", "gold"])
    assert {h.ticker for h in p.by_slice()["interest"]} == {*universe.theme_companies("clean_energy", THEME_PICKS), "GLD"}

def test_no_company_reaches_8_percent_of_the_fund_across_slices():
    spend = [Spend("Apple Store", "Electronics", 5000, "AAPL", "brand")]
    p = build_portfolio(4, ["tech"], ["AAPL"], spending=spend)
    total = {}
    for h in p.holdings:
        total[h.ticker] = total.get(h.ticker, 0) + h.weight
    assert max(total.values()) < COMPANY_CAP
    assert any(r.kind == "cap" and r.ticker == "AAPL" for r in p.reasons)
```

- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Implement.**
  - **Spend slice:** take `company_spend` and keep the top `SPEND_PICKS` by dollars. Weights are proportional to dollars and sum to `SPEND_CAP_BY_RISK[level]`. Picks under `MIN_PICK` are dropped, each with a reason, and their weight goes to the rest.
  - **Interest slice:** each interest's budget comes from `_size_interests` (PR #5, unchanged), split across `theme_companies(theme, THEME_PICKS)` in proportion to market cap. A theme with no companies keeps its theme fund.
  - **Company cap:** a ticker held in more than one slice (core plus spend plus interest plus added) is one position. A position can't pass `COMPANY_CAP`. Trim the core part of that company first, then the pick, and give the trimmed weight to the rest of the core. Each trim gets a "cap" reason.
  - **Reasons:**
    - Spend, brand: "You spent ${dollars:,.0f} at {merchant} in the last 90 days, so {name} is {w} of your fund. This personalizes your fund; it isn't a prediction."
    - Spend, category: "{merchant} isn't a public company, so your {category} spending goes to its biggest public companies: {names}."
- [ ] **Step 4: Run `pytest -q`.** Expected: all pass, the PR #5 property grid included. That grid gets `spending=None` (no slice) until Task 6.
- [ ] **Step 5: Commit** with the message "Where you spend and your interests as companies; one 8% cap per company".

### Task 5: Explanation data for the new slices

**Files:**
- Modify `core/explain.py`.
- Test `tests/test_explain_page.py`. The template change is Ethan's; this task tests the data only.

- [ ] **Step 1: Write the failing test:** `explain.build(acct)["slices"]` follows `SLICES` order, each slice's weight equals `p.slice_weights()[key]`, and every holding's texts are its reasons. The full version has these sections:
  - "Why your own index" (replaces "Why index funds")
  - "Where you spend"
  - "Why some parts stay funds" (bonds, international, themes with no companies)
- [ ] **Step 2: Run the test.** Expected: KeyError on `"slices"`.
- [ ] **Step 3: Implement.**
  - The copy is fixed text plus reason texts, with no numbers typed in.
  - "Why your own index": owning about 50 companies directly gives the same spread as an index fund's biggest names, lets you see each one, and lets the fund lean toward your life. The cost is that it misses thousands of smaller companies, so it tracks the market closely but not exactly. That's labeled.
- [ ] **Step 4: Run `pytest -q`. Commit** with the message "Explanation data: slices, your own index, where you spend".

### Task 6: Properties and golden funds

**Files:**
- Modify `tests/test_fund_properties.py` and `tests/fund_helpers.py`.

- [ ] **Step 1: Extend the grid** with spending ∈ {none, one brand, eight brands with one also a core company, private merchants only}. Add these properties:
  - weights sum to 1, with no negatives;
  - no company ≥ `COMPANY_CAP` across slices;
  - the spend slice is ≤ `SPEND_CAP_BY_RISK[level]`;
  - every spend pick traces to a `Spend` record;
  - every sector in `SECTORS` appears in the US core;
  - VXUS = (1 − bonds) × `INTL_SHARE` within 0.1%;
  - the PR #5 properties still hold (bonds monotone, max-drop ceiling, horizon cap, a reason for every holding, drop and trim).
- [ ] **Step 2: Golden funds.** Pin the four personas with their spending profiles: the slice weights, the spend tickers, and the reason kinds. Pin slice-level numbers, not all 60 weights.
- [ ] **Step 3: Run `pytest -q`;** all pass, in under 30 seconds. **Commit** with the message "Custom index: property grid with spending, golden personas".

### After Task 6

- Merge `lance/fund-builder` with a merge commit. Run `pytest -q` and `bash scripts/git-hooks/pre-commit`.
- Run the trial merge with Ethan's branch and check the contract fields line up.
- Open a PR into `lance/fund-builder`. It should cover before/after slice weights for each persona, the data sources and dates, and what's approximate.
