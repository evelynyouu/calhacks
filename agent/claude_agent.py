"""Chat: the intake script, then a Claude tool loop, with an offline fallback.  OWNER: Ethan.

The intake questions are fixed (core/intake.py) so each one can show the right input: text box,
slider, scale, multiple choice or chips. Claude takes over once the fund is built. If
ANTHROPIC_API_KEY is missing or the API fails, short Python-written replies run instead, so the
demo never dies on venue wifi.
"""
from __future__ import annotations

import json
import os
import re

from agent.prompts import RUNDOWN_INSTRUCTION, SNAPSHOT_HEADER, SUGGESTED_PROMPTS, SYSTEM_PROMPT
from agent.tools import TOOLS, account_facts, account_snapshot, headlines, market_today, news_tickers, run_tool
from app.state import Session
from core.followups import HIGH_RISK_LEVEL, HORIZON_LABELS
from core import intake, market, slices
from core.explain import fallback_intro, intro_facts, slice_facts
from core.intake import QUESTIONS, goal_label, suggested_horizon
from core.market import ETFS
from core.models import SLICES
from core.report import build_report

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOOL_ROUNDS = 6

_client = None


def _get_client():
    global _client
    if _client is None and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        _client = anthropic.Anthropic()
    return _client


# ---------- Intake ----------

def greeting(name: str) -> str:
    return (f"Hi {name}! I'll build you a personal index fund in {len(QUESTIONS)} quick questions: "
            "a broad base of thousands of companies, plus a slice for the industries you care about. "
            "Then every paycheck invests itself.")


def question_text(session: Session) -> str:
    return session.question.prompt if session.question else ""


def followup_text(fu) -> str:
    """A follow-up's question (core/followups.py). Claude may reword it in one sentence; numbers come only
    from Python, so a rewording that adds or changes a number is dropped. Offline: the Python text."""
    if _get_client() is None:
        return fu.text
    text = explain(fu.facts + [f"Question to ask: {fu.text}"],
                   "Reword this follow-up question in one short, friendly sentence, keeping it a question. "
                   "Keep every number exactly as written. No advice, no judgment. Reply with the question only.",
                   fallback=fu.text)
    nums = lambda t: set(re.findall(r"\d+(?:[.,]\d+)*", t))
    return text if text and "\n" not in text and nums(text) <= nums(fu.text) else fu.text


_HORIZON_PHRASES = {"lt3": "You expect to need this money in under 3 years.",
                    "3to7": "You expect to need this money in 3 to 7 years.",
                    "7to15": "You expect to need this money in 7 to 15 years.",
                    "15plus": "You expect to need this money in 15 years or more.",
                    "unsure": "You're not sure yet when you'll need this money."}


def _followup_facts(answers: dict, level: int) -> list[str]:
    """What the intake follow-ups changed or learned, in plain words (Python-written)."""
    facts = list(answers.get("followup_notes", []))
    h = answers.get("horizon")
    if h in HORIZON_LABELS:
        facts.append(_HORIZON_PHRASES.get(h, f"You expect to need this money in: {HORIZON_LABELS[h].lower()}."))
        if h == "lt3" and level >= HIGH_RISK_LEVEL:
            facts.append("A growth-leaning fund can be down in any single year, so money needed within 3 years is "
                         "often kept in steadier funds. It's your call; ask me about it any time.")
    sugg = suggested_horizon(answers)
    if sugg:   # "Not sure yet": mention what the goal usually means, as a suggestion only
        facts.append(f"They aren't sure when they'll need the money. Their goal often means "
                     f"{HORIZON_LABELS[sugg[0]].lower()}, but no timeline limit was applied; they can set one any time.")
    return facts


def fund_facts(session: Session) -> list[str]:
    """The fund in Python-computed facts. When a limit or the customer's own choice moved the mix away
    from what the answers scored, both numbers and the reason are included, so Claude can't misstate why."""
    a, p = session.fund, session.fund.portfolio
    if p.score is None or p.score == a.risk_score:
        score = [f"Risk score {a.risk_score} out of 100, so the fund is {p.risk_label} (level {p.risk_level} of 4)."]
    else:
        score = [f"The customer's answers give a risk score of {a.risk_score} out of 100.",
                 f"The fund was built at a risk score of {p.score} out of 100, so it is {p.risk_label} "
                 f"(level {p.risk_level} of 4), for these reasons:",
                 *(r.text for r in p.reasons if r.kind in ("contradiction", "horizon", "drop", "override")
                   and (r.kind in ("contradiction", "override") or r.facts.get("lowered")))]
    return [*score, *slice_facts(p),
            f"Yearly fee {p.annual_fee_pct:.2f}%.",
            f"{a.invest_pct:g}% of each paycheck will be invested as soon as it lands.",
            *p.overlap_warnings]


def fund_ready(session: Session) -> str:
    """The first message after the fund is built: Claude explains it, or a Python summary offline."""
    a, p = session.fund, session.fund.portfolio
    facts = fund_facts(session)
    unmatched = a.answers.get("interests", {}).get("unmatched", [])
    notes = list(p.overlap_warnings)
    if unmatched:
        notes.append("No fund matches these yet, so they were noted but not added: " + ", ".join(unmatched) + ".")
        facts.append(notes[-1])
    facts += _followup_facts(a.answers, p.risk_level)
    offline = (f"Your fund is ready: {p.risk_label.lower()}. " + " ".join(slice_facts(p))
               + f" The yearly fee is {p.annual_fee_pct:.2f}%. " + " ".join(notes) + " Press “Paycheck arrives” to watch a paycheck invest itself, or ask me anything.")
    return explain(facts, f"The customer's goal: {goal_label(a.answers)!r}. In 3 to 4 short sentences, explain "
                          "their new fund: what their own US index is, what the personal slices (where they spend, "
                          "their interests) add, and one thing to know about their risk level. Say the spending "
                          "picks personalize the fund and aren't predictions. End by inviting questions.",
                   fallback=offline)


def fund_intro(session: Session) -> str:
    """At most 2-3 sentences on top of "How your fund was built", tied to the goal. Python facts only."""
    a = session.fund
    return explain(intro_facts(a), "In 2 to 3 short sentences, tie this fund to the customer's goal. Don't list "
                   "numbers; the page below shows them.", fallback=fallback_intro(a))


# ---------- Chat after the intake ----------

def chat_turn(session: Session, user_text: str) -> tuple[str, set[str]]:
    """One user message -> (assistant reply, UI panels to refresh)."""
    client = _get_client()
    if client is None:
        return offline_turn(session, user_text)
    try:
        return _claude_turn(client, session, user_text)
    except Exception as e:  # noqa: BLE001  demo must not crash
        print(f"[claude] falling back to offline replies: {e!r}")
        text, events = offline_turn(session, user_text)
        return text, events | set(getattr(e, "events", ()))


CHAT_MAX_TOKENS = 4000   # thinking counts toward this; 700 could be used up before any text was written


def system_blocks(session: Session) -> list[dict]:
    """The system prompt as two blocks. The first never changes, so it (and the tool list before it) is cached;
    the account snapshot changes every turn and comes after the cache breakpoint."""
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": SNAPSHOT_HEADER + account_snapshot(session)}]


def _claude_turn(client, session: Session, user_text: str) -> tuple[str, set[str]]:
    """The tool loop. On any failure the turn's messages are taken back out, so a half-finished turn (a tool
    call without its result) never poisons the next request; the caller then answers offline."""
    start = len(session.messages)
    session.messages.append({"role": "user", "content": user_text})
    events: set[str] = set()
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.messages.create(model=MODEL, max_tokens=CHAT_MAX_TOKENS, system=system_blocks(session),
                                          tools=TOOLS, messages=session.messages)
            session.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                if not text:   # a refusal, or the token cap hit before any text: answer offline instead
                    raise RuntimeError(f"no text (stop_reason={resp.stop_reason})")
                return text, events
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out, ev = run_tool(session, b.name, b.input)
                    events.update(ev)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out, default=str)})
            session.messages.append({"role": "user", "content": results})
    except Exception as e:
        del session.messages[start:]
        e.events = events   # tools that already ran still changed the fund: the caller refreshes those panels
        raise
    del session.messages[start:]
    err = RuntimeError("too many tool rounds")
    err.events = events
    raise err


def suggested_prompts(session: Session) -> list[str]:
    rep = build_report(session.fund) if session.fund.portfolio else None
    top = rep["companies"][0]["name"] if rep and rep["companies"] else "the S&P 500"
    return [p.format(top=top) for p in SUGGESTED_PROMPTS]


def _moves(session: Session) -> list[str]:
    """Today's moves as sentences, computed in Python."""
    m = market_today(session)
    lines = ["Today: " + ", ".join(f"{ETFS[q['symbol']]['label']} {q['change_pct']:+.2f}%" for q in m["indexes"]) + "."]
    held = sorted(m["holdings"], key=lambda q: -abs(q["change_pct"]))
    if held:
        lines.append("Your biggest movers: " + ", ".join(f"{q['symbol']} {q['change_pct']:+.2f}%" for q in held[:3]) + ".")
    if any(q["source"] == "simulated" for q in m["indexes"] + m["holdings"]):
        lines.append("(Some prices are simulated.)")
    return lines


def rundown(session: Session) -> str:
    """The News tab's AI rundown: Claude writes it from today's moves and headlines; offline, Python does."""
    news = headlines(news_tickers(session), limit=10)
    facts = _moves(session) + [f"Headline ({', '.join(n['tickers']) or 'market'}): {n['title']}" for n in news]
    offline = "\n\n".join(["The market\n" + _moves(session)[0],
                             "Your fund\n" + " ".join(_moves(session)[1:] or ["Your fund has no holdings yet."]),
                             "Worth watching\n" + ("; ".join(n["title"] for n in news[:3]) + "."
                                                    if news else "Headlines aren't reachable right now.")])
    return explain(facts, RUNDOWN_INSTRUCTION, fallback=offline)


# ---------- Offline replies (no API key, or the API failed) ----------
# Python-written answers to the same questions the snapshot covers. Questions never change anything: turning
# auto-trade on or off needs one of the exact command phrases below, typed as a request, not a question.

_CMD_LEAD = r"^\s*(?:ok(?:ay)?[,!.]?\s+|yes[,!.]?\s+|please\s+)*(?:please\s+)?"
_COMMANDS = [
    ("auto_on", re.compile(_CMD_LEAD + r"turn\s+(?:on\s+auto[- ]?trade|auto[- ]?trade\s+on)(?:\s+please)?\s*[.!]*\s*$")),
    ("auto_off", re.compile(_CMD_LEAD + r"turn\s+(?:off\s+auto[- ]?trade|auto[- ]?trade\s+off)(?:\s+please)?\s*[.!]*\s*$")),
]
COMMAND_PHRASES = {"auto_on": "turn on auto-trade", "auto_off": "turn off auto-trade"}


def mode_command(text: str) -> str | None:
    """'auto_on' / 'auto_off' when the whole message is one of the command phrases, else None.
    A question ("how does auto-trade work?") is never a command."""
    t = text.lower().strip()
    if "?" in t:
        return None
    return next((name for name, rx in _COMMANDS if rx.match(t)), None)


def _has(t: str, *patterns: str) -> bool:
    return any(re.search(p, t) for p in patterns)


def _held_ticker(session: Session, text: str) -> str | None:
    """A holding named in the message, by ticker (any case, whole word) or by its label ("bonds")."""
    p = session.fund.portfolio
    words = set(re.findall(r"[a-z]+", text.lower()))
    for h in p.holdings:
        if h.ticker.lower() in words:
            return h.ticker
    t = text.lower()
    for h in p.holdings:
        label = market.info(h.ticker)["label"].lower()
        if len(label) > 3 and label in t:
            return h.ticker
    return None


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _run_command(session: Session, cmd: str) -> tuple[str, set[str]]:
    from core import fund as _fund
    from core.fund import AUTO_EXECUTE_TIER
    from core.policy import tier_for
    a = session.fund
    tier = tier_for(_fund.value(a))
    if cmd == "auto_on" and not tier.auto_trades:
        return (f"Automatic trade prep unlocks at a higher balance; you're Tier {tier.tier} ({tier.balance_range}). "
                "Nothing changed."), set()
    a.auto_trade = cmd == "auto_on"
    if not a.auto_trade:
        return "Auto-trade is off. Reviews won't line up trades; the report still shows recommendations.", {"account"}
    if tier.tier >= AUTO_EXECUTE_TIER:
        return (f"Auto-trade is on. You're Tier {tier.tier}, so trades from each {tier.check_frequency.lower()} "
                "review run without a Confirm click."), {"account"}
    return (f"Auto-trade is on. You're Tier {tier.tier}: each {tier.check_frequency.lower()} review lines up trades, "
            "and nothing trades until you press Confirm."), {"account"}


def offline_turn(session: Session, text: str) -> tuple[str, set[str]]:
    """One chat message answered without Claude -> (reply, UI panels to refresh). Every number comes from
    account_facts or the report, both computed in Python."""
    cmd = mode_command(text)
    if cmd:
        return _run_command(session, cmd)
    return _offline_answer(session, text), set()


def _offline_answer(session: Session, text: str) -> str:
    from core import spending as _spending
    from core.profile import get_profile as _get_profile
    a = session.fund
    if not a.portfolio:
        return "Your fund isn't built yet. Finish the questions and I'll explain everything about it."
    t = text.lower()
    f = account_facts(session)
    fd, pc, rv = f["fund"], f["paycheck"], f["review"]

    # Why is a holding there? ("why do I own TGT?", "why bonds?"): the builder's own reasons, with its slice
    tick = _held_ticker(session, text)
    if tick and _has(t, r"\bwhy\b", r"\bwhat'?s .* for\b", r"\breason"):
        p = a.portfolio
        named = next(h for h in p.holdings if h.ticker == tick)
        why = " ".join(r.text for r in p.reasons if r.ticker == tick and r.kind not in ("cap", "dropped"))
        return (f"{tick} is in the “{dict(SLICES)[p.slice_of(named)]}” part of your fund, {named.weight:.1%} of it. "
                f"{why}").strip()

    # Questions about auto-trade: explain, change nothing
    if _has(t, r"\bauto[- ]?trad", r"\bauto[- ]?execut", r"\bautomatic trad"):
        state = "on" if rv["auto_trade"] else "off"
        return (f"Auto-trade is {state}. You're Tier {rv['tier']} ({rv['tier_range']}); "
                + ("reviews run trades without a Confirm click." if rv["auto_executes"] else
                   "reviews line up trades and nothing trades until you press Confirm." if rv["auto_prep_unlocked"] else
                   "automatic trade prep unlocks at a higher balance.")
                + f" Type “{COMMAND_PHRASES['auto_on']}” or “{COMMAND_PHRASES['auto_off']}” to change it.")
    if _has(t, r"spending (insight|habit|pattern)", r"\bwhat do i spend\b", r"\bwhere does my money go\b"):
        try:
            b = _spending.spending_breakdown(a.customer_id, _get_profile(a.customer_id))
        except Exception:  # noqa: BLE001
            return "Spending insights aren't available right now. The Money tab shows your bank activity."
        cats = ", ".join(f"{k} ${v:,.0f}" for k, v in list(b["categories"].items())[:3])
        themes = "; ".join(s["why"] for s in b["theme_suggestions"][:3])
        return f"Your top spending: {cats}." + (f" Themes that match: {themes}." if themes else "")

    if _has(t, r"paycheck", r"payday", r"percent of (each|my)", r"how much .*invest"):
        last = pc["recent"][0] if pc["recent"] else None
        out = (f"Your last paycheck, {last['on']}, was {_money(last['paycheck'])}; {_money(last['invested'])} "
               f"({last['pct']:g}%) went into your fund right away. " if last else "No paycheck has landed since your "
               "fund started. ")
        return out + (f"You invest {pc['invest_pct']:g}% of each {_money(pc['amount'])} paycheck. The next one lands "
                      f"{pc['next_on']} and invests {_money(pc['next_invests'])}. Change the percent on the Portfolio tab.")
    if _has(t, r"\breview", r"\brebalanc", r"\btier\b", r"\bnext report\b"):
        nxt = rv["next"] or ("with your next paycheck" if rv["auto_trade"] and rv["auto_prep_unlocked"] else
                             "not scheduled, since automatic trade prep is off")
        return (f"You're Tier {rv['tier']} ({rv['tier_range']}): reviews {rv['frequency'].lower()}, every "
                f"{rv['every_days']} days. Last review: {rv['last'] or 'none yet'}. Next review: {nxt}. Trades waiting "
                f"for your Confirm: {rv['pending_trades']}. The report has today's recommendations any time.")
    if _has(t, r"\bbank\b", r"\bchecking\b", r"\bsavings\b", r"transaction", r"\bactivity\b", r"\btransfers?\b",
            r"\bspent\b", r"\bdeposits?\b"):
        rows = f["bank_activity"][:5]
        b = f["bank"]
        lines = "; ".join(f"{x['on']} {x['text']} {'+' if x['flow'] == 'in' else '−' if x['flow'] == 'out' else ''}"
                          f"{_money(x['amount'])}" for x in rows)
        return (f"Checking {_money(b['checking'])}, savings {_money(b['savings'])} (mock bank). "
                + (f"Latest activity: {lines}." if rows else "No bank activity yet.") + " The Money tab has the rest.")
    if _has(t, r"\bgoal\b", r"\bhorizon\b", r"when .*need", r"saving for", r"investing for", r"\btimeline\b",
            r"\bhow long\b"):
        return ((f"Your goal: “{f['goal']}”. " if f["goal"] else "You didn't name a goal. ")
                + (f"You expect to need the money in: {f['horizon'].lower()}. " if f["horizon"] else "")
                + f"That's why your fund is {fd['risk_label'].lower()} (level {fd['risk_level']} of "
                f"{fd['risk_level_max']}).")
    if _has(t, r"\bworth\b", r"\bvalue\b", r"\bbalance\b", r"how much (do i have|money|is (it|my))", r"\bgain",
            r"\bmade\b", r"\bup or down\b"):
        top = ", ".join(f"{h['ticker']} {_money(h['dollars'])}" for h in fd["holdings"][:3])
        return (f"Your fund is worth {_money(fd['value'])}. You've put in {_money(fd['put_in'])}, so you're "
                f"{'up' if fd['gain'] >= 0 else 'down'} {_money(abs(fd['gain']))}. Biggest holdings: {top}. "
                f"({f['prices']}.)")
    if _has(t, r"\btoday\b", r"\bmoved\b", r"\bmove\b"):
        return " ".join(_moves(session))
    if _has(t, r"\bnews\b", r"\brundown\b", r"going on"):
        news = headlines(news_tickers(session), limit=4)
        if not news:
            return "I can't reach the news feed right now. " + " ".join(_moves(session))
        return "Latest headlines for your holdings: " + " · ".join(n["title"] for n in news) + ". The News tab has the links."
    rep = build_report(a)
    if _has(t, r"recommend", r"should i", r"what now", r"what should", r"\badvice\b", r"\bchange anything\b"):
        recs = rep["recommendations"][:2]
        return ("Here's what your report suggests: " + " ".join(f"{r['title']}. {r['facts'][0]}" for r in recs)
                if recs else "Your report has no recommendations right now: your fund is close to its targets.")
    if _has(t, r"\breport\b", r"how am i", r"how is", r"\bdoing\b", r"perform", r"\breturns?\b", r"\bmarket\b"):
        return " ".join(rep["facts"][:3]) + " The full report has every holding and the market picture."
    if _has(t, r"\bhold", r"\bown\b", r"\bin my fund\b", r"\bfund\b", r"\bexplain\b", r"\bwhy\b", r"\bportfolio\b"):
        top = ""
        if not _has(t, r"\bwhy\b", r"\bexplain\b"):   # "what do I hold?": the biggest positions too, never all sixty
            top = " Biggest positions: " + ", ".join(f"{h['ticker']} {h['pct']:.1f}%" for h in fd["holdings"][:6]) + "."
        return (f"Your fund is {fd['risk_label'].lower()}: {slices.describe(a.portfolio)}.{top} Ask “why do I own” and "
                "a ticker for the reason behind any holding.")
    return ("I'm answering offline right now (no Claude connection), so I can tell you: what your fund is worth, "
            "what it holds and why, your last paycheck, your bank activity, your next review, your goal and timeline, "
            "today's moves, news, and your report's recommendations.")


# ---------- Explanations ----------

EXPLAIN_MAX_TOKENS = 1500   # the model may think before it writes; a long fact list used all of 400 and left no text


def explain(facts: list[str], instruction: str, fallback: str | None = None) -> str:
    """Turn Python-computed facts into a short explanation. Falls back to `fallback` (or the facts)."""
    offline = fallback if fallback is not None else " ".join(facts)
    client = _get_client()
    if client is None:
        return offline
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=EXPLAIN_MAX_TOKENS,
            system=SYSTEM_PROMPT + "\nUse only these facts; add no new numbers.",
            messages=[{"role": "user", "content": instruction + "\n\nFacts:\n- " + "\n- ".join(facts)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:   # ran out of tokens before writing, or no text block: show the offline version, never a blank
            print(f"[claude] explain returned no text (stop_reason={resp.stop_reason}); using the offline version")
        return text or offline
    except Exception as e:  # noqa: BLE001
        print(f"[claude] explain fallback: {e!r}")
        return offline
