"""The Backtest tab renders.  OWNER: Ethan (the page itself is Saahil's)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.fund_helpers import make_fund

client = TestClient(app)


@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_the_backtest_page_renders_for_every_persona(cid):
    client.get(f"/p/{cid}")
    if cid != "alex":
        make_fund(cid, starting_amount=4_000)
    r = client.get(f"/p/{cid}/backtest")
    assert r.status_code == 200 and "Backtest" in r.text


def test_the_backtest_page_sends_you_to_the_chat_before_a_fund_exists():
    client.get("/p/sam")
    assert client.get("/p/sam/backtest", follow_redirects=False).status_code == 303
