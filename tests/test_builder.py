"""Answers -> fund: the score, the horizon and max-drop ceilings, contradictions, overrides.  OWNER: Evelyn."""
import pytest

from core import builder, intake
from core.portfolio import BOND_MAX, BOND_MIN, RETURNS, bond_share_for, build_portfolio, level_for_score, worst_year

BOLD = {"risk_priority": "max", "risk_range": "widest", "max_drop": 50, "horizon": "15plus"}


def answers(**kw):
    return {**BOLD, "interests": {"themes": [], "unmatched": []}, **kw}


def bonds(p):
    return next(h.weight for h in p.holdings if h.ticker == "BND")


def kinds(p):
    return [r.kind for r in p.reasons]


def test_bond_share_is_smooth_and_in_5_point_steps():
    shares = [bond_share_for(s) for s in range(101)]
    assert shares[0] == BOND_MAX and shares[100] == BOND_MIN
    assert all(a >= b for a, b in zip(shares, shares[1:]))                    # never more bonds at a higher score
    assert all(round(x * 100) % 5 == 0 for x in shares)
    assert abs(bond_share_for(49) - bond_share_for(50)) <= 0.05 + 1e-9          # no cliff at a band edge


def test_levels_stay_in_bands_of_25():
    assert [level_for_score(s) for s in (0, 24, 25, 49, 50, 74, 75, 100)] == [1, 1, 2, 2, 3, 3, 4, 4]


def test_worst_year_uses_real_2008_and_2022_returns():
    steadiest = build_portfolio(1, [], bond_share=BOND_MAX)
    riskiest = build_portfolio(4, [], bond_share=BOND_MIN)
    assert worst_year(steadiest.holdings) == (2022, pytest.approx(-15.0, abs=0.1))   # bonds fell too in 2022
    assert worst_year(riskiest.holdings) == (2008, pytest.approx(-34.5, abs=0.2))
    assert set(RETURNS["VTI"]) == {2008, 2022}


def test_max_drop_is_a_ceiling_not_an_average():
    p = builder.build(answers(max_drop=25))
    assert p.worst_year_return >= -25
    assert p.pointed_level == 4 and p.risk_level < 4
    assert "drop" in kinds(p)


def test_a_drop_no_mix_can_meet_builds_the_steadiest_and_says_so():
    p = builder.build(answers(max_drop=5))
    assert bonds(p) == pytest.approx(BOND_MAX)
    note = next(r for r in p.reasons if r.kind == "drop")
    assert "5%" in note.text and note.text in p.overlap_warnings


def test_short_horizon_caps_the_level():
    p = builder.build(answers(horizon="lt3"))
    assert p.risk_level == 1 and p.pointed_level == 4
    assert "horizon" in kinds(p)


def test_not_sure_sets_no_cap():
    assert builder.build(answers(horizon="unsure")).risk_level == 4


def test_contradictory_answers_are_named_and_we_lean_careful():
    p = builder.build(answers(risk_priority="protect", risk_range="widest"))
    note = next(r for r in p.reasons if r.kind == "contradiction")
    assert "Protecting what I put in" in note.text and "$700 and $1,400" in note.text
    assert p.score < 50                                          # the plain average would be 50
    assert note.text in p.overlap_warnings


def test_override_builds_what_the_answers_pointed_to():
    capped = builder.build(answers(horizon="lt3"))
    chosen = builder.build(answers(horizon="lt3", override=True))
    assert chosen.risk_level == capped.pointed_level == 4
    assert chosen.pointed_level is None
    assert "you chose" in next(r for r in chosen.reasons if r.kind == "override").text.lower()


def test_intake_has_the_horizon_question_instead_of_swing_comfort():
    keys = [q.key for q in intake.QUESTIONS]
    assert "horizon" in keys and "swing_comfort" not in keys and len(keys) == 8
    q = intake.BY_KEY["horizon"]
    assert q.kind == "choice" and [v for v, _ in q.options] == ["lt3", "3to7", "7to15", "15plus", "unsure"]
    assert not q.risk                                            # a ceiling, not part of the average
