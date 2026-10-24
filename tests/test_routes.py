"""HTTP smoke tests for the demo flow. Forces the offline scripted agent so
these never call the real Claude API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from core import nessie

client = TestClient(app)


@pytest.fixture(autouse=True)
def offline_and_reset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None
    nessie.reset()
    state._SESSIONS.clear()
    yield


def test_index_200():
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_dashboard_200(cid):
    assert client.get(f"/p/{cid}").status_code == 200


def test_alex_report_200():
    assert client.get("/p/alex/report").status_code == 200


def test_unknown_persona_404():
    assert client.get("/p/unknown").status_code == 404


def test_maria_not_ready_chat():
    r = client.post("/p/maria/chat", data={"message": ""})
    assert r.status_code == 200
    assert "Not yet" in r.text


def test_maria_payday_paused_no_trades():
    r = client.post("/p/maria/event/payday")
    assert r.status_code == 200
    assert "paused" in r.text.lower()
    assert "Alpaca order" not in r.text


def test_jordan_dashboard_shows_25():
    r = client.get("/p/jordan")
    assert "$25" in r.text


def test_sam_full_flow():
    r = client.post("/p/sam/chat", data={"message": ""})
    assert r.status_code == 200

    for answer in ["house", "10", "22", "tech", "wait it out"]:
        r = client.post("/p/sam/chat", data={"message": answer})
        assert r.status_code == 200

    assert "hx-swap-oob" in r.text
    assert "Overlap check" in r.text
    assert "AOA" in r.text

    r = client.post("/p/sam/crash/sell")
    assert r.status_code == 200
    assert "AOR" in r.text or "Growth" in r.text

    r = client.post("/p/sam/event/payday")
    assert r.status_code == 200
    assert "invested $50 of $150" in r.text


def test_crash_before_fund_exists_400():
    r = client.post("/p/sam/crash/sell")
    assert r.status_code == 400


@pytest.mark.parametrize("event", ["car_repair", "raise", "market_drop"])
def test_life_events_200(event):
    r = client.post(f"/p/sam/event/{event}")
    assert r.status_code == 200


def test_market_drop_lowers_sam_balance():
    before = client.get("/p/sam")
    assert "$3,000" in before.text
    r = client.post("/p/sam/event/market_drop")
    assert "$2,100" in r.text


def test_alex_tier4():
    r = client.get("/p/alex")
    assert "Tier 4" in r.text
