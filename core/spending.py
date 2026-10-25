"""Spending-based fund and risk insights from C1/Nessie banking data.  OWNER: Saahil.

Three features:
- Direct stock mapping: map what a customer spends on to the actual companies they're buying
  from. "You buy groceries at Costco -> hold COST." Weighted by actual dollars spent.
- Theme suggestions: map spending patterns to fund themes for interest discovery.
- Risk adjustment: use actual banking data (cushion months, debt, surplus share) to sanity-check
  the risk level from the intake questionnaire.
- Reinvestment signal: compare observed spending to the monthly baseline to surface money that
  could be invested right now.
"""
from __future__ import annotations

from core import nessie
from core.models import FinancialProfile

# ---------------------------------------------------------------------------
# Direct merchant name -> list of investable tickers (ordered by conviction).
# The first ticker in each list gets the highest weight when allocating spending.
# We use substrings (lower-case) so "Berkeley Bowl" matches "berkeley bowl".
# ---------------------------------------------------------------------------
MERCHANT_TO_TICKERS: list[tuple[str, list[str]]] = [
    # --- Groceries ---
    ("berkeley bowl", ["COST", "WMT", "PG"]),
    ("whole foods",   ["AMZN", "COST"]),
    ("trader joe",    ["COST", "WMT"]),
    ("safeway",       ["WMT", "COST"]),
    ("kroger",        ["WMT", "COST"]),
    ("costco",        ["COST"]),
    ("walmart",       ["WMT"]),
    ("sprouts",       ["COST", "WMT"]),
    ("grocery",       ["COST", "WMT"]),
    ("albertsons",    ["WMT", "COST"]),
    # --- Dining / restaurants ---
    ("starbucks",     ["SBUX"]),
    ("sliver",        ["SBUX", "MCD"]),   # local pizza -> restaurant sector
    ("pizza",         ["MCD", "SBUX"]),
    ("chipotle",      ["MCD", "SBUX"]),   # no CMG in our universe
    ("mcdonald",      ["MCD"]),
    ("restaurant",    ["MCD", "SBUX"]),
    ("cafe",          ["SBUX"]),
    ("coffee",        ["SBUX"]),
    ("dining",        ["MCD", "SBUX"]),
    ("doordash",      ["AMZN", "GOOGL"]),  # delivery -> logistics + tech
    ("grubhub",       ["MCD", "SBUX"]),
    # --- Transit / transport ---
    ("bay area transit", ["GE", "UNP"]),
    ("bart",          ["GE", "UNP"]),
    ("caltrain",      ["GE", "UNP"]),
    ("transit",       ["GE", "UNP"]),
    ("uber",          ["DAL", "GE"]),     # no UBER in our universe
    ("lyft",          ["DAL", "GE"]),
    ("airline",       ["DAL"]),
    ("delta",         ["DAL"]),
    ("united",        ["DAL"]),
    ("southwest",     ["DAL"]),
    ("amtrak",        ["GE", "UNP"]),
    ("auto repair",   ["GE", "CAT"]),
    # --- Tech / electronics ---
    ("apple store",   ["AAPL"]),
    ("best buy",      ["AAPL", "MSFT", "AMZN"]),
    ("amazon",        ["AMZN"]),
    ("microsoft",     ["MSFT"]),
    ("google",        ["GOOGL"]),
    ("newegg",        ["NVDA", "AMD"]),
    ("software",      ["MSFT", "ORCL"]),
    # --- Entertainment / streaming ---
    ("netflix",       ["NFLX"]),
    ("spotify",       ["DIS", "NFLX"]),
    ("disney",        ["DIS"]),
    ("hulu",          ["DIS"]),
    ("youtube",       ["GOOGL"]),
    ("twitch",        ["AMZN"]),
    # --- Gaming ---
    ("steam",         ["MSFT", "NVDA"]),
    ("playstation",   ["MSFT", "AMD"]),
    ("xbox",          ["MSFT"]),
    ("nintendo",      ["NTDOY"]),
    ("gaming",        ["MSFT", "NVDA", "AMD"]),
    # --- Telecom / internet ---
    ("verizon",       ["VZ"]),
    ("at&t",          ["T"]),
    ("t-mobile",      ["VZ", "T"]),
    ("comcast",       ["T", "VZ"]),
    ("xfinity",       ["T", "VZ"]),
    # --- Energy / gas stations ---
    ("shell",         ["XOM", "CVX"]),
    ("chevron",       ["CVX"]),
    ("exxon",         ["XOM"]),
    ("gas station",   ["XOM", "CVX"]),
    ("bp ",           ["XOM", "CVX"]),
    # --- Healthcare ---
    ("cvs",           ["CVS"]),
    ("walgreens",     ["CVS"]),
    ("pharmacy",      ["CVS"]),
    ("hospital",      ["UNH", "JNJ"]),
    ("medical",       ["UNH", "JNJ"]),
    ("clinic",        ["UNH"]),
    ("urgent care",   ["UNH"]),
    # --- Finance / payments ---
    ("visa",          ["V"]),
    ("mastercard",    ["MA"]),
    ("chase",         ["JPM"]),
    ("bank of america", ["BAC"]),
    ("insurance",     ["BRK.B", "V"]),
    ("bank fee",      ["JPM", "BAC"]),
    # --- Utilities ---
    ("pg&e",          ["NEE", "SO"]),
    ("electric",      ["NEE", "SO"]),
    ("utility",       ["NEE", "DUK"]),
    # --- Real estate (bill keywords) ---
    ("rent",          ["PLD", "AMT"]),
    ("mortgage",      ["PLD", "EQIX"]),
    # --- Telecom bills ---
    ("phone",         ["VZ", "T"]),
    ("internet",      ["VZ", "T"]),
]

# Purchase description text (lower-case substring) -> fund theme (for theme suggestions)
_PURCHASE_KEYWORDS: list[tuple[str, str]] = [
    # groceries / food at home -> consumer staples
    ("bowl", "staples"), ("grocery", "staples"), ("whole foods", "staples"),
    ("trader joe", "staples"), ("safeway", "staples"), ("albertsons", "staples"),
    ("sprouts", "staples"), ("costco", "staples"),
    # dining out -> consumer discretionary
    ("pizza", "consumer"), ("restaurant", "consumer"), ("cafe", "consumer"),
    ("dining", "consumer"), ("sliver", "consumer"), ("chipotle", "consumer"),
    ("coffee", "consumer"), ("starbucks", "consumer"), ("mcdonald", "consumer"),
    # transit / transport -> industrials
    ("transit", "industrials"), ("bart", "industrials"), ("caltrain", "industrials"),
    ("uber", "industrials"), ("lyft", "industrials"), ("airline", "industrials"),
    ("amtrak", "industrials"),
    # auto services -> industrials; fuel -> oil & gas
    ("auto repair", "industrials"), ("auto", "industrials"), ("mechanic", "industrials"),
    ("gas station", "oil_gas"), ("shell", "oil_gas"), ("chevron", "oil_gas"),
    ("exxon", "oil_gas"), ("bp ", "oil_gas"),
    # tech / software
    ("apple store", "tech"), ("best buy", "tech"), ("microsoft", "tech"),
    ("amazon", "tech"), ("software", "software"), ("newegg", "tech"),
    # healthcare
    ("hospital", "healthcare"), ("pharmacy", "healthcare"), ("cvs", "healthcare"),
    ("walgreens", "healthcare"), ("medical", "healthcare"), ("clinic", "healthcare"),
    ("urgent care", "healthcare"),
    # utilities
    ("pg&e", "utilities"), ("electric", "utilities"), ("utility", "utilities"),
    # media / streaming -> communications
    ("netflix", "communications"), ("spotify", "communications"), ("hulu", "communications"),
    ("at&t", "communications"), ("verizon", "communications"), ("t-mobile", "communications"),
    ("comcast", "communications"), ("xfinity", "communications"),
    # gaming
    ("gaming", "gaming"), ("steam", "gaming"), ("playstation", "gaming"), ("xbox", "gaming"),
    ("nintendo", "gaming"),
    # finance
    ("insurance", "financials"), ("bank fee", "financials"),
]

# Bill name keywords -> suggested theme
_BILL_KEYWORDS: list[tuple[str, str]] = [
    ("rent", "real_estate"),
    ("phone", "communications"),
    ("utility", "utilities"),
    ("electric", "utilities"),
    ("internet", "communications"),
    ("health", "healthcare"),
    ("insurance", "financials"),
    ("car", "industrials"),
]


def _match_keywords(text: str, table: list[tuple[str, str]]) -> str | None:
    t = text.lower()
    for keyword, theme in table:
        if keyword in t:
            return theme
    return None


def _match_tickers(text: str) -> list[str]:
    """Return list of tickers matched by the first matching merchant rule (order matters)."""
    t = text.lower()
    for keyword, tickers in MERCHANT_TO_TICKERS:
        if keyword in t:
            return tickers
    return []


def spending_stock_weights(cid: str) -> dict[str, float]:
    """Map Nessie activity to {ticker: total_dollars} for portfolio building.

    Pulls all spend and bill activity and maps merchant/payee names directly to
    investable tickers. Returns raw dollar totals — callers normalize to weights.
    A transaction can match multiple tickers; dollars are split equally across them.
    """
    return {t: v["amount"] for t, v in spending_stock_details(cid).items()}


def spending_stock_details(cid: str) -> dict[str, dict]:
    """Like spending_stock_weights but returns {ticker: {amount, source, label}} so callers
    can generate human-readable reasons ('You pay rent' vs 'You shop at Costco')."""
    items = nessie.recent_activity(cid, limit=None)
    ticker_info: dict[str, dict] = {}

    for item in items:
        if item["tag"] not in ("spend", "bill"):
            continue
        tickers = _match_tickers(item["text"])
        if not tickers:
            continue
        per_ticker = item["amount"] / len(tickers)
        source = item["tag"]     # "spend" or "bill"
        label = item["text"]     # e.g. "Berkeley Bowl", "Rent"
        for ticker in tickers:
            if ticker not in ticker_info:
                ticker_info[ticker] = {"amount": 0.0, "source": source, "label": label}
            ticker_info[ticker]["amount"] += per_ticker
            # Upgrade source label: if the same ticker appears in both spend and bills,
            # prefer the description with the higher amount (already accumulated).

    return ticker_info


def spending_breakdown(cid: str, profile: FinancialProfile) -> dict:
    """Analyze Nessie activity + bill names to surface fund themes and a reinvestment signal.

    Returns:
        categories: {category_label: dollars seen in feed}
        theme_suggestions: up to 5 [{theme, etf, why}] ordered by confidence
        spending_surplus_estimate: dollars/month the customer spent below their own baseline
        baseline_monthly_spending: from FinancialProfile
        observed_monthly_spending: derived from the activity feed
    """
    items = nessie.recent_activity(cid, limit=None)
    spend_items = [i for i in items if i["tag"] == "spend"]

    # --- purchase tallies ---
    category_totals: dict[str, float] = {}
    theme_dollars: dict[str, float] = {}
    for item in spend_items:
        theme = _match_keywords(item["text"], _PURCHASE_KEYWORDS)
        label = _theme_label(theme) if theme else "Other"
        category_totals[label] = category_totals.get(label, 0.0) + item["amount"]
        if theme:
            theme_dollars[theme] = theme_dollars.get(theme, 0.0) + item["amount"]

    # --- bill-based hints ---
    theme_from_bills: dict[str, str] = {}
    for bill in profile.upcoming_bills:
        theme = _match_keywords(bill.name, _BILL_KEYWORDS)
        if theme:
            theme_from_bills[theme] = bill.name

    # --- build suggestions ---
    suggestions: list[dict] = []
    seen: set[str] = set()

    for theme, amount in sorted(theme_dollars.items(), key=lambda x: -x[1]):
        if theme not in seen:
            suggestions.append({
                "theme": theme,
                "etf": _theme_etf(theme),
                "why": f"You spent ${amount:,.0f} on {_theme_label(theme).lower()} recently",
            })
            seen.add(theme)

    for theme, bill_name in theme_from_bills.items():
        if theme not in seen:
            suggestions.append({
                "theme": theme,
                "etf": _theme_etf(theme),
                "why": (f"You pay {bill_name.lower()} — owning "
                        f"{_theme_label(theme)} ({_theme_etf(theme)}) puts you on the other side"),
            })
            seen.add(theme)

    # --- reinvestment signal ---
    months_covered = max(1, len({i["on"][:7] for i in spend_items}))
    observed_monthly = round(sum(i["amount"] for i in spend_items) / months_covered, 2)
    surplus_estimate = max(0.0, round(profile.monthly_spending - observed_monthly, 2))

    return {
        "categories": {k: round(v, 2) for k, v in sorted(category_totals.items(), key=lambda x: -x[1])},
        "theme_suggestions": suggestions[:5],
        "spending_surplus_estimate": surplus_estimate,
        "baseline_monthly_spending": profile.monthly_spending,
        "observed_monthly_spending": observed_monthly,
    }


def banking_risk_adjustment(profile: FinancialProfile, current_level: int) -> dict:
    """Sanity-check the intake risk level against actual banking data.

    Returns:
        suggested_level: 1..4
        delta: shift from current_level (+1, 0, -1)
        reasons: plain-words list
        factors: the numbers behind the call
    """
    cushion = profile.cushion_months
    surplus_pct = (profile.monthly_surplus / profile.monthly_income
                   if profile.monthly_income else 0.0)
    monthly_interest = profile.card_debt * profile.card_apr / 100 / 12
    debt_service_pct = (monthly_interest / profile.monthly_income
                        if profile.monthly_income else 0.0)

    reduce: list[str] = []
    increase: list[str] = []

    if cushion < 1.5:
        reduce.append(
            f"Your cash cushion covers only {cushion:.1f} months — a market dip while you're "
            "cash-thin is hard to ride out without selling at the wrong time"
        )
    if profile.card_debt > 0 and profile.card_apr >= 15.0:
        reduce.append(
            f"High-interest card debt at {profile.card_apr:.0f}% APR costs more than "
            "a conservative fund is assumed to earn; paying it down is a guaranteed return"
        )
    if debt_service_pct > 0.15:
        reduce.append(
            f"Card interest takes {debt_service_pct:.0%} of your monthly income — "
            "less room to hold through a volatile stretch without stress"
        )
    if 0 <= surplus_pct < 0.08:
        reduce.append(
            f"Monthly surplus is only {surplus_pct:.0%} of income — "
            "not much buffer if expenses spike at the same time markets drop"
        )

    if cushion >= 5.0 and not reduce:
        increase.append(
            f"Your cushion ({cushion:.1f} months) is strong — "
            "you can weather a downturn without touching the fund"
        )
    if profile.card_debt == 0 and surplus_pct >= 0.25 and not reduce:
        increase.append(
            f"No card debt and {surplus_pct:.0%} monthly surplus — "
            "real room to absorb volatility and grow"
        )

    if reduce:
        delta, reasons = -1, reduce
    elif increase:
        delta, reasons = 1, increase
    else:
        delta = 0
        reasons = [
            f"Cushion of {cushion:.1f} months and {surplus_pct:.0%} monthly surplus "
            "are consistent with your intake risk level."
        ]

    return {
        "suggested_level": max(1, min(4, current_level + delta)),
        "current_level": current_level,
        "delta": delta,
        "reasons": reasons,
        "factors": {
            "cushion_months": round(cushion, 1),
            "surplus_pct_of_income": round(surplus_pct * 100, 1),
            "card_interest_pct_of_income": round(debt_service_pct * 100, 1),
            "card_apr": profile.card_apr,
        },
    }


def spending_stock_suggestions(cid: str, profile: FinancialProfile) -> list[dict]:
    """Map the customer's top spending categories to specific investable companies.

    For each spending theme, returns the top 2 companies from that ETF's holdings —
    the "invest in what you buy" angle. Capped at 6 total suggestions.

    Returns [{ticker, name, etf, why, from_theme}]
    """
    from core.market import HOLDINGS, COMPANIES, ETFS
    from core.portfolio import THEMES

    breakdown = spending_breakdown(cid, profile)
    suggestions: list[dict] = []
    seen: set[str] = set()

    for item in breakdown["theme_suggestions"]:
        theme = item["theme"]
        etf_ticker = THEMES.get(theme)
        if not etf_ticker:
            continue
        top = sorted(HOLDINGS.get(etf_ticker, []), key=lambda x: -x[1])[:2]
        etf_label = ETFS.get(etf_ticker, {}).get("label", etf_ticker)
        for ticker, _ in top:
            if ticker not in seen:
                seen.add(ticker)
                name = COMPANIES.get(ticker, {}).get("name", ticker)
                suggestions.append({
                    "ticker": ticker,
                    "name": name,
                    "etf": etf_ticker,
                    "etf_label": etf_label,
                    "why": f"{item['why']} — {name} is a top holding in {etf_label}",
                    "from_theme": theme,
                })

    return suggestions[:6]


# ---------- helpers ----------

def _theme_label(theme: str) -> str:
    from core.market import ETFS
    return next((r["label"] for r in ETFS.values() if r.get("theme") == theme),
                theme.replace("_", " ").title())


def _theme_etf(theme: str) -> str:
    from core.market import ETFS
    return next((t for t, r in ETFS.items() if r.get("theme") == theme), theme)
