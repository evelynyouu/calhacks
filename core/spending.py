"""Spending-based fund and risk insights from C1/Nessie banking data.  OWNER: Saahil.

Two features:
- Theme suggestions: map what a customer spends on (purchases + bills) to fund themes they might
  care about. "You spend at restaurants → the consumer sector fund tracks those companies."
- Risk adjustment: use actual banking data (cushion months, debt, surplus share) to sanity-check
  the risk level that came out of the intake questionnaire.
- Reinvestment signal: compare observed spending to the monthly baseline to surface money that
  could be invested right now.
"""
from __future__ import annotations

from core import nessie
from core.models import FinancialProfile

# Purchase description text (lower-case substring) → fund theme (etfs.csv theme column)
_PURCHASE_KEYWORDS: list[tuple[str, str]] = [
    # groceries / food at home → consumer staples
    ("bowl", "staples"), ("grocery", "staples"), ("whole foods", "staples"),
    ("trader joe", "staples"), ("safeway", "staples"), ("albertsons", "staples"),
    ("sprouts", "staples"), ("costco", "staples"),
    # dining out → consumer discretionary
    ("pizza", "consumer"), ("restaurant", "consumer"), ("cafe", "consumer"),
    ("dining", "consumer"), ("sliver", "consumer"), ("chipotle", "consumer"),
    ("coffee", "consumer"), ("starbucks", "consumer"), ("mcdonald", "consumer"),
    # transit / transport → industrials
    ("transit", "industrials"), ("bart", "industrials"), ("caltrain", "industrials"),
    ("uber", "industrials"), ("lyft", "industrials"), ("airline", "industrials"),
    ("amtrak", "industrials"),
    # auto services → industrials; fuel → oil & gas
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
    # media / streaming → communications
    ("netflix", "communications"), ("spotify", "communications"), ("hulu", "communications"),
    ("at&t", "communications"), ("verizon", "communications"), ("t-mobile", "communications"),
    ("comcast", "communications"), ("xfinity", "communications"),
    # gaming
    ("gaming", "gaming"), ("steam", "gaming"), ("playstation", "gaming"), ("xbox", "gaming"),
    ("nintendo", "gaming"),
    # finance
    ("insurance", "financials"), ("bank fee", "financials"),
]

# Bill name keywords → suggested theme (bills show you depend on these sectors)
_BILL_KEYWORDS: list[tuple[str, str]] = [
    ("rent", "real_estate"),      # paying rent → REITs let you own a slice
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


# ---------- helpers ----------

def _theme_label(theme: str) -> str:
    from core.market import ETFS
    return next((r["label"] for r in ETFS.values() if r.get("theme") == theme),
                theme.replace("_", " ").title())


def _theme_etf(theme: str) -> str:
    from core.market import ETFS
    return next((t for t, r in ETFS.items() if r.get("theme") == theme), theme)
