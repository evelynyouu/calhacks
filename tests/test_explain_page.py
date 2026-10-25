"""The "How your fund was built" page and the get_fund_explanation tool.  OWNER: Evelyn."""
import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from agent.tools import run_tool
from app import state
from app.main import app
from tests.fund_helpers import PERSONA_ANSWERS, make_fund

client = TestClient(app)
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


@pytest.mark.parametrize("cid", list(PERSONA_ANSWERS))
def test_page_renders_for_each_persona_with_the_full_version(cid):
    client.get(f"/p/{cid}")
    if not state.get(cid).fund.portfolio:
        make_fund(cid, **PERSONA_ANSWERS[cid])
    r = client.get(f"/p/{cid}/how")
    assert r.status_code == 200
    page = html.unescape(r.text)
    assert "How your fund was built" in page and "Read the full version" in page and "<details" in page
    assert 'data-chart="pie"' in page
    p = state.get(cid).fund.portfolio
    for h in p.holdings:   # every holding's line shows the Portfolio's own weight
        assert re.search(rf'data-holding="{h.ticker}"[^>]*data-weight="{h.weight:.4f}"', page), h.ticker
        assert f"{h.weight * 100:.1f}%" in page
    for title in ("Why your own index", "Where you spend", "Why some parts stay funds", "What bonds do", "What it costs", "How each paycheck invests",
                  "When the fund is rebalanced", "What's simulated or approximate"):
        assert title in page


def test_templates_hard_code_no_numbers():
    for name in ("how_built.html", "partials/how_built.html"):
        src = (TEMPLATES / name).read_text()
        src = re.sub(r"\{[{%#].*?[}%#]\}", "", src, flags=re.S)        # drop Jinja expressions
        src = re.sub(r'class="[^"]*"', "", src)                        # Tailwind sizes aren't money
        assert not re.search(r"\d+(\.\d+)?\s*%|\$\s*\d", src), name


def test_linked_from_the_end_of_the_intake_and_the_portfolio_tab():
    from tests.test_routes import INTAKE
    client.get("/p/sam")
    for form, _ in INTAKE:
        r = client.post("/p/sam/answer", data=form)
    assert 'href="/p/sam/how"' in r.text                          # the last chat message links to it
    assert 'href="/p/sam/how"' in client.get("/p/sam").text       # and so does the Portfolio tab


def test_agent_tool_answers_why_from_the_same_reasons():
    s = make_fund("sam")
    out, _ = run_tool(s, "get_fund_explanation", {"ticker": "XLK"})
    texts = [r["text"] for r in out["reasons"]]
    assert texts and all(r["ticker"] == "XLK" for r in out["reasons"])
    assert set(texts) <= {r.text for r in s.fund.portfolio.reasons}
    whole, _ = run_tool(s, "get_fund_explanation", {})
    assert len(whole["reasons"]) == len(s.fund.portfolio.reasons)


def test_explanation_data_has_the_slices_in_order_with_exact_weights():
    from core import explain
    from core.models import SLICES
    s = make_fund("sam")
    how = explain.build(s.fund)
    p = s.fund.portfolio
    assert [x["key"] for x in how["slices"]] == [k for k, _ in SLICES]
    for x in how["slices"]:
        assert x["weight"] == p.slice_weights()[x["key"]] and x["label"] == dict(SLICES)[x["key"]]
        for h in x["holdings"]:
            assert h["texts"] == [r.text for r in p.reasons if r.ticker == h["ticker"] and r.kind not in explain.ADJUST_KINDS]
    titles = [sec["title"] for sec in how["full"]]
    assert "Why your own index" in titles and "Where you spend" in titles and "Why some parts stay funds" in titles
    spend = next(sec for sec in how["full"] if sec["title"] == "Where you spend")
    assert any("isn't a prediction" in t or "personaliz" in t for t in spend["paras"])
