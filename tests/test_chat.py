"""Chat: the account snapshot Claude sees, prompt caching, the tool loop's failure path, offline replies, mode
commands that never fire on a question, and the duplicate-message regressions. Never calls the real Claude API."""
from __future__ import annotations

import re
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from agent.prompts import SNAPSHOT_HEADER, SYSTEM_PROMPT
from agent.tools import SNAPSHOT_MAX_CHARS, TOOLS, account_facts, account_snapshot, run_tool
from app import state
from app.main import app
from app.routes import pages
from core import fund, nessie
from core.broker import values
from tests.fund_helpers import make_fund

client = TestClient(app)


# ---------- a fake Anthropic client ----------

def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(name, args, id_="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


class FakeClient:
    """Records every messages.create call and plays back scripted responses (or raises)."""

    def __init__(self, *responses):
        self.calls, self._responses = [], list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def _alex_with_history():
    """Alex's preset fund plus two paychecks, so the snapshot has paychecks, transfers and a review."""
    s = pages._session("alex")
    fund.paycheck(s.fund)
    fund.paycheck(s.fund)
    return s


# ---------- the snapshot ----------

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUM = re.compile(r"(-?)\$?(\d[\d,]*(?:\.\d+)?)")   # "-$12.75" is -12.75


def _nums(text: str) -> list[float]:
    return [round(float(sign + n.replace(",", "")), 2) for sign, n in NUM.findall(DATE.sub(" ", text))]


def _numbers_in(x, out: set):
    """Every number in the Python-computed facts: numeric values, and digits inside strings (labels, the goal)."""
    if isinstance(x, bool) or x is None:
        return out
    if isinstance(x, (int, float)):
        out.add(round(float(x), 2))
    elif isinstance(x, str):
        for d in DATE.findall(x):
            out.add(d)
        out.update(_nums(x))
    elif isinstance(x, dict):
        for v in x.values():
            _numbers_in(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _numbers_in(v, out)
    return out


@pytest.mark.parametrize("cid", ["alex", "maria"])
def test_every_number_in_the_snapshot_comes_from_python(cid):
    s = _alex_with_history() if cid == "alex" else make_fund("maria")
    if cid == "maria":
        fund.paycheck(s.fund)
    facts = account_facts(s)
    text = account_snapshot(s, facts)
    source = _numbers_in(facts, set())
    dates = DATE.findall(text)
    nums = _nums(text)
    assert dates and nums
    assert [d for d in dates if d not in source] == []
    assert [n for n in nums if n not in source] == []


def test_snapshot_figures_match_the_app():
    """The facts themselves are the app's numbers: fund value, holdings, paychecks, bank feed, review."""
    s = _alex_with_history()
    a = s.fund
    f = account_facts(s)
    vals = values(a.customer_id, a.today)
    assert f["fund"]["value"] == round(sum(vals.values()), 2) == round(fund.value(a), 2)
    assert f["fund"]["put_in"] == round(a.contributed, 2)
    for h in f["fund"]["holdings"]:
        assert h["dollars"] == round(vals.get(h["ticker"], 0.0), 2)
    assert [d["invested"] for d in f["paycheck"]["recent"]] == [round(d["invested"], 2) for d in a.deposits[:3]]
    feed = nessie.recent_activity(a.customer_id, limit=len(f["bank_activity"]))
    assert [x["amount"] for x in f["bank_activity"]] == [round(x["amount"], 2) for x in feed]
    assert f["review"]["last"] == (a.last_review.isoformat() if a.last_review else None)
    text = account_snapshot(s, f)
    for needle in ("Retire early", "15 years or more", "Recent paychecks", "Recent bank activity", "Tier ",
                   "Notifications:", "Watchlist:"):
        assert needle in text


def test_snapshot_stays_within_its_budget():
    s = _alex_with_history()
    s.fund.answers["stocks"] = ["AAPL", "MSFT", "NVDA"]
    fund.build(s.fund)
    for _ in range(4):
        fund.paycheck(s.fund)
    text = account_snapshot(s)
    assert len(text) <= SNAPSHOT_MAX_CHARS + 60
    assert len(text) / 4 < 1500     # ~4 characters a token
    assert text.count("\n- ") <= 10 + 8   # capped holdings + bank lines


# ---------- the Claude loop (fake client) ----------

def test_claude_turn_puts_the_snapshot_after_the_cached_system_prompt(monkeypatch):
    s = _alex_with_history()
    fake = FakeClient(_resp(_text("You're up.")))
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake)
    reply, events = claude_agent.chat_turn(s, "How much is my fund worth?")
    assert reply == "You're up." and events == set()
    call = fake.calls[0]
    assert call["model"] == claude_agent.MODEL and call["tools"] is TOOLS
    cached, snap = call["system"]
    assert cached == {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in snap
    assert snap["text"] == SNAPSHOT_HEADER + account_snapshot(s)
    assert f"Fund value ${account_facts(s)['fund']['value']:,.2f}" in snap["text"]


def test_model_comes_from_the_environment():
    assert claude_agent.MODEL == __import__("os").getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def test_tool_loop_runs_tools_and_refreshes_panels(monkeypatch):
    s = _alex_with_history()
    fake = FakeClient(_resp(_tool("set_paycheck_percent", {"percent": 15}), stop="tool_use"), _resp(_text("Done.")))
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake)
    reply, events = claude_agent.chat_turn(s, "Invest 15% from now on")
    assert reply == "Done." and "account" in events and s.fund.invest_pct == 15
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user", "assistant"]


def test_a_failed_turn_is_taken_back_out_and_answered_offline(monkeypatch):
    """A tool call whose follow-up request fails would leave a tool_use with no result in the history and break
    every later request. The turn is removed and the customer gets an offline answer instead."""
    s = _alex_with_history()
    fake = FakeClient(_resp(_tool("get_fund", {}), stop="tool_use"), RuntimeError("network down"))
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake)
    reply, _ = claude_agent.chat_turn(s, "How much is my fund worth?")
    assert s.messages == []
    assert f"${account_facts(s)['fund']['value']:,.2f}" in reply


def test_an_empty_reply_falls_back_offline(monkeypatch):
    s = _alex_with_history()
    monkeypatch.setattr(claude_agent, "_get_client",
                        lambda: FakeClient(_resp(SimpleNamespace(type="thinking"), stop="max_tokens")))
    reply, _ = claude_agent.chat_turn(s, "what's my goal?")
    assert "Retire early" in reply and s.messages == []


def test_auto_trade_tool_respects_the_tier(monkeypatch):
    s = make_fund("maria")
    out, events = run_tool(s, "toggle_auto_trade", {"enabled": True})
    assert "error" in out and s.fund.auto_trade is False and events == []


# ---------- offline replies ----------

def test_offline_replies_cover_the_account_questions():
    s = _alex_with_history()
    f = account_facts(s)
    ask = lambda q: claude_agent.offline_turn(s, q)[0]
    assert f"${f['fund']['value']:,.2f}" in ask("How much is my fund worth?")
    held = ask("What do I hold?")
    assert all(h["ticker"] in held for h in f["fund"]["holdings"][:5])
    last = f["paycheck"]["recent"][0]
    assert last["on"] in ask("Tell me about my last paycheck") and f"${last['invested']:,.2f}" in ask("my last paycheck?")
    bank = ask("What's been happening in my bank account?")
    assert f["bank_activity"][0]["text"] in bank and f"${f['bank']['checking']:,.2f}" in bank
    review = ask("When is my next review?")
    assert f"Tier {f['review']['tier']}" in review and str(f["review"]["every_days"]) in review
    goal = ask("What's my goal again?")
    assert "Retire early" in goal and "15 years or more" in goal
    why = ask("Why do I own BND?")
    assert "BND" in why and "bond" in why.lower()


AUTO_QUESTIONS = ["Is my fund customized for me?", "What's a custom fund?", "Can you show me a custom fund?",
                  "What would a custom fund look like?", "How does auto-trade work?", "Is auto trade on?",
                  "Should I turn off auto-trade?", "Switch to custom fund?", "What do I spend the most on?",
                  "Can I go back to the ETF fund?", "What are my spending insights?", "Is automatic trading off?"]


@pytest.mark.parametrize("q", AUTO_QUESTIONS)
def test_a_question_never_rebuilds_the_fund_or_flips_auto_trade(q):
    s = _alex_with_history()
    before = ([(h.ticker, h.weight) for h in s.fund.portfolio.holdings], dict(s.fund.answers), s.fund.auto_trade)
    reply, events = claude_agent.offline_turn(s, q)
    assert reply and events == set()
    assert ([(h.ticker, h.weight) for h in s.fund.portfolio.holdings], dict(s.fund.answers), s.fund.auto_trade) == before


def test_mode_switches_need_the_command_phrase():
    s = _alex_with_history()
    assert claude_agent.mode_command("is my fund custom?") is None
    assert claude_agent.mode_command("I'd like a custom fund someday") is None
    reply, events = claude_agent.offline_turn(s, "Switch to custom fund")
    assert s.fund.answers.get("custom_mode") and {"fund", "account"} <= events
    assert any(h.role == "stock" for h in s.fund.portfolio.holdings)
    claude_agent.offline_turn(s, "switch back to ETF fund.")
    assert "custom_mode" not in s.fund.answers and not any(h.role == "stock" for h in s.fund.portfolio.holdings)
    claude_agent.offline_turn(s, "Turn off auto-trade")
    assert s.fund.auto_trade is False
    claude_agent.offline_turn(s, "please turn on auto-trade")
    assert s.fund.auto_trade is True


# ---------- duplicate-message regressions ----------

def test_a_prompt_from_another_tab_is_sent_exactly_once():
    """Was: /chat?ask=... posted the prompt on load and relied on JS to drop ?ask= from the address bar. That ran
    after the request had already fired (or not at all), so a reload or Forward sent the prompt again and the
    question and answer showed up twice."""
    s = make_fund("sam")
    q = "What moved my fund today?"
    r = client.get(f"/p/sam/chat?ask={q}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/sam/chat"
    page = client.get("/p/sam/chat").text
    assert page.count('hx-post="/p/sam/chat/ask"') == 1
    first = client.post("/p/sam/chat/ask")
    assert q in first.text and "Today:" in first.text
    assert client.post("/p/sam/chat/ask").text == ""        # a second load or retry answers nothing
    assert '/chat/ask"' not in client.get("/p/sam/chat").text   # reload: nothing left to send
    assert [m["text"] for m in s.transcript].count(q) == 1


def test_two_first_requests_at_once_seed_the_session_once(monkeypatch):
    """Was: _session seeded a fresh session whenever its transcript was empty, without a lock. Two requests at
    once (a double click on Alex) both seeded it: the welcome message twice and the starting $52,000 bought twice."""
    real_start = fund.start

    def slow_start(acct, on=None):
        time.sleep(0.2)
        return real_start(acct, on)

    monkeypatch.setattr(pages.fund, "start", slow_start)
    threads = [threading.Thread(target=pages._session, args=("alex",)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = state.get("alex")
    assert sum("Welcome back" in m["text"] for m in s.transcript) == 1
    assert s.fund.contributed == 52_000


def test_chat_panel_scrolls_in_a_column_reverse_box():
    """The newest reply was cut off at the bottom: the scroll position was set at DOMContentLoaded and the web
    font loading afterwards made the text taller. A column-reverse scroller stays pinned to the newest message."""
    make_fund("sam")
    page = client.get("/p/sam/chat").text
    box = page.index("data-chat-scroll")
    assert "flex-col-reverse" in page[page.rindex("<div", 0, box):box]
    assert page.index('id="chat-messages"') > box
    assert "h-[calc(100vh-8.5rem)]" not in page
