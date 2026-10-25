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
    followup: object = None                                    # core.followups.FollowUp waiting for an answer
    followups_asked: list[str] = field(default_factory=list)   # follow-up rule keys asked this intake (max 2)
    pending_ask: str | None = None   # a suggested prompt tapped on another tab, sent once by the Chat tab
    robinhood_connected: bool = False  # demo: Robinhood agentic trading UI

    def __post_init__(self):
        if self.fund is None:
            self.fund = FundAccount(self.customer_id)

    @property
    def question(self) -> Question | None:
        return QUESTIONS[self.step] if self.step < len(QUESTIONS) else None

    @property
    def done(self) -> bool:
        return self.step >= len(QUESTIONS)

    @property
    def bank_linked(self) -> bool:
        """Went through the bank link step, or has a fund already running (Alex): either way the bank is in use."""
        return bool(self.linked) or self.done

    def say(self, text: str, role: str = "assistant", link: dict | None = None) -> dict:
        """link: {"href", "label"}, shown under the message (e.g. "How your fund was built")."""
        m = {"role": role, "text": text, **({"link": link} if link else {})}
        self.transcript.append(m)
        return m


_SESSIONS: dict[str, Session] = {}


def get(customer_id: str) -> Session:
    s = _SESSIONS.get(customer_id)
    # setdefault is atomic: two first requests at once share one Session instead of each making its own
    return s if s is not None else _SESSIONS.setdefault(customer_id, Session(customer_id))


def reset(customer_id: str) -> None:
    _SESSIONS.pop(customer_id, None)
