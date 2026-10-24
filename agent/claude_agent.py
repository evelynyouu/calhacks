"""Claude tool-use loop + offline fallback.  OWNER: Ethan.

If ANTHROPIC_API_KEY is missing or the API fails, a scripted intake runs
instead, so the demo never dies on venue wifi.
"""
from __future__ import annotations

import json
import os
import re

from agent.prompts import SYSTEM_PROMPT
from agent.tools import TOOLS, run_tool
from app.state import Session

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOOL_ROUNDS = 6

_client = None


def _get_client():
    global _client
    if _client is None and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def chat_turn(session: Session, user_text: str) -> tuple[str, set[str]]:
    """One user message -> (assistant reply, UI sections to refresh)."""
    client = _get_client()
    if client is None:
        return _scripted_turn(session, user_text)
    try:
        return _claude_turn(client, session, user_text)
    except Exception as e:  # noqa: BLE001  demo must not crash
        print(f"[claude] falling back to script: {e!r}")
        return _scripted_turn(session, user_text)


def _claude_turn(client, session: Session, user_text: str) -> tuple[str, set[str]]:
    session.messages.append({"role": "user", "content": user_text})
    events: set[str] = set()
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=MODEL, max_tokens=600, system=SYSTEM_PROMPT, tools=TOOLS, messages=session.messages,
        )
        session.messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return text, events
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out, ev = run_tool(session, b.name, b.input)
                events.update(ev)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)})
        session.messages.append({"role": "user", "content": results})
    return "Sorry, that took too many steps. Try again?", events


def explain(facts: list[str], instruction: str) -> str:
    """Turn Python-computed facts into a short explanation. Falls back to the facts."""
    client = _get_client()
    if client is None:
        return " ".join(facts)
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=300,
            system=SYSTEM_PROMPT + "\nUse only these facts; add no new numbers.",
            messages=[{"role": "user", "content": instruction + "\n\nFacts:\n- " + "\n- ".join(facts)}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:  # noqa: BLE001
        print(f"[claude] explain fallback: {e!r}")
        return " ".join(facts)


# ---------- Offline scripted intake ----------

_QUESTIONS = [
    ("goal", "What are you investing for? A house, a safety net, retirement, or just growing your money?"),
    ("timeline_years", "When might you need this money? A rough number of years is fine."),
    ("age", "How old are you?"),
    ("interests", "What are you into? Tech, healthcare, clean energy, or none?"),
    ("loss_reaction", "If your fund dropped 30% in a month, would you sell, wait it out, or buy more?"),
]


def _scripted_turn(session: Session, user_text: str) -> tuple[str, set[str]]:
    events: set[str] = set()
    step = session.scripted_step
    if step == 0:
        r, ev = run_tool(session, "check_readiness", {})
        events.update(ev)
        session.scripted_step = 1
        if r["status"] == "not_yet":
            return r["headline"] + ". " + " ".join(r["reasons"]), events
        return f"{r['headline']}. Let's build your fund. {_QUESTIONS[0][1]}", events

    key = _QUESTIONS[step - 1][0]
    session.intake[key] = _parse(key, user_text)
    if step < len(_QUESTIONS):
        session.scripted_step += 1
        return _QUESTIONS[step][1], events

    session.scripted_step += 1
    fund, ev1 = run_tool(session, "build_fund", {**{"goal": "", "interests": []}, **session.intake})
    proj, ev2 = run_tool(session, "run_projection", {})
    events.update(ev1 + ev2)
    if "error" in fund:
        return fund["error"], events
    y10 = proj["year_10"]
    return (f"Here's your {fund['risk_label'].lower()} fund. In 10 years it typically lands near "
            f"${y10['typical']:,.0f} (range ${y10['low']:,.0f}–${y10['high']:,.0f})."), events


def _parse(key: str, text: str):
    t = text.lower()
    if key in ("timeline_years", "age"):
        m = re.search(r"\d+", t)
        return int(m.group()) if m else (10 if key == "timeline_years" else 22)
    if key == "interests":
        return [k for k, words in {"tech": ["tech"], "healthcare": ["health"],
                                   "clean_energy": ["clean", "energy", "climate"]}.items()
                if any(w in t for w in words)]
    if key == "loss_reaction":
        return "sell" if "sell" in t else "buy_more" if "buy" in t else "wait"
    return text
