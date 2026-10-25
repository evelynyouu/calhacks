"""Override, not a gate: when the horizon or max-drop limit lowered the mix, the customer can choose what
their answers pointed to.  OWNER: Evelyn."""
from fastapi.testclient import TestClient

from app import state
from app.main import app
from core import builder
from tests.fund_helpers import make_fund

client = TestClient(app)
SOON = {"risk_priority": "max", "risk_range": "widest", "max_drop": 50, "horizon": "lt3"}


def test_offer_names_both_mixes_and_the_reason():
    p = builder.build({**SOON, "interests": {"themes": [], "unmatched": []}})
    offer = builder.override_offer(p, {})
    assert offer["pointed"] == "Aggressive" and offer["built"] == "Conservative"
    assert "need most of this money" in offer["because"]
    assert builder.override_offer(builder.build({**SOON, "horizon": "15plus"}), {}) is None


def test_fund_card_offers_the_override_and_the_choice_rebuilds_the_fund():
    make_fund("sam", **SOON)
    page = client.get("/p/sam").text
    assert "Your answers pointed to Aggressive; we built Conservative" in page and "Use Aggressive instead" in page

    r = client.post("/p/sam/override", data={"use": "answers"})
    a = state.get("sam").fund
    assert r.status_code == 200 and a.answers["override"] is True and a.portfolio.risk_level == 4
    assert any(x.kind == "override" for x in a.portfolio.reasons)
    assert "Go back to our suggestion" in r.text

    client.post("/p/sam/override", data={"use": "suggested"})
    assert state.get("sam").fund.portfolio.risk_level == 1 and not state.get("sam").fund.answers["override"]


def test_no_offer_when_nothing_was_lowered():
    make_fund("sam", horizon="15plus", max_drop=50)
    assert "Your answers pointed to" not in client.get("/p/sam").text
