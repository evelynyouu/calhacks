"""The goal question: choice cards plus "Something else", the structured value every reader uses, and the
horizon the goal suggests.  OWNER: Lance."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from core import explain, intake
from core.intake import BY_KEY, AnswerError, goal_from_text, goal_key, goal_label, goal_text, parse, record
from core.intake import suggested_horizon
from tests.fund_helpers import make_fund
from tests.test_routes import link

client = TestClient(app)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


def test_goal_is_five_cards_plus_something_else():
    q = BY_KEY["goal"]
    assert q.kind == "choice" and q.other == "Something else"
    assert [v for v, _ in q.options] == ["home", "retirement", "travel", "safety", "wealth"]


def test_card_and_typed_goal_parse():
    q = BY_KEY["goal"]
    assert parse(q, "travel") == ("travel", "Travel")
    assert parse(q, "travel", other="ignored when a card is clicked") == ("travel", "Travel")
    assert parse(q, "", other="  Pay for grad school ") == ("other", "Pay for grad school")
    with pytest.raises(AnswerError, match="type your own"):
        parse(q, "", other="   ")
    with pytest.raises(AnswerError):
        parse(q, "yacht")   # not a card, nothing typed


def test_other_box_only_on_questions_that_offer_it():
    with pytest.raises(AnswerError):
        parse(BY_KEY["horizon"], "", other="whenever")


@pytest.mark.parametrize("text,key", [("a condo in Oakland", "home"), ("Retire at 55", "retirement"),
                                      ("a trip to Japan", "travel"), ("emergency fund", "safety"),
                                      ("grow my money", "wealth"), ("our wedding", "other"),
                                      ("sell my house and retire", "other")])   # two cards: don't guess
def test_typed_goal_maps_to_a_card_only_when_clear(text, key):
    assert goal_from_text(text) == key


def test_record_keeps_typed_words_and_drops_stale_ones():
    q, a = BY_KEY["goal"], {}
    assert record(a, q, "", other="Retire by the beach") == "Retire by the beach"
    assert a == {"goal": "retirement", "goal_text": "Retire by the beach"}
    assert goal_label(a) == "Retire by the beach"
    record(a, q, "home")
    assert a == {"goal": "home"} and goal_label(a) == "Buying a home"


def test_readers_handle_cards_typed_and_older_free_text_goals():
    assert (goal_key({}), goal_label({})) == (None, "")
    assert (goal_key({"goal": "safety"}), goal_text({"goal": "safety"}), goal_label({"goal": "safety"})) == \
           ("safety", "", "A safety net")
    old = {"goal": "Retire early"}                       # answers saved before the cards existed
    assert goal_key(old) == "retirement" and goal_label(old) == "Retire early"
    assert intake.answer_label("goal", {"goal": "wealth"}) == "Growing my wealth"


@pytest.mark.parametrize("goal,horizon", [("safety", "lt3"), ("home", "3to7"), ("travel", "3to7"),
                                          ("retirement", "15plus"), ("wealth", None), ("other", None)])
def test_goal_suggests_a_horizon(goal, horizon):
    s = suggested_horizon({"goal": goal})
    assert (s[0] if s else None) == horizon
    if s:
        assert s[1].startswith("Suggested because your goal is ") and "Pick whichever fits you." in s[1]


def test_suggestion_only_while_horizon_is_missing_or_not_sure():
    assert suggested_horizon({"goal": "retirement", "horizon": "unsure"})[0] == "15plus"
    assert suggested_horizon({"goal": "retirement", "horizon": "3to7"}) is None
    assert suggested_horizon({"goal": "home", "goal_text": "a house in 2035"}) is None   # they already said when
    assert "“Retire early”" in suggested_horizon({"goal": "retirement", "goal_text": "Retire early"})[1]


def _to_horizon(goal_form: dict) -> str:
    link("sam")
    r = None
    for form in [goal_form, {"value": "2500"}, {"value": "12"}, {"value": "growth"}, {"value": "medium"},
                 {"value": "20"}]:
        r = client.post("/p/sam/answer", data=form)
    assert "When will you need most of this money?" in r.text
    return r.text


def test_horizon_cards_highlight_the_suggestion_without_gating():
    html = _to_horizon({"value": "retirement"})
    assert 'data-suggested="15plus"' in html and "Suggested because your goal is retirement" in html
    assert html.count("Suggested</span>") == 1
    for v in ("lt3", "3to7", "7to15", "15plus", "unsure"):          # every card still submits
        assert f'name="value" value="{v}"' in html
    r = client.post("/p/sam/answer", data={"value": "lt3"})
    assert state.get("sam").fund.answers["horizon"] == "lt3" and r.status_code == 200


def test_no_suggestion_for_growing_wealth():
    assert "data-suggested" not in _to_horizon({"value": "wealth"})


def test_typed_goal_through_the_route():
    link("sam")
    r = client.post("/p/sam/answer", data={"value": "", "other": "A down payment"})
    a = state.get("sam").fund.answers
    assert "A down payment" in r.text and a["goal"] == "home" and a["goal_text"] == "A down payment"


def test_goal_label_on_the_account_page_and_explanations():
    s = make_fund("sam", goal="travel")
    assert "Your goal: Travel" in client.get("/p/sam/account").text
    assert "Goal: Travel." in explain.intro_facts(s.fund)
    s = make_fund("sam", goal="home", goal_text="Buy a house someday")
    assert "Your goal: Buy a house someday" in client.get("/p/sam/account").text
