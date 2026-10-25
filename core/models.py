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

HoldingRole = Literal["core", "tilt", "stock"]

# What a reason explains. Holdings: core, bonds, tilt, stock. Adjustments: cap (shrunk to fit), swing (sized
# down because it moves a lot), dropped (left out). Notes: overlap, concentration, sector. How the mix was
# chosen: score, contradiction, horizon, drop, override.
ReasonKind = Literal["core", "bonds", "tilt", "stock", "cap", "swing", "dropped", "overlap", "concentration",
                     "sector", "score", "contradiction", "horizon", "drop", "override"]


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
    role: HoldingRole         # core: VTI/VXUS/BND; tilt: one fund per interest; stock: a single company
    expense_ratio: float      # percent, e.g. 0.15 means 0.15%


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

    @property
    def core(self) -> Holding:
        return next(h for h in self.holdings if h.role == "core")

    @property
    def tilt_share(self) -> float:
        return sum(h.weight for h in self.holdings if h.role == "tilt")

    def annual_fee_dollars(self, balance: float) -> float:
        return balance * self.annual_fee_pct / 100


WARNING_KINDS = ("dropped", "overlap", "concentration", "sector", "contradiction", "drop")


@dataclass
class SimResult:
    years: list[int]          # 0..10
    p10: list[float]          # dollars, one value per year
    p50: list[float]
    p90: list[float]
    monthly_contribution: float
    starting_balance: float
    total_contributed: float


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
