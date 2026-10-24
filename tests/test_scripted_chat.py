"""The offline scripted intake (no ANTHROPIC_API_KEY, e.g. bad venue wifi) must not 500 when the
visitor keeps chatting after their fund is built."""
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app


def test_chatting_after_the_fund_is_built_does_not_crash(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None
    state._SESSIONS.clear()
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/p/sam/chat", data={"message": ""})                     # the page load opens the chat
    for answer in ["house", "10", "22", "tech", "wait it out"]:
        c.post("/p/sam/chat", data={"message": answer})
    for extra in ["thanks!", "what now?"]:
        r = c.post("/p/sam/chat", data={"message": extra})
        assert r.status_code == 200, extra
        assert "payday" in r.text.lower() or "crash test" in r.text.lower()
