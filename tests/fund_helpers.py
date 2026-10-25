"""Give a demo persona a built fund without clicking through the chat."""
from app import state
from core.models import Portfolio
from core import fund
from core.intake import QUESTIONS

ANSWERS = {"goal": "A house", "starting_amount": 3000, "paycheck_pct": 10, "risk_priority": "steady",
           "risk_range": "medium", "max_drop": 15, "horizon": "7to15",
           "interests": {"themes": ["tech"], "unmatched": []}}


# One answer set per demo persona, for golden tests and the explanation page. Alex's is the app's preset
# (app/routes/pages.DEMO_PRESETS); Maria, Jordan and Sam start at the intake in the demo.
PERSONA_ANSWERS = {
    "maria": {"goal": "An emergency fund, then a car", "starting_amount": 500, "paycheck_pct": 5,
              "risk_priority": "protect", "risk_range": "narrow", "max_drop": 10, "horizon": "3to7",
              "interests": {"themes": ["staples"], "unmatched": []}},
    "jordan": {"goal": "Our wedding in a few years", "starting_amount": 1000, "paycheck_pct": 8,
               "risk_priority": "steady", "risk_range": "medium", "max_drop": 20, "horizon": "3to7",
               "interests": {"themes": ["gaming", "communications"], "unmatched": []}},
    "sam": {"goal": "Buy a house someday", "starting_amount": 3000, "paycheck_pct": 10,
            "risk_priority": "growth", "risk_range": "medium", "max_drop": 25, "horizon": "7to15",
            "interests": {"themes": ["tech", "healthcare"], "unmatched": []}},
}


def make_fund(cid: str = "sam", **answers) -> state.Session:
    s = state.get(cid)
    s.fund.answers = {**ANSWERS, **answers}
    fund.start(s.fund)
    s.step = len(QUESTIONS)
    s.say("Your fund is ready.")
    return s


def _alex():
    from app.routes.pages import DEMO_PRESETS
    return dict(DEMO_PRESETS["alex"]["answers"])


PERSONA_ANSWERS["alex"] = _alex()


# ---------- The custom-index contract (docs/superpowers/plans/2026-10-24-custom-index-fund.md) ----------
# 50 US companies (name, sector, rough size in $B: only the order and ratios matter here), so pages and report
# can be built and tested before the engine produces a real fund.
_US_CORE = [
    ("AAPL", "Apple", "Technology", 3500), ("MSFT", "Microsoft", "Technology", 3700), ("NVDA", "NVIDIA", "Technology", 4000),
    ("AVGO", "Broadcom", "Technology", 1300), ("ORCL", "Oracle", "Technology", 600), ("CRM", "Salesforce", "Technology", 250),
    ("AMD", "Advanced Micro Devices", "Technology", 250), ("ADBE", "Adobe", "Technology", 170), ("CSCO", "Cisco", "Technology", 250),
    ("GOOGL", "Alphabet", "Communication", 2200), ("META", "Meta Platforms", "Communication", 1700),
    ("NFLX", "Netflix", "Communication", 500), ("DIS", "Disney", "Communication", 200), ("VZ", "Verizon", "Communication", 190),
    ("AMZN", "Amazon", "Consumer", 2300), ("TSLA", "Tesla", "Consumer", 1000), ("HD", "Home Depot", "Consumer", 380),
    ("MCD", "McDonald's", "Consumer", 210), ("NKE", "Nike", "Consumer", 90), ("LOW", "Lowe's", "Consumer", 140),
    ("WMT", "Walmart", "Everyday essentials", 750), ("PG", "Procter & Gamble", "Everyday essentials", 380),
    ("KO", "Coca-Cola", "Everyday essentials", 300), ("PEP", "PepsiCo", "Everyday essentials", 200),
    ("LLY", "Eli Lilly", "Healthcare", 800), ("UNH", "UnitedHealth", "Healthcare", 300), ("JNJ", "Johnson & Johnson", "Healthcare", 380),
    ("ABBV", "AbbVie", "Healthcare", 330), ("MRK", "Merck", "Healthcare", 220),
    ("JPM", "JPMorgan Chase", "Financials", 700), ("V", "Visa", "Financials", 650), ("MA", "Mastercard", "Financials", 500),
    ("BAC", "Bank of America", "Financials", 330), ("WFC", "Wells Fargo", "Financials", 250), ("GS", "Goldman Sachs", "Financials", 190),
    ("GE", "GE Aerospace", "Industrials", 280), ("CAT", "Caterpillar", "Industrials", 190), ("RTX", "RTX", "Industrials", 190),
    ("UNP", "Union Pacific", "Industrials", 140), ("HON", "Honeywell", "Industrials", 140),
    ("XOM", "Exxon Mobil", "Energy", 480), ("CVX", "Chevron", "Energy", 280), ("COP", "ConocoPhillips", "Energy", 130),
    ("NEE", "NextEra Energy", "Utilities", 150), ("SO", "Southern Company", "Utilities", 95), ("DUK", "Duke Energy", "Utilities", 90),
    ("PLD", "Prologis", "Real estate", 100), ("AMT", "American Tower", "Real estate", 100),
    ("LIN", "Linde", "Materials", 220), ("SHW", "Sherwin-Williams", "Materials", 90),
]
_BONDS, _INTL, _SPEND, _INTEREST, _ADDED = 0.25, 0.225, 0.08, 0.06, 0.03   # slice weights; the US index gets the rest
CAP_OF_US_CORE = 0.06     # no company over 6% of the US index (the contract)


def make_portfolio_fixture() -> Portfolio:
    """A 60-holding fund in the contract's shape: BND, VXUS, 50 us_core companies, 3 spend, 2 interest, 1 added.
    Weights sum to 1, every holding has a sector (companies) or "" (funds), a slice, and at least one reason."""
    from core.models import Holding, Portfolio, Reason
    us_total = round(1 - _BONDS - _INTL - _SPEND - _INTEREST - _ADDED, 6)
    core = sorted(_US_CORE, key=lambda c: -c[3])            # biggest first, so a company's rank in its reason is true
    weights = _capped([c for *_, c in core], CAP_OF_US_CORE)
    h, why = [], []

    def add(ticker, name, w, role, slice_, sector, kind, text):
        h.append(Holding(ticker, name, round(w, 6), role, 0.0 if sector else 0.05, sector, slice_))
        why.append(Reason(kind, text, ticker))

    add("BND", "Vanguard Total Bond Market", _BONDS, "core", "bonds", "", "bonds", "Bonds soften the dips.")
    add("VXUS", "Vanguard Total International Stock", _INTL, "core", "intl", "", "core", "Companies outside the US.")
    for i, ((t, name, sector, _), w) in enumerate(zip(core, weights), 1):
        add(t, name, w * us_total, "core", "us_core", sector, "core",
            f"{name} ({t}): the {_ordinal(i)} biggest US company we hold, in {sector}.")
    for (t, name, sector), w in zip((("TGT", "Target", "Consumer"), ("SBUX", "Starbucks", "Consumer"),
                                     ("COST", "Costco", "Everyday essentials")), (0.04, 0.025, 0.015)):
        add(t, name, w, "spend", "spend", sector, "spend",
            f"You spent $300 at {name} in the last 90 days, so it is part of your fund. This personalizes your fund; it isn't a prediction.")
    for (t, name, sector), w in zip((("VRTX", "Vertex Pharmaceuticals", "Healthcare"), ("REGN", "Regeneron", "Healthcare")), (0.035, 0.025)):
        add(t, name, w, "tilt", "interest", sector, "tilt", f"You're interested in biotech, and {name} is one of its biggest companies.")
    add("PYPL", "PayPal", _ADDED, "stock", "added", "Financials", "stock", "You added PayPal.")
    total = sum(x.weight for x in h)
    h[2].weight = round(h[2].weight + 1 - total, 6)      # the largest company absorbs rounding, so weights sum to 1
    parts: dict[str, dict[str, float]] = {}                  # the contract's exact shares: slice -> {ticker: weight}
    for x in h:
        parts.setdefault(x.slice, {})[x.ticker] = x.weight
    return Portfolio(3, "Growth", h, 0.02, 2022, -17.0, [], why, parts=parts, score=60)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _capped(sizes: list[float], cap: float) -> list[float]:
    """Weights proportional to `sizes` summing to 1, no weight above `cap`: cap, then spread the excess over the rest."""
    w, free = [s / sum(sizes) for s in sizes], set(range(len(sizes)))
    fixed: set[int] = set()
    for _ in range(len(sizes)):
        over = {i for i in free if w[i] > cap + 1e-12}
        if not over:
            break
        fixed |= over
        free -= over
        room = 1 - cap * len(fixed)
        base = sum(sizes[i] for i in free)
        w = [cap if i in fixed else sizes[i] / base * room for i in range(len(sizes))]
    return w


def make_custom_fund(cid: str = "sam", amount: float = 10_000, **answers) -> state.Session:
    """A persona whose fund is the 58-holding contract fixture, with `amount` invested across all of it, so
    every page can be rendered the way it will look with a custom index."""
    s = state.get(cid)
    s.fund.answers = {**ANSWERS, **answers}
    s.fund.portfolio = make_portfolio_fixture()
    s.fund.risk_score, s.fund.invest_pct = s.fund.portfolio.score, float(s.fund.answers.get("paycheck_pct", 10))
    fund.invest(s.fund, amount)
    s.fund.contributed = amount
    s.step = len(QUESTIONS)
    s.say("Your fund is ready.")
    return s


def make_legacy_etf_fund() -> Portfolio:
    """An older three-fund fund (VTI/VXUS/BND plus an interest fund and an added company), with no slice, sector or
    parts set: what a fund built before the custom index looks like. Pages must still read it."""
    from core.models import Holding, Portfolio
    h = [Holding("VTI", "Vanguard Total Stock Market", 0.28, "core", 0.03), Holding("VXUS", "Vanguard Total International Stock", 0.12, "core", 0.05),
         Holding("BND", "Vanguard Total Bond Market", 0.45, "core", 0.03), Holding("XLK", "Technology Select Sector SPDR", 0.10, "tilt", 0.08),
         Holding("AAPL", "Apple", 0.05, "stock", 0.0)]
    return Portfolio(2, "Moderate", h, 0.04, 2022, -15.6, [], [], score=40)


def make_legacy_fund(cid: str = "sam", amount: float = 4_000) -> state.Session:
    """A persona holding the hand-built older ETF fund (5 holdings, no slices): pages must still read it."""
    s = state.get(cid)
    s.fund.answers = {**ANSWERS}
    s.fund.portfolio = make_legacy_etf_fund()
    s.fund.risk_score, s.fund.invest_pct = s.fund.portfolio.score, 10.0
    fund.invest(s.fund, amount)
    s.fund.contributed = amount
    s.step = len(QUESTIONS)
    s.say("Your fund is ready.")
    return s
