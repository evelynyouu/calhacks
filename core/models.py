"""Shared data structures. Agree on these before building in parallel.

Rule: Python computes every number. Claude only converses and explains.
Change a field here -> tell the whole team (Evelyn, Saahil, Lance).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Literal


# ---------- Bank side (Evelyn: nessie.py / profile.py) ----------

@dataclass
class Bill:
    name: str
    amount: float
    due_in_days: int          # days from "today" in the demo clock


@dataclass
class FinancialProfile:
    customer_id: str
    name: str
    monthly_income: float
    monthly_bills: float      # fixed: rent, phone, insurance
    monthly_spending: float   # variable: food, transport, fun
    checking_balance: float
    cash_cushion: float       # savings available for emergencies
    card_debt: float
    card_apr: float           # assumed APR from personas.json (Nessie has none)
    invested_balance: float
    paycheck_amount: float
    days_to_payday: int
    upcoming_bills: list[Bill] = field(default_factory=list)

    @property
    def monthly_expenses(self) -> float:
        return self.monthly_bills + self.monthly_spending

    @property
    def monthly_surplus(self) -> float:
        return self.monthly_income - self.monthly_expenses

    @property
    def cushion_months(self) -> float:
        return self.cash_cushion / self.monthly_expenses if self.monthly_expenses else 0.0


ReadinessStatus = Literal["not_yet", "start_small", "ready"]


@dataclass
class Readiness:
    status: ReadinessStatus
    safe_monthly_amount: float
    headline: str             # short, plain-language verdict
    reasons: list[str]        # bullet reasons, computed in Python


# ---------- Engine side (Saahil: portfolio.py / sim.py / rebalance.py / broker.py) ----------

HoldingRole = Literal["core", "tilt", "stock", "spend"]
# The parts of a fund, in display order: (key, label). Every Holding is in exactly one. A company held for
# two reasons (in the US index and where you spend) is one Holding, in its most personal slice.
SLICES = (("bonds", "Bonds"), ("intl", "International"), ("us_core", "Your US index"),
          ("spend", "Where you spend"), ("interest", "Your interests"), ("added", "Companies you added"))

# What a reason explains. Holdings: core, bonds, tilt, stock. Adjustments: cap (shrunk to fit), swing (sized
# down because it moves a lot), dropped (left out). Notes: overlap, concentration, sector. How the mix was
# chosen: score, contradiction, horizon, drop, override.
ReasonKind = Literal["core", "bonds", "tilt", "stock", "cap", "swing", "dropped", "overlap", "concentration",
                     "sector", "score", "contradiction", "horizon", "drop", "override", "spend", "combined"]


@dataclass
class Reason:
    """One decision the fund builder made, in plain words. The "How your fund was built" page and the
    chat's get_fund_explanation tool are generated from these, so the explanation can't drift from the code."""
    kind: ReasonKind
    text: str
    ticker: str | None = None     # the holding (or left-out fund/company) it's about
    facts: dict = field(default_factory=dict)   # the numbers behind the text, for templates and tests


@dataclass
class Holding:
    ticker: str
    name: str
    weight: float             # 0..1, all holdings sum to 1
    role: HoldingRole         # core: the US index, VXUS, BND; spend; tilt: an interest; stock: a company you added
    expense_ratio: float      # percent, e.g. 0.15 means 0.15%
    sector: str = ""          # a company's sector (core/universe.SECTORS); "" for funds
    slice: str = ""           # one of SLICES' keys


@dataclass
class Portfolio:
    risk_level: int           # 1 conservative .. 4 aggressive
    risk_label: str           # "Conservative", "Moderate", ...
    holdings: list[Holding]
    annual_fee_pct: float     # weighted expense ratio, percent
    worst_year: int
    worst_year_return: float  # percent, negative
    overlap_warnings: list[str] = field(default_factory=list)   # the reasons worth flagging (see WARNING_KINDS)
    reasons: list[Reason] = field(default_factory=list)
    parts: dict = field(default_factory=dict)   # slice -> {ticker: weight}: each slice's exact share of each position
    score: int | None = None          # 0..100, what the mix was built at (after any ceilings)
    pointed_level: int | None = None  # the level the answers pointed to, if a ceiling lowered it

    @property
    def holding_reasons(self) -> dict[str, str]:
        """ticker -> why it's here, in one line: the holding's own reasons joined (read by the chat tools)."""
        out: dict[str, list[str]] = {}
        for r in self.reasons:
            if r.ticker and r.kind in ("core", "bonds", "tilt", "stock", "spend", "combined"):
                out.setdefault(r.ticker, []).append(r.text)
        return {t: " ".join(texts) for t, texts in out.items()}

    @property
    def core(self) -> Holding:
        return next(h for h in self.holdings if h.role == "core")

    @property
    def tilt_share(self) -> float:
        """The interest slice. A company held for an interest and in the US index counts only its interest part."""
        if self.parts:
            return round(sum(self.parts.get("interest", {}).values()), 4)
        return sum(h.weight for h in self.holdings if h.role == "tilt")

    def slice_weights(self) -> dict[str, float]:
        """Each slice's share of the fund, in SLICES order (0.0 for an empty slice). Exact when the builder
        recorded parts: a company in two slices counts toward each by its part, not its whole position."""
        if self.parts:
            return {k: round(sum(self.parts.get(k, {}).values()), 4) for k, _ in SLICES}
        return {k: round(sum(h.weight for h in self.holdings if self.slice_of(h) == k), 4) for k, _ in SLICES}

    def by_slice(self) -> dict[str, list[Holding]]:
        """Every slice's holdings (all keys always there; an unused slice is []). An older fund is read by role."""
        return {k: [h for h in self.holdings if self.slice_of(h) == k] for k, _ in SLICES}

    def annual_fee_dollars(self, balance: float) -> float:
        return balance * self.annual_fee_pct / 100

    def slice_of(self, h: Holding) -> str:
        """The holding's slice. An older fund (no slice set) is read from its ticker and role."""
        if h.slice:
            return h.slice
        return {"BND": "bonds", "VXUS": "intl"}.get(h.ticker) or \
            {"core": "us_core", "tilt": "interest", "stock": "added", "spend": "spend"}[h.role]



# Reasons also listed as warnings on the fund card. Any other reason can opt in with facts["warn"].
WARNING_KINDS = ("dropped", "overlap", "concentration", "sector", "contradiction")


@dataclass
class SimResult:
    years: list[int]          # 0..10
    p10: list[float]          # dollars, one value per year
    p50: list[float]
    p90: list[float]
    monthly_contribution: float
    starting_balance: float
    total_contributed: float
    expected_return_pct: float = 0.0   # the assumptions behind the range (yearly, after fees), shown to the customer
    volatility_pct: float = 0.0


@dataclass
class CrashTestResult:
    drop_pct: float           # e.g. -30
    dollar_loss: float
    user_choice: Literal["sell", "hold", "buy"]
    risk_adjusted: bool
    new_portfolio: Portfolio | None
    explanation_facts: list[str]   # numbers for Claude/the UI to explain


TradeSide = Literal["buy", "sell"]


@dataclass
class Trade:
    symbol: str
    side: TradeSide
    notional: float           # dollars
    status: str               # alpaca order status, e.g. "accepted", "filled"
    order_id: str
    est_fill_price: float | None = None   # cached last close; weekend orders won't fill


@dataclass
class PaydayResult:
    paycheck_detected: float
    planned_amount: float
    invested_amount: float
    transfer_id: str | None
    trades: list[Trade]
    reasons: list[str]


@dataclass
class TierPolicy:
    tier: int
    balance_range: str
    check_frequency: str
    behaviors: list[str]
    review_every_days: int = 30   # how often the report is refreshed and trades are prepared
    auto_trades: bool = False     # can the report prepare trades for the customer to confirm


def to_dict(obj) -> dict:
    """Dataclass -> JSON-safe dict (for Claude tool results and Chart.js)."""
    return asdict(obj)
