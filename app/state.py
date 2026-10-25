"""Per-persona demo session state (in memory; one demo user at a time is fine)."""
from __future__ import annotations

from dataclasses import dataclass, field

from core.fund import FundAccount
from core.intake import QUESTIONS, Question

DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "AMZN"]
DEFAULT_PREFS = {"weekly_report": True, "trade_confirmations": True, "big_moves": True, "big_move_pct": 3}


@dataclass
class Session:
    customer_id: str
    fund: FundAccount = None                                # set in __post_init__
    step: int = 0                                           # index into QUESTIONS; len(QUESTIONS) = intake done
    messages: list[dict] = field(default_factory=list)     # Claude API message history (chat after intake)
    transcript: list[dict] = field(default_factory=list)   # [{"role": "user"/"assistant", "text": ...}] for UI
    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    prefs: dict = field(default_factory=lambda: dict(DEFAULT_PREFS))   # notification settings
    rundown: dict | None = None                                        # {"text", "at"}: the News tab's AI rundown
    linked: dict | None = None   # the demo bank link (core.onboarding.link): {"bank", "checking", "investing", "note"}

    def __post_init__(self):
        if self.fund is None:
            self.fund = FundAccount(self.customer_id)

    @property
    def question(self) -> Question | None:
        return QUESTIONS[self.step] if self.step < len(QUESTIONS) else None

    @property
    def done(self) -> bool:
        return self.step >= len(QUESTIONS)

    def say(self, text: str, role: str = "assistant") -> dict:
        m = {"role": role, "text": text}
        self.transcript.append(m)
        return m


_SESSIONS: dict[str, Session] = {}


def get(customer_id: str) -> Session:
    if customer_id not in _SESSIONS:
        _SESSIONS[customer_id] = Session(customer_id)
    return _SESSIONS[customer_id]


def reset(customer_id: str) -> None:
    _SESSIONS.pop(customer_id, None)
