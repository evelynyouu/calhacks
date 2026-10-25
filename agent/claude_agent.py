"""Chat: the intake script, then a Claude tool loop, with an offline fallback.  OWNER: Ethan.

The intake questions are fixed (core/intake.py) so each one can show the right input: text box,
slider, scale, multiple choice or chips. Claude takes over once the fund is built. If
ANTHROPIC_API_KEY is missing or the API fails, short Python-written replies run instead, so the
demo never dies on venue wifi.
"""
from __future__ import annotations

import json
import os

from agent.prompts import RUNDOWN_INSTRUCTION, SUGGESTED_PROMPTS, SYSTEM_PROMPT
from agent.tools import TOOLS, headlines, market_today, news_tickers, run_tool
from app.state import Session
from core.intake import QUESTIONS
from core.market import ETFS
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


def fund_ready(session: Session) -> str:
    """The first message after the fund is built: Claude explains it, or a Python summary offline."""
    a, p = session.fund, session.fund.portfolio
    core = ", ".join(f"{h.ticker} {h.weight:.0%}" for h in p.holdings if h.role == "core")
    tilts = ", ".join(f"{h.ticker} {h.weight:.1%}" for h in p.holdings if h.role == "tilt")
    facts = [
        f"Risk score {a.risk_score} out of 100, so the fund is {p.risk_label} (level {p.risk_level} of 4).",
        f"Core: {core}.",
        f"Interest slice: {tilts}." if tilts else "No interest slice: the fund stays broad.",
        f"Yearly fee {p.annual_fee_pct:.2f}%.",
        f"{a.invest_pct:g}% of each paycheck will be invested as soon as it lands.",
        *p.overlap_warnings,
    ]
    unmatched = a.answers.get("interests", {}).get("unmatched", [])
    if unmatched:
        facts.append("No fund matches these yet, so they were noted but not added: " + ", ".join(unmatched) + ".")
    offline = (f"Your fund is ready: {p.risk_label.lower()}, with {core}"
               + (f", plus {tilts} for your interests" if tilts else "") + f". The yearly fee is {p.annual_fee_pct:.2f}%. "
               + " ".join(facts[5:]) + ' Press "Paycheck arrives" to watch a paycheck invest itself, or ask me anything.')
    return explain(facts, f"The customer's goal: {a.answers.get('goal', '')!r}. In 3 to 4 short sentences, explain "
                          "their new fund: what the core does, what the interest slice adds, and one thing to "
                          "know about their risk level. End by inviting questions.", fallback=offline)


# ---------- Chat after the intake ----------

def chat_turn(session: Session, user_text: str) -> tuple[str, set[str]]:
    """One user message -> (assistant reply, UI panels to refresh)."""
    client = _get_client()
    if client is None:
        return _offline_turn(session, user_text), set()
    try:
        return _claude_turn(client, session, user_text)
    except Exception as e:  # noqa: BLE001  demo must not crash
        print(f"[claude] falling back to offline replies: {e!r}")
        return _offline_turn(session, user_text), set()


def _session_context(session: Session) -> str:
    """What already happened in the app, so chat can answer questions about it (app-computed, safe to cite)."""
    a = session.fund
    lines = [f"Today in the demo is {a.today.isoformat()}."]
    if a.answers.get("goal"):
        lines.append(f"Their goal: {a.answers['goal']}")
    lines += a.log[:6]
    if a.pending:
        lines.append(f"{len(a.pending)} trades are waiting for the customer to confirm.")
    return "\n\nWhat already happened in the app (app-computed, safe to cite):\n- " + "\n- ".join(lines)


def _claude_turn(client, session: Session, user_text: str) -> tuple[str, set[str]]:
    session.messages.append({"role": "user", "content": user_text})
    events: set[str] = set()
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=MODEL, max_tokens=700, system=SYSTEM_PROMPT + _session_context(session), tools=TOOLS,
            messages=session.messages,
        )
        session.messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip(), events
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out, ev = run_tool(session, b.name, b.input)
                events.update(ev)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)})
        session.messages.append({"role": "user", "content": results})
    return "Sorry, that took too many steps. Try asking again?", events


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


def _offline_turn(session: Session, text: str) -> str:
    from core import fund as _fund, intake as _intake, spending as _spending
    from core.portfolio import custom_fund_preview as _preview, THEMES as _THEMES
    from core.profile import get_profile as _get_profile
    t = text.lower()
    rep = build_report(session.fund)

    # --- custom fund commands (check before generic "fund"/"hold" keywords) ---
    _is_custom_req = any(w in t for w in ("custom", "individual stock", "real stock", "actual stock",
                                           "switch to custom", "use stock", "stock instead"))
    _is_etf_req = any(w in t for w in ("etf fund", "switch back", "back to etf", "use etf"))
    _is_preview = any(w in t for w in ("preview", "what would", "show me what", "what would it look"))
    _is_spending = any(w in t for w in ("spending", "insight", "habit", "purchase", "i buy", "i spend"))
    _is_auto = any(w in t for w in ("auto trade", "auto-trade", "auto execute", "automat"))

    if _is_etf_req:
        session.fund.answers.pop("custom_mode", None)
        session.fund.answers.pop("spending_stocks", None)
        session.fund.answers["etf_mode"] = True
        _fund.build(session.fund)
        p = session.fund.portfolio
        tilts = ", ".join(f"{h.ticker} ({h.weight:.1%})" for h in p.holdings if h.role == "tilt")
        return (f"Switched to ETF mode. Your {p.risk_label.lower()} fund now holds "
                + (f"sector ETFs: {tilts}." if tilts else "a broad core with no sector tilts."))

    if _is_custom_req or _is_preview:
        ints = _intake.interests(session.fund.answers)
        level = session.fund.risk_score or 50
        from core.intake import risk_level as _rl
        lvl = _rl(level) if session.fund.risk_score is not None else 2
        # detect spending tickers mentioned in the message
        _spend_tickers: list[str] = []
        try:
            profile = _get_profile(session.fund.customer_id)
            sugg = _spending.spending_stock_suggestions(session.fund.customer_id, profile)
            for s in sugg:
                if s["ticker"].lower() in t or s["name"].lower() in t:
                    _spend_tickers.append(s["ticker"])
        except Exception:
            pass
        if _is_preview:
            rows = _preview(lvl, ints, _spend_tickers or None)
            if not rows:
                return ("A custom fund replaces your sector ETFs with the top 3 companies from each ETF. "
                        "Add some interests first, then ask to switch.")
            sample = ", ".join(f"{r['ticker']} ({r['weight_pct']}% via {r['from_interest']})" for r in rows[:6])
            return (f"Here's a preview of your custom fund — {len(rows)} individual stocks instead of sector ETFs: "
                    f"{sample}{'...' if len(rows) > 6 else ''}. "
                    'Say "switch to custom fund" to apply it.')
        # apply custom mode
        session.fund.answers["custom_mode"] = True
        session.fund.answers["spending_stocks"] = _spend_tickers
        _fund.build(session.fund)
        p = session.fund.portfolio
        stocks = [h for h in p.holdings if h.role == "stock"]
        sample = ", ".join(f"{h.ticker} ({h.weight:.1%})" for h in stocks[:6])
        n = len(stocks)
        return (f"Done! Your fund now holds {n} individual stock{'s' if n != 1 else ''} instead of sector ETFs: "
                f"{sample}{'...' if n > 6 else ''}. "
                "Your core (VTI/VXUS/BND) is unchanged. Check the Fund tab to see the full breakdown.")

    if _is_spending:
        try:
            profile = _get_profile(session.fund.customer_id)
            breakdown = _spending.spending_breakdown(session.fund.customer_id, profile)
            sugg = breakdown["theme_suggestions"]
            cats = list(breakdown["categories"].items())[:3]
            cat_str = ", ".join(f"{k} ${v:,.0f}" for k, v in cats)
            sugg_str = "; ".join(f"{s['why']}" for s in sugg[:3])
            surplus = breakdown["spending_surplus_estimate"]
            out = f"Your top spending: {cat_str}. " + (f"Themes that match: {sugg_str}. " if sugg_str else "")
            if surplus > 0:
                out += f"You spent ${surplus:,.0f} below your baseline this month — that could be invested."
            return out
        except Exception as e:
            return f"Couldn't load spending data right now ({e})."

    if _is_auto:
        from core.fund import AUTO_EXECUTE_TIER
        from core.policy import tier_for
        import core.fund as _cf
        enabled = "on" in t or "enabl" in t or "turn on" in t
        disabled = "off" in t or "disabl" in t or "turn off" in t
        if enabled or disabled:
            session.fund.auto_trade = enabled and not disabled
            bal = _cf.value(session.fund)
            tier = tier_for(bal)
            auto_exec = session.fund.auto_trade and tier.tier >= AUTO_EXECUTE_TIER
            return (f"Auto-trade is now {'on' if session.fund.auto_trade else 'off'}. "
                    + (f"You're Tier {tier.tier} — trades will execute automatically on each {tier.check_frequency.lower()} review."
                       if auto_exec else
                       f"You're Tier {tier.tier}; trades will queue for your confirmation "
                       f"(auto-execute starts at Tier {AUTO_EXECUTE_TIER}, $50K+ AUM)."))
        return (f"Auto-trade is currently {'on' if session.fund.auto_trade else 'off'}. "
                'Say "turn auto-trade on" or "turn auto-trade off" to change it.')

    # --- original offline handlers ---
    if any(w in t for w in ("today", "moved", "move ")):
        return " ".join(_moves(session))
    if "news" in t or "rundown" in t or "going on" in t:
        news = headlines(news_tickers(session), limit=4)
        if not news:
            return "I can't reach the news feed right now. " + " ".join(_moves(session))
        return "Latest headlines for your holdings: " + " · ".join(n["title"] for n in news) + ". The News tab has the links."
    if any(w in t for w in ("recommend", "should i", "what now", "what should", "advice", "next")):
        return "Here's what your report suggests: " + " ".join(
            f"{r['title']}. {r['facts'][0]}" for r in rep["recommendations"][:2])
    if any(w in t for w in ("report", "how am i", "how is", "doing", "perform", "return", "market")):
        return " ".join(rep["facts"][:3]) + " The full report has every holding and the market picture."
    if any(w in t for w in ("percent", "%", "paycheck", "how much")):
        return (f"You're investing {session.fund.invest_pct:g}% of each paycheck. "
                "Drag the slider in the Paycheck autopilot panel to change it.")
    if any(w in t for w in ("why", "fund", "hold", "own", "explain")):
        p = session.fund.portfolio
        mode = " (custom — individual stocks)" if session.fund.answers.get("custom_mode") else ""
        return f"Your fund is {p.risk_label.lower()}{mode}: " + " ".join(
            f"{h.ticker} ({h.weight:.0%})." for h in p.holdings[:6])
    return ("I'm in offline mode right now, so I can answer about your fund, report, recommendations, "
            "paycheck percent, spending insights, or switch to a custom stock fund. "
            'Try "show me a custom fund" or "how am I doing?".')


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
