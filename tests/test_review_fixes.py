"""Fixes from the final review: what the card, page and chat say must match what was built.  OWNER: Evelyn."""
from core import builder, explain, intake
from core.portfolio import BOND_MAX, build_portfolio, worst_year
from tests.fund_helpers import make_fund

BOLD = {"risk_priority": "max", "risk_range": "widest", "interests": {"themes": [], "unmatched": []}}


def text(p, kind):
    return " ".join(r.text for r in p.reasons if r.kind == kind)


def test_after_an_override_the_reasons_say_the_customer_chose_past_the_limits():
    p = builder.build({**BOLD, "max_drop": 20, "horizon": "lt3", "override": True})
    assert "steadiest" not in text(p, "drop") and "you chose" in text(p, "drop").lower()
    assert "ride out" not in text(p, "horizon") and "you chose" in text(p, "horizon").lower()
    assert not any("steadiest" in w for w in p.overlap_warnings)


def test_the_built_score_is_explained_where_a_limit_changed_it():
    p = builder.build({**BOLD, "max_drop": 50, "horizon": "lt3"})
    assert p.score != 100 and f"risk score of {p.score}" in text(p, "horizon")
    q = builder.build({**BOLD, "max_drop": 25, "horizon": "15plus"})
    assert f"risk score of {q.score}" in text(q, "drop")


def test_claude_and_the_chat_tool_get_both_scores_and_the_reason():
    s = make_fund("sam", **{**BOLD, "max_drop": 50, "horizon": "lt3"})
    from agent.claude_agent import fund_facts
    from agent.tools import fund_summary
    facts = " ".join(fund_facts(s))
    assert "100 out of 100" in facts and f"built at a risk score of {s.fund.portfolio.score}" in facts
    assert "so the fund is Conservative" not in facts.replace(f"{s.fund.portfolio.score} out of 100, so the fund is Conservative", "")
    summary = fund_summary(s)
    assert summary["risk_score_used"] == s.fund.portfolio.score and summary["limits"]


def test_drop_help_text_is_true_for_low_answers_and_the_default_isnt_the_floor():
    q = intake.BY_KEY["max_drop"]
    steadiest = worst_year(build_portfolio(1, [], bond_share=BOND_MAX).holdings)[1]
    assert f"{abs(steadiest):g}%" in q.help and "steadiest" in q.help
    assert q.default > abs(steadiest)


def test_contradiction_note_never_says_x_instead_of_x_or_contradicts_the_drop_note():
    careful_prefs = builder.build({"risk_priority": "protect", "risk_range": "narrow", "max_drop": 50, "horizon": "15plus"})
    assert "instead of 0" not in text(careful_prefs, "contradiction")
    unreachable = builder.build({**BOLD, "max_drop": 5, "horizon": "15plus"})
    assert "stays within it" not in text(unreachable, "contradiction")


def test_horizon_sentence_reads_right_for_every_option():
    for h, _ in intake.BY_KEY["horizon"].options:
        t = text(builder.build({**BOLD, "max_drop": 50, "horizon": h}), "horizon")
        assert "money 7 to 15" not in t and "money 15 years" not in t and "money 3 to 7" not in t, t


def test_full_version_counts_funds_not_companies_and_retaking_is_described_right():
    s = make_fund("sam", stocks=["NVDA", "AAPL"])
    how = explain.build(s.fund)
    funds = sum(1 for h in s.fund.portfolio.holdings if h.role != "stock")
    assert f"built from {funds} of them" in " ".join(how["full"][0]["paras"])
    assert "clears your simulated fund" in " ".join(how["change"])


def test_offline_fund_ready_still_mentions_interests_with_no_fund(monkeypatch):
    import agent.claude_agent as claude_agent
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None
    s = make_fund("sam", interests={"themes": ["tech"], "unmatched": ["pet care"]})
    assert "pet care" in claude_agent.fund_ready(s)
