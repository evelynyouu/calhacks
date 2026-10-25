"""HTTP smoke tests for the demo flow. Forces the offline agent so these never call the real Claude API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from core import market
from tests.fund_helpers import make_fund

client = TestClient(app)

INTAKE = [  # (form data, what the chat shows as the user's message)
    ({"value": "Buy a house someday"}, "Buy a house someday"),
    ({"value": "$2,500"}, "$2,500"),
    ({"value": "12"}, "12%"),
    ({"value": "growth"}, "Strong growth, even with bigger dips"),
    ({"value": "medium"}, "Ends between $900 and $1,150"),
    ({"value": "20"}, "20%"),
    ({"value": "15plus"}, "In 15 years or more"),
    ({"values": ["tech", "healthcare"], "other": "video games"}, "Technology, Healthcare, Gaming"),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


def test_index_200():
    r = client.get("/")
    assert r.status_code == 200 and "Every paycheck invests itself" in r.text


@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_dashboard_200(cid):
    assert client.get(f"/p/{cid}").status_code == 200


def test_unknown_persona_404():
    assert client.get("/p/unknown").status_code == 404


def test_first_visit_greets_and_asks_the_first_question_with_a_text_box():
    page = client.get("/p/sam").text
    assert "Hi Sam!" in page and "What are you investing for?" in page
    assert '<textarea name="value"' in page and "Question 1 of 8" in page


def test_full_intake_builds_the_fund():
    client.get("/p/sam")
    for i, (form, shown) in enumerate(INTAKE):
        r = client.post("/p/sam/answer", data=form)
        assert r.status_code == 200 and shown in r.text, i
        if i == 1:
            assert 'type="range"' in r.text and "of your $2,050 paycheck" in r.text   # next: the % slider
        if i == 5:
            assert 'class="choice-btn"' in r.text and "When will you need" in r.text   # next: the horizon choice
        if i == 6:
            assert 'type="checkbox" name="values"' in r.text and 'name="other"' in r.text   # next: chips + Other
    assert "Your fund is ready" in r.text and 'id="fund-card" hx-swap-oob' in r.text
    for t in ("VTI", "XLK", "XLV", "ESPO"):
        assert t in r.text
    s = state.get("sam")
    assert s.done and s.fund.contributed == 2500 and s.fund.invest_pct == 12
    assert 'placeholder="Ask about your fund' in r.text                                # composer is free chat now


def test_bad_answer_explains_and_stays_on_the_question():
    client.get("/p/sam")
    client.post("/p/sam/answer", data={"value": "house"})
    r = client.post("/p/sam/answer", data={"value": "lots"})
    assert "Type a dollar amount" in r.text and state.get("sam").step == 1


def test_offline_chat_answers_from_the_report():
    make_fund("sam")
    for text in ("how am I doing?", "what should I do next?", "why do I own this?", "thanks!"):
        r = client.post("/p/sam/chat", data={"message": text})
        assert r.status_code == 200, text
    assert "Fund value" in client.post("/p/sam/chat", data={"message": "how am I doing?"}).text


def test_chat_before_the_intake_is_done_400():
    client.get("/p/sam")
    assert client.post("/p/sam/chat", data={"message": "hi"}).status_code == 400


def test_paycheck_posts_into_the_chat_and_updates_panels():
    make_fund("sam", paycheck_pct=10)
    r = client.post("/p/sam/paycheck")
    assert "Paycheck landed: $2,050. Invested $205 (10%)" in r.text
    assert 'hx-swap-oob="beforeend:#chat-messages"' in r.text and 'id="account" hx-swap-oob' in r.text


def test_actions_need_a_fund():
    client.get("/p/sam")
    for path in ("paycheck", "market_drop", "raise", "trades/confirm"):
        assert client.post(f"/p/sam/{path}").status_code == 400, path


def test_report_page_has_every_section():
    make_fund("sam", starting_amount=8_000)
    client.post("/p/sam/paycheck")
    r = client.get("/p/sam/report")
    assert r.status_code == 200
    # The report was reorganized (headline, what moved, your money, picks, mix, recs, outlook, finances, market, data notes).
    for section in ("What moved your fund", "Your money", "Your picks", "Your mix and risk", "Companies inside your fund",
                    "What to do now", "Looking ahead", "Your finances, and where the fund fits", "The market, briefly",
                    "Where these numbers come from", "All prices here are simulated"):
        assert section in r.text, section


def test_report_before_a_fund_redirects_to_the_chat():
    client.get("/p/sam")
    r = client.get("/p/sam/report", follow_redirects=False)
    assert r.status_code == 303


def test_alex_starts_with_a_year_old_fund():
    page = client.get("/p/alex").text
    assert "Welcome back, Alex" in page and "Tier 4" in page
    assert client.get("/p/alex/report").status_code == 200


def test_queue_then_confirm_rebalance_trades():
    make_fund("sam", starting_amount=8_000, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(state.get("sam").fund.today, 30)
    r = client.post("/p/sam/rec", data={"action": "queue_trades"}, follow_redirects=False)
    assert r.status_code == 303 and state.get("sam").fund.pending
    page = client.get("/p/sam").text
    assert "waiting for your OK" in page and "Confirm trades" in page
    r = client.post("/p/sam/trades/confirm")
    # SMH is smaller now (sized for how much it swings), so it's no longer the first sale
    assert "Done: sell" in r.text and "sell SMH" in r.text and not state.get("sam").fund.pending


def test_recommendation_buttons_change_pct_and_interests():
    make_fund("sam")
    client.post("/p/sam/rec", data={"action": "set_pct", "value": "15"})
    client.post("/p/sam/rec", data={"action": "add_interest", "value": "utilities"})
    a = state.get("sam").fund
    assert a.invest_pct == 15 and "XLU" in [h.ticker for h in a.portfolio.holdings]
    client.post("/p/sam/rec", data={"action": "remove_interest", "value": "tech"})
    assert "XLK" not in [h.ticker for h in a.portfolio.holdings]
    assert client.post("/p/sam/rec", data={"action": "add_interest", "value": "nope"}).status_code == 400


def test_paycheck_slider_saves():
    make_fund("sam")
    client.post("/p/sam/pct", data={"pct": "35"})
    assert state.get("sam").fund.invest_pct == 35


def test_auto_trade_toggle_is_locked_below_5k():
    make_fund("sam", starting_amount=1_000)
    assert client.post("/p/sam/auto", data={"on": "1"}).status_code == 400
    make_fund("jordan", starting_amount=9_000)
    assert client.post("/p/jordan/auto", data={"on": "1"}).status_code == 200
    assert state.get("jordan").fund.auto_trade


def test_market_drop_lowers_the_fund():
    make_fund("sam")
    r = client.post("/p/sam/market_drop")
    assert "The market just fell 10%" in r.text


def test_start_over_resets_everything():
    make_fund("sam")
    client.post("/p/sam/paycheck")
    r = client.post("/p/sam/reset", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/p/sam").text
    assert "Question 1 of 8" in page and state.get("sam").fund.contributed == 0


# ---------- Tabs after the intake ----------

def test_finishing_the_intake_shows_the_tabs():
    client.get("/p/sam")
    for form, _ in INTAKE:
        r = client.post("/p/sam/answer", data=form)
    assert 'id="nav" hx-swap-oob' in r.text and "/p/sam/markets" in r.text


def test_portfolio_tab_shows_each_holding_today():
    make_fund("sam")
    page = client.get("/p/sam").text
    assert 'id="today"' in page and "today" in page and 'class="pill pill-' in page
    for tab in ("Portfolio", "Markets", "News", "Chat", "Report", "Account"):
        assert f">{tab}</a>" in page, tab
    assert "From FirstFund" in page


def test_markets_tab_lists_indexes_fund_and_watchlist_and_shows_detail():
    make_fund("sam")
    page = client.get("/p/sam/markets?s=AAPL&r=1M").text
    for section in ("Indexes", "Your fund", "Watchlist", "Apple", "52-week high", "$25 buys", "Add to my fund"):
        assert section in page, section
    assert 'data-chart="price"' in page
    assert client.get("/p/sam/markets?s=NOPE123").status_code == 404


def test_watchlist_add_and_remove():
    make_fund("sam")
    client.post("/p/sam/watch", data={"symbol": "xom", "op": "add"})
    assert "XOM" in state.get("sam").watchlist
    client.post("/p/sam/watch", data={"symbol": "XOM", "op": "remove"})
    assert "XOM" not in state.get("sam").watchlist


def test_add_a_single_company_then_a_paycheck_buys_partial_shares():
    make_fund("sam", paycheck_pct=10)
    client.post("/p/sam/stocks", data={"symbol": "AAPL", "op": "add"})
    a = state.get("sam").fund
    assert "AAPL" in [h.ticker for h in a.portfolio.holdings]
    r = client.post("/p/sam/paycheck")
    assert "AAPL $" in r.text
    from core.broker import get_broker
    assert 0 < get_broker().positions("sam")["AAPL"] < 1          # a partial share
    client.post("/p/sam/stocks", data={"symbol": "AAPL", "op": "remove"})
    assert "AAPL" not in [h.ticker for h in a.portfolio.holdings]
    assert client.post("/p/sam/stocks", data={"symbol": "VTI", "op": "add"}).status_code == 400


def test_search_works_offline_from_known_tickers():
    make_fund("sam")
    r = client.get("/p/sam/search?q=tesla")
    assert "TSLA" in r.text


def test_news_tab_without_a_connection_says_so_and_rundown_falls_back():
    make_fund("sam")
    page = client.get("/p/sam/news").text
    assert "No headlines right now" in page and "What moved my fund today?" in page
    r = client.get("/p/sam/rundown")
    assert "The market" in r.text and "Your fund" in r.text and "Worth watching" in r.text


def test_suggested_prompt_echoes_the_question():
    make_fund("sam")
    r = client.post("/p/sam/chat", data={"message": "What moved my fund today?", "echo": "1"})
    assert "What moved my fund today?" in r.text and "Today:" in r.text


def test_chat_tab_sends_a_prompt_from_another_tab():
    make_fund("sam")
    page = client.get("/p/sam/chat?ask=How%20is%20the%20market%20doing").text
    assert 'hx-trigger="load"' in page and "How is the market doing" in page


def test_account_page_sections_and_settings():
    make_fund("sam")
    page = client.get("/p/sam/account").text
    for section in ("Linked accounts", "Investing", "Notifications", "Security", "How partial shares work",
                    "Sign-in and two-factor authentication", "Data &amp; privacy"):
        assert section in page, section
    client.post("/p/sam/settings", data={"weekly_report": "on", "big_move_pct": "5"})
    assert state.get("sam").prefs == {"weekly_report": True, "trade_confirmations": False, "big_moves": False,
                                      "big_move_pct": 5}
    client.post("/p/sam/interests", data={"values": ["utilities", "bogus"]})
    assert [h.ticker for h in state.get("sam").fund.portfolio.holdings if h.role == "tilt"] == ["XLU"]


def test_ticker_tape_has_indexes_and_holdings():
    make_fund("sam")
    r = client.get("/p/sam/tape")
    assert "S&amp;P 500" in r.text and "VTI" in r.text and "Simulated prices" in r.text
