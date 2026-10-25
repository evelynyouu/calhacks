"""Intake follow-ups: each rule fires on a clear case and not on a near miss; at most 2; keeping changes nothing.
OWNER: Lance."""
from __future__ import annotations

import copy
import math

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from core import followups, intake
from core.followups import KEEP, MAX_FOLLOWUPS, apply, check
from core.intake import AnswerError, Question
from core.profile import get_profile

client = TestClient(app)

# Answers that trip nothing (same as tests/test_routes.py INTAKE). The goal is an older free-text one on
# purpose: every rule must still read those (intake.goal_key maps "Buy a house someday" to "home").
BASE = {"goal": "Buy a house someday", "starting_amount": 2500, "paycheck_pct": 12, "risk_priority": "growth",
        "risk_range": "medium", "max_drop": 20,
        "interests": {"themes": ["tech", "healthcare"], "unmatched": []}}
BOLD = {"risk_priority": "max", "risk_range": "wide", "max_drop": 35}     # level 4, no clash


@pytest.fixture
def no_horizon_question(monkeypatch):
    """near_term_high_risk runs after the last risk question when the intake doesn't ask horizon itself, and
    after the horizon question (only on "Not sure yet") when it does; this fixture tests the first case."""
    monkeypatch.setattr(intake, "QUESTIONS", [q for q in intake.QUESTIONS if q.key != "horizon"])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


@pytest.fixture
def sam():
    return get_profile("sam")   # paycheck $2,050, income $4,100/mo, bills $1,850/mo, checking $575, savings $9,000


def ans(**kw):
    a = copy.deepcopy(BASE)
    a.update(kw)
    return a


def fired(answers, step, profile=None, asked=()):
    fu = check(answers, step, profile, asked)
    return fu.key if fu else None


def test_plain_answers_trip_nothing(sam):
    for q in intake.QUESTIONS:
        assert check(BASE, q.key, sam) is None, q.key


# ---------- Each rule: clear case vs near miss ----------

@pytest.mark.parametrize("goal", ["idk", "x", "money", "Not sure!", "??"])
def test_unclear_goal_fires(goal):
    fu = check(ans(goal=goal), "goal")
    assert fu.key == "unclear_goal" and ("wealth", "Growing my wealth") in fu.options
    fu = check(ans(goal="other", goal_text=goal), "goal")                    # the structured form
    assert fu and fu.key == "unclear_goal"


def test_unclear_goal_never_fires_on_a_card():
    for v, _ in intake.GOAL_OPTIONS:
        assert fired(ans(goal=v), "goal") is None, v


@pytest.mark.parametrize("goal", ["a car", "money for a house", "Retire at 60", "travel"])
def test_unclear_goal_near_miss(goal):
    assert fired(ans(goal=goal), "goal") is None


def test_unclear_goal_choice_sets_the_goal():
    a = ans(goal="other", goal_text="idk")
    shown = apply(check(a, "goal"), a, "wealth")
    assert shown == "Growing my wealth" and a["goal"] == "wealth" and intake.goal_label(a) == "Growing my wealth"


def test_start_over_balance(sam):
    total = sam.checking_balance + sam.cash_cushion + sam.invested_balance
    fu = check(ans(starting_amount=total + 1), "starting_amount", sam)
    assert fu.key == "start_over_balance" and fu.kind == "money"
    assert f"${total:,.0f}" in fu.text and f"${total + 1:,.0f}" in fu.text
    assert fired(ans(starting_amount=total), "starting_amount", sam) is None


def test_start_over_balance_edit_and_validation(sam):
    a = ans(starting_amount=50_000)
    fu = check(a, "starting_amount", sam)
    with pytest.raises(AnswerError):
        apply(fu, a, "lots")
    assert apply(fu, a, "$4,000") == "$4,000" and a["starting_amount"] == 4000
    assert a["followup_notes"] == ["You changed your starting amount to $4,000."]


def test_nothing_invested(sam):
    fu = check(ans(starting_amount=0, paycheck_pct=0), "paycheck_pct", sam)
    assert fu.key == "nothing_invested"
    assert ("pct", f"Invest 5% of each paycheck (${math.floor(2050 * 5 / 100)})") in fu.options
    assert fired(ans(starting_amount=0, paycheck_pct=1), "paycheck_pct", sam) is None
    assert fired(ans(starting_amount=1, paycheck_pct=0), "paycheck_pct", sam) is None
    a = ans(starting_amount=0, paycheck_pct=0)
    apply(fu, a, "pct")
    assert a["paycheck_pct"] == followups.SUGGESTED_PCT and a["starting_amount"] == 0


def test_paycheck_over_leftover_numbers_match_python(sam):
    pay, bills_month, income = sam.paycheck_amount, sam.monthly_bills, sam.monthly_income
    bills = round(bills_month * pay / income)
    left = pay - bills
    over = next(p for p in range(101) if math.floor(pay * p / 100) > left)       # first % that doesn't fit
    fu = check(ans(paycheck_pct=over), "paycheck_pct", sam)
    assert fu.key == "paycheck_over_leftover" and fu.kind == "slider" and fu.target == "paycheck_pct"
    for n in (math.floor(pay * over / 100), pay, bills, left):
        assert f"${n:,.0f}" in fu.text, n
    assert fu.default == over - 1 and math.floor(pay * fu.default / 100) <= left
    assert fired(ans(paycheck_pct=over - 1), "paycheck_pct", sam) is None
    assert (bills, left) == (925, 1125)   # Sam: $1,850 of bills over two $2,050 paychecks


def test_paycheck_over_leftover_slider_sets_pct(sam):
    a = ans(paycheck_pct=80)
    assert apply(check(a, "paycheck_pct", sam), a, "40") == "40%" and a["paycheck_pct"] == 40


@pytest.mark.parametrize("clash", [{"risk_priority": "protect", "max_drop": 45},
                                   {"risk_range": "widest", "max_drop": 5, "risk_priority": "steady"}])
def test_contradictory_risk_fires(clash):
    fu = check(ans(**clash), "max_drop")
    assert fu.key == "contradictory_risk" and len(fu.options) == 2


def test_contradictory_risk_near_miss():
    # protect = 0 vs a 25% drop = 0.5: a spread under RISK_SPREAD
    assert fired(ans(risk_priority="protect", risk_range="narrow", max_drop=25), "max_drop") is None
    # only fires after the last risk question
    assert fired(ans(risk_priority="protect", max_drop=45), "risk_range") is None


def test_contradictory_risk_pulls_the_other_answer_halfway():
    """Chosen answer stays; the other moves to the nearest allowed value about halfway between the two (0..1 scale)."""
    a = ans(risk_priority="protect", risk_range="medium", max_drop=45)
    fu = check(a, "max_drop")
    assert [v for v, _ in fu.options] == ["careful", "bold"]
    careful = copy.deepcopy(a)
    apply(fu, careful, "careful")              # protect chosen: the 45% drop comes down toward it
    assert careful["risk_priority"] == "protect" and 15 <= careful["max_drop"] <= 30
    assert careful["followup_notes"]
    bold = copy.deepcopy(a)
    apply(fu, bold, "bold")                    # the 45% drop chosen: protect moves up toward it
    assert bold["max_drop"] == 45 and bold["risk_priority"] in ("steady", "growth")
    # max drop is a ceiling on the mix, not part of the score, so only moving the priority changes the score
    assert intake.risk_score(careful) == intake.risk_score(a) < intake.risk_score(bold)


def test_contradictory_risk_range_vs_drop():
    a = ans(risk_priority="steady", risk_range="widest", max_drop=5)
    fu = check(a, "max_drop")
    assert fu.effects == {"careful": {"risk_range": "wide"}, "bold": {"max_drop": 30}}


def test_near_term_goal_with_high_risk(no_horizon_question):
    fu = check(ans(goal="wedding in 2027", **BOLD), "max_drop")
    assert fu.key == "near_term_high_risk"
    assert [v for v, _ in fu.options] == ["lt3", "3to7", "7to15", "15plus", "unsure"]
    for goal in ("a car", "house next year", "emergency fund", "tuition"):
        assert fired(ans(goal=goal, **BOLD), "max_drop") == "near_term_high_risk", goal
    a = ans(goal="a car", **BOLD)
    apply(fu, a, "lt3")
    assert a["horizon"] == "lt3"


def test_near_term_near_misses(no_horizon_question):
    assert fired(ans(goal="a car", risk_priority="steady"), "max_drop") is None   # risk level 2, not growth
    assert fired(ans(goal="a car in 2035", **BOLD), "max_drop") is None       # far date
    assert fired(ans(goal="Buy a house someday", **BOLD), "max_drop") is None
    assert fired(ans(goal="wedding in 2027", horizon="3to7", **BOLD), "max_drop") is None


def test_near_term_reads_the_goal_card(no_horizon_question):
    for g in ("safety", "travel"):
        assert fired(ans(goal=g, **BOLD), "max_drop") == "near_term_high_risk", g
    for g in ("home", "retirement", "wealth"):
        assert fired(ans(goal=g, **BOLD), "max_drop") is None, g
    assert fired(ans(goal="travel", goal_text="a trip around the world in 2035", **BOLD), "max_drop") is None


def test_with_the_horizon_question_near_term_asks_only_after_not_sure():
    assert any(q.key == "horizon" for q in intake.QUESTIONS)   # the intake asks it directly now
    assert fired(ans(goal="safety", **BOLD), "max_drop") is None                     # waits for the horizon answer
    assert fired(ans(goal="safety", horizon="15plus", **BOLD), "horizon") is None     # they said when: done
    assert fired(ans(goal="retirement", horizon="unsure", **BOLD), "horizon") is None  # not a near-term goal
    fu = check(ans(goal="safety", horizon="unsure", **BOLD), "horizon")
    assert fu.key == "near_term_high_risk" and "“A safety net”" in fu.text
    assert "unsure" not in [v for v, _ in fu.options]          # they already said "Not sure yet"
    a = ans(goal="safety", horizon="unsure", **BOLD)
    apply(fu, a, "lt3")
    assert a["horizon"] == "lt3"


def test_goal_no_timeline_was_removed():
    assert "goal_no_timeline" not in {r.__name__ for r in followups.RULES}


def test_unmatched_interests_offers_close_themes():
    a = ans(interests={"themes": ["tech"], "unmatched": ["helthcare", "solr"]})
    fu = check(a, "interests")
    assert fu.key == "unmatched_interests" and fu.kind == "chips" and fu.keep_label == "Skip it"
    assert [t for t, _ in fu.options] == ["healthcare", "clean_energy"]
    assert apply(fu, a, values=["healthcare", "bogus"]) == "Healthcare"
    assert a["interests"] == {"themes": ["tech", "healthcare"], "unmatched": ["solr"]}


def test_unmatched_interests_near_miss():
    assert fired(ans(interests={"themes": [], "unmatched": ["bitcoin"]}), "interests") is None   # nothing close
    assert fired(ans(interests={"themes": ["healthcare"], "unmatched": ["helthcare"]}), "interests") is None


# ---------- Shared behavior ----------

CASES = [  # (answers, step) for every rule
    (dict(goal="idk"), "goal"),
    (dict(starting_amount=99_999), "starting_amount"),
    (dict(starting_amount=0, paycheck_pct=0), "paycheck_pct"),
    (dict(paycheck_pct=90), "paycheck_pct"),
    (dict(risk_priority="protect", max_drop=45), "max_drop"),
    (dict(goal="a car", **BOLD), "max_drop"),
    (dict(interests={"themes": [], "unmatched": ["helthcare"]}), "interests"),
]


def test_every_rule_has_a_case_and_keep_changes_nothing(sam, no_horizon_question):
    keys = set()
    for kw, step in CASES:
        a = ans(**kw)
        fu = check(a, step, sam)
        assert fu, kw
        keys.add(fu.key)
        before = copy.deepcopy(a)
        assert apply(fu, a, keep=True) == fu.keep_label
        assert a == before, fu.key
        assert fu.keep_label == ("Skip it" if fu.kind == "chips" else "Keep my answer")
    assert keys == {r.__name__ for r in followups.RULES}


def test_at_most_two_per_intake(sam):
    a = ans(goal="idk", starting_amount=0, paycheck_pct=0)
    asked = []
    for q in intake.QUESTIONS:
        while (fu := check(a, q.key, sam, asked)):
            asked.append(fu.key)
            apply(fu, a, keep=True)
    assert asked == ["unclear_goal", "nothing_invested"] and len(asked) == MAX_FOLLOWUPS
    assert check(ans(risk_priority="protect", max_drop=45), "max_drop", sam, asked) is None


def test_keep_value_is_not_an_option():
    fu = check(ans(goal="idk"), "goal")
    with pytest.raises(AnswerError):
        apply(fu, ans(goal="idk"), KEEP)


# ---------- Through the route ----------

INTAKE = [{"value": "", "other": "a car"}, {"value": "$2,500"}, {"value": "12"}, {"value": "protect"}, {"value": "medium"},
          {"value": "45"}, {"value": "3to7"}, {"values": ["tech"], "other": ""}]


def test_full_intake_with_a_followup_in_the_middle_builds_the_fund():
    client.post("/p/sam/link", data={"checking": "checking", "investing": "investing"})
    client.get("/p/sam")
    for i, form in enumerate(INTAKE):
        r = client.post("/p/sam/answer", data=form)
        assert r.status_code == 200, i
        if i == 5:   # last risk answer clashes with the first: a follow-up, counter stays on question 6
            s = state.get("sam")
            assert s.followup and s.followup.key == "contradictory_risk" and s.step == 5
            assert "Which sounds more like you?" in r.text and "Question 6 of 8" in r.text
            assert "quick follow-up" in r.text and 'name="keep"' in r.text
            r = client.post("/p/sam/answer", data={"value": "careful"})
            assert "When will you need most of this money?" in r.text and "Question 7 of 8" in r.text
    s = state.get("sam")
    assert s.done and s.step == len(intake.QUESTIONS) and s.followup is None
    assert s.followups_asked == ["contradictory_risk"] and s.fund.answers["risk_priority"] == "protect"
    assert s.fund.answers["max_drop"] < 45 and s.fund.answers["followup_notes"]
    assert "Your fund is ready" in r.text and s.fund.portfolio


def test_keep_through_the_route_and_bad_followup_answer():
    client.post("/p/sam/link", data={"checking": "checking", "investing": "investing"})
    client.get("/p/sam")
    client.post("/p/sam/answer", data={"value": "", "other": "idk"})
    s = state.get("sam")
    assert s.followup.key == "unclear_goal" and s.step == 0
    r = client.post("/p/sam/answer", data={"value": "nope"})
    assert "Pick one of the options" in r.text and s.followup
    r = client.post("/p/sam/answer", data={"keep": "1", "value": ""})
    assert "Keep my answer" in r.text and s.fund.answers["goal"] == "other" and s.step == 1
    assert s.fund.answers["goal_text"] == "idk"
    assert "How much do you want to put in to start?" in r.text


def test_money_followup_keep_skips_validation():
    client.post("/p/sam/link", data={"checking": "checking", "investing": "investing"})
    client.get("/p/sam")
    client.post("/p/sam/answer", data={"value": "home"})
    r = client.post("/p/sam/answer", data={"value": "$50,000"})
    assert "$50,000 is more than the $12,575" in r.text and 'formnovalidate' in r.text
    client.post("/p/sam/answer", data={"keep": "1"})
    s = state.get("sam")
    assert s.fund.answers["starting_amount"] == 50_000 and s.step == 2


def test_offline_wording_is_the_python_text():
    fu = check(ans(paycheck_pct=80), "paycheck_pct", get_profile("sam"))
    assert claude_agent.followup_text(fu) == fu.text


def test_claude_rewording_with_a_new_number_is_dropped(monkeypatch):
    fu = check(ans(paycheck_pct=80), "paycheck_pct", get_profile("sam"))
    monkeypatch.setattr(claude_agent, "_get_client", lambda: object())
    monkeypatch.setattr(claude_agent, "explain", lambda *a, **k: "Is 80% okay, or about $999 instead?")
    assert claude_agent.followup_text(fu) == fu.text
    monkeypatch.setattr(claude_agent, "explain", lambda *a, **k: "Want to keep 80%, which leaves about $1,125?")
    assert claude_agent.followup_text(fu) == "Want to keep 80%, which leaves about $1,125?"
