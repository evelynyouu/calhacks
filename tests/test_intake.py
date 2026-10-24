"""Intake questions: each answer type parses the way the chat shows it, and the risk score.  OWNER: Lance."""
import pytest

from core.intake import BY_KEY, QUESTIONS, AnswerError, match_interests, parse, parse_money, risk_level, risk_score


def test_question_kinds_follow_the_input_rules():
    for q in QUESTIONS:
        if q.kind == "choice":
            assert 2 <= len(q.options) <= 4, q.key       # fewer than 5 answers -> multiple choice
        if q.kind in ("slider", "scale"):
            assert q.min < q.max and q.step > 0, q.key
        if q.kind == "chips":
            assert len(q.options) >= 15, q.key           # lots of options, plus Other


@pytest.mark.parametrize("text,amount", [("500", 500), ("$1,200", 1200), ("1.5k", 1500), (" 0 ", 0),
                                         ("$3,000.50", 3000.5), ("250 USD", 250)])
def test_money(text, amount):
    assert parse_money(text) == amount


@pytest.mark.parametrize("text", ["", "abc", "-5", "99999999"])
def test_bad_money(text):
    with pytest.raises(AnswerError):
        parse_money(text)


def test_money_shows_as_dollars():
    assert parse(BY_KEY["starting_amount"], "1200") == (1200, "$1,200")


def test_slider_rounds_to_step_and_checks_range():
    q = BY_KEY["max_drop"]   # 0..50 in steps of 5
    assert parse(q, "23") == (25, "25%")
    with pytest.raises(AnswerError):
        parse(q, "80")


def test_scale_shows_out_of_ten():
    assert parse(BY_KEY["swing_comfort"], "7") == (7, "7 out of 10")


def test_choice_must_be_an_option():
    assert parse(BY_KEY["risk_priority"], "steady")[0] == "steady"
    with pytest.raises(AnswerError):
        parse(BY_KEY["risk_priority"], "yolo")


def test_text_is_required():
    with pytest.raises(AnswerError):
        parse(BY_KEY["goal"], "   ")


def test_chips_plus_other():
    stored, shown = parse(BY_KEY["interests"], values=["tech", "nonsense"], other="solar, pet care")
    assert stored == {"themes": ["tech", "clean_energy"], "unmatched": ["pet care"]}
    assert shown == "Technology, Clean energy, “pet care”"


def test_no_chips_keeps_it_broad():
    assert parse(BY_KEY["interests"]) == ({"themes": [], "unmatched": []}, "None, keep it broad")


@pytest.mark.parametrize("text,themes", [
    ("AI and video games", ["ai_chips", "gaming"]),
    ("banks; real estate", ["financials", "real_estate"]),
    ("I love coffee", ["staples"]),
    ("space", ["space_defense"]),
])
def test_match_interests(text, themes):
    assert match_interests(text)[0] == themes


def test_risk_score_bounds_and_levels():
    careful = {"risk_priority": "protect", "risk_range": "narrow", "max_drop": 0, "swing_comfort": 1}
    bold = {"risk_priority": "max", "risk_range": "widest", "max_drop": 50, "swing_comfort": 10}
    assert risk_score(careful) == 0 and risk_level(0) == 1
    assert risk_score(bold) == 100 and risk_level(100) == 4
    middle = {"risk_priority": "steady", "risk_range": "medium", "max_drop": 15, "swing_comfort": 4}
    assert risk_level(risk_score(middle)) == 2
