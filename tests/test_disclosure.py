"""The standard disclosure: one line under the header with the real state, the full text in the footer."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from core import disclosure as disclosure_module, market
from tests.fund_helpers import make_fund

client = TestClient(app)
PAGES = ("", "/money", "/report", "/markets?s=VTI", "/chat", "/account", "/how")


def test_every_customer_page_has_one_disclosure_line_and_the_footer():
    make_fund("sam")
    for path in PAGES:
        page = client.get(f"/p/sam{path}").text
        assert page.count('id="disclosure"') == 1, path
        assert page.count('id="disclosures"') == 1, path       # the footer
        assert "Not financial advice" in page and "Nothing here is financial" in page, path
        assert "Trades: simulated" in page and "Bank: demo data" in page, path
        assert page.count("Bank: demo data") == 1, path          # no leftover per-card badges
        assert page.count("Trades: simulated") == 1, path


def test_the_landing_page_has_the_footer_too():
    page = client.get("/").text
    assert 'id="disclosures"' in page and market.price_label(market.today()) in page


def test_the_line_follows_the_demo_date_after_a_paycheck():
    s = make_fund("sam")
    page = client.post("/p/sam/paycheck").text
    assert 'id="disclosure" hx-swap-oob="true"' in page
    assert market.price_label(s.fund.today) in page and "demo date" in page


def test_broker_state_is_accurate(monkeypatch):
    monkeypatch.setattr(disclosure_module, "get_broker", lambda: SimpleNamespace(mode="Alpaca paper"))
    d = disclosure_module.disclosure()
    labels = [i["label"] for i in d["items"]]
    assert "Trades: Alpaca paper" in labels and "Trades: simulated" not in labels
    assert "paper (practice)" in d["full"][0]


def test_markets_labels_a_price_only_when_it_differs_from_the_rest():
    make_fund("sam")
    usual = market.price_label(market.today())
    page = client.get("/p/sam/markets?s=VTI").text
    assert page.count(usual) == 1 + page.count(f"Prices shown: {usual}")   # just the header line (+ footer)
