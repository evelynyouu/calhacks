"""Per-persona demo session state (in memory; one demo user at a time is fine)."""
from __future__ import annotations

from dataclasses import dataclass, field

from core.models import CrashTestResult, PaydayResult, Portfolio


@dataclass
class Session:
    customer_id: str
    messages: list[dict] = field(default_factory=list)     # Claude API message history
    transcript: list[dict] = field(default_factory=list)   # [{"role": "user"/"assistant", "text": ...}] for UI
    intake: dict = field(default_factory=dict)
    portfolio: Portfolio | None = None
    crash: CrashTestResult | None = None
    last_payday: PaydayResult | None = None
    last_payday_explanation: str = ""
    events: list[str] = field(default_factory=list)        # life-event log for the UI
    scripted_step: int = 0                                  # offline fallback progress


_SESSIONS: dict[str, Session] = {}


def get(customer_id: str) -> Session:
    if customer_id not in _SESSIONS:
        _SESSIONS[customer_id] = Session(customer_id)
    return _SESSIONS[customer_id]


def reset(customer_id: str) -> None:
    _SESSIONS.pop(customer_id, None)
