"""Demo state for live mode: the sim clock and paycheck overrides, per persona.  OWNER: Evelyn.

Lives in data/state.json (gitignored). Nothing reads date.today(): "today" for a persona is
its sim clock, which starts at SIM_START and only moves when the demo fast-forwards.
"""
from __future__ import annotations

import calendar
import json
import os
from datetime import date, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state.json"
SIM_START = date(2026, 10, 23)   # the demo's "today" before any fast-forward


def load() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}


def _save(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, STATE_PATH)


def get(persona: str) -> dict:
    return load().get(persona, {})


def update(persona: str, **fields) -> None:
    data = load()
    data.setdefault(persona, {}).update(fields)
    _save(data)


def clear(persona: str | None = None) -> None:
    data = load()
    if persona is None:
        data = {}
    else:
        data.pop(persona, None)
    _save(data)


def as_of(persona: str) -> date:
    stored = get(persona).get("as_of")
    return date.fromisoformat(stored) if stored else SIM_START


def paycheck_override(persona: str) -> float | None:
    return get(persona).get("paycheck_override")


def next_due(day: int, on_or_after: date) -> date:
    """Next date on/after `on_or_after` falling on day-of-month `day` (clamped to the month's length)."""
    year, month = on_or_after.year, on_or_after.month
    while True:
        candidate = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if candidate >= on_or_after:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def occurrences(day: int, start: date, end: date) -> list[date]:
    """Every due date for day-of-month `day` in [start, end)."""
    out, d = [], next_due(day, start)
    while d < end:
        out.append(d)
        d = next_due(day, d + timedelta(days=1))
    return out
