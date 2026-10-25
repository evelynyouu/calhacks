"""The left sidebar and the Money tab (bank + investing on one page). Offline: fixture bank, simulated prices."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from app.routes import money as money_route
from core import fund, nessie
from core.profile import get_profile
from tests.fund_helpers import make_fund

client = TestClient(app)
PERSONAS = ["maria", "jordan", "sam", "alex"]
TABS = {"portfolio": "", "money": "/money", "markets": "/markets", "news": "/news", "chat": "/chat",
        "report": "/report", "backtest": "/backtest", "account": "/account"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


def money(v: float) -> str:      # the template's money2 filter
    return f"${v:,.2f}"


def active_key(page: str) -> str | None:
    m = re.search(r'class="side-link side-link-active" data-nav-key="(\w+)"\s+aria-current="page"', page)
    return m.group(1) if m else None


# ---------- sidebar ----------

@pytest.mark.parametrize("key", list(TABS))
def test_sidebar_on_every_tab_with_the_active_one_marked(key):
    make_fund("sam")
    page = client.get(f"/p/sam{TABS[key]}").text
    assert '<aside id="nav"' in page and 'data-nav-toggle aria-controls="nav" aria-expanded="false" aria-label="Menu"' in page
    assert active_key(page) == key
    assert page.count('aria-current="page"') == 1
    for k, href in TABS.items():
        assert f'href="/p/sam{href}" class="side-link' in page, k
    assert "Switch customer" in page and ">Sam<" in page


def link(cid: str) -> None:
    client.post(f"/p/{cid}/link", data={"checking": "checking", "investing": "investing"})


def test_sidebar_during_the_intake_shows_only_the_chat_and_money():
    link("maria")
    page = client.get("/p/maria").text
    keys = re.findall(r'data-nav-key="(\w+)"', page)
    assert keys == ["intake", "money"] and active_key(page) == "intake"
    assert active_key(client.get("/p/maria/money").text) == "money"


def test_money_link_hidden_until_a_bank_is_linked():
    page = client.get("/p/maria").text   # not linked yet: the link step, and no Money in the sidebar
    assert re.findall(r'data-nav-key="(\w+)"', page) in ([], ["intake"])
    r = client.get("/p/maria/money", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/maria"


def test_no_sidebar_on_the_landing_page():
    page = client.get("/").text
    assert 'id="nav"' not in page and "data-nav-toggle" not in page


def test_no_sidebar_on_sign_in_pages(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("LOGIN_ENABLED", "1")
    for path in ("/login", "/signup"):
        r = client.get(path)
        assert r.status_code == 200 and 'id="nav"' not in r.text and "data-nav-toggle" not in r.text, path


def test_finishing_the_intake_swaps_in_the_full_sidebar():
    s = make_fund("sam", goal="A house in 10 years")   # a timeline, so no follow-up before the fund is built
    s.step -= 1   # one question left
    r = client.post("/p/sam/answer", data={"values": ["tech"], "other": ""})
    assert '<aside id="nav" hx-swap-oob="true"' in r.text
    assert re.findall(r'data-nav-key="(\w+)"', r.text) == list(TABS)


def test_collapse_toggle_and_storage_are_wired():
    page = client.get("/p/alex").text
    assert "data-nav-collapse" in page and "localStorage.getItem('ff-nav')" in page
    assert '<link rel="stylesheet" href="/static/nav.css">' in page
    js = client.get("/static/app.js").text
    assert "localStorage.setItem(NAV_KEY" in js and "'Escape'" in js
    assert client.get("/static/nav.css").status_code == 200


# ---------- Money tab ----------

@pytest.mark.parametrize("cid", PERSONAS)
def test_money_renders_before_and_after_the_fund(cid):
    p = get_profile(cid)
    if cid != "alex":
        link(cid)
    page = client.get(f"/p/{cid}/money").text
    if cid != "alex":   # alex starts with a fund
        assert "Your fund isn't built yet" in page
    assert money(p.checking_balance) in page and money(p.cash_cushion) in page and money(p.card_debt) in page
    assert "Bank: demo data" in page
    if cid != "alex":
        make_fund(cid)
    page = client.get(f"/p/{cid}/money").text
    a = state.get(cid).fund
    assert "Your fund isn't built yet" not in page
    assert money(fund.value(a)) in page and money(a.contributed) in page
    assert "Simulated prices" in page


def test_next_paycheck_number_is_what_the_paycheck_actually_invests():
    s = make_fund("sam", paycheck_pct=13)
    nxt = fund.next_paycheck(s.fund)
    page = client.get("/p/sam/money").text
    shown = re.search(r"<strong[^>]*data-next-invest>\$([\d,]+)</strong>", page).group(1)
    d = fund.paycheck(s.fund)
    assert float(shown.replace(",", "")) == d["invested"] == nxt["invested"] == 266   # floor(2050 * 13%)
    assert nxt["on"] == s.fund.today and nxt["paycheck"] == d["paycheck"]


def test_invest_amount_rounds_down_and_preview_is_zero_without_a_fund():
    assert fund.invest_amount(2050, 15) == 307 and fund.invest_amount(999, 0) == 0
    s = state.get("maria")
    s.fund.invest_pct = 20
    assert fund.next_paycheck(s.fund)["invested"] == 0


def test_money_shows_bills_before_payday_activity_tags_and_python_numbers():
    s = make_fund("sam")
    client.post("/p/sam/paycheck")
    page = client.get("/p/sam/money").text
    ctx = money_route.money_context(s, get_profile("sam"), nessie.recent_activity("sam", limit=None))
    assert "Moved to investing</span>" in page   # the FirstFund transfer in the bank feed
    for b in ctx["bills_due"]:
        assert b["name"] in page and money(b["amount"]) in page
    assert ctx["bills_due"] and money(ctx["bills_total"]) in page
    inv = ctx["inv"]
    assert inv["from_paychecks"] == s.fund.deposits[0]["invested"] == inv["moved_by_bank"]
    assert money(inv["gain"] if inv["gain"] >= 0 else -inv["gain"]) in page
    for r in inv["top"]:
        assert money(r["value"]) in page
    for m in ctx["flow"]["months"]:
        for b in m["bars"]:
            assert f"${b['amount']:,.0f}" in page


def test_money_flow_buckets():
    feed = [
        nessie.activity_item("2026-10-10", "[payroll] paycheck", 2000, "in", "checking"),
        nessie.activity_item("2026-10-10", "[firstfund] payday auto-invest", 200, "move", "checking", "investing"),
        nessie.activity_item("2026-10-11", "[bill] Rent", 1000, "out", "checking"),
        nessie.activity_item("2026-10-12", "[spend] coffee", 50, "out", "checking"),
        nessie.activity_item("2026-09-12", "[spend] coffee", 25, "out", "card"),
        nessie.activity_item("2026-10-12", "[seed] move leftover to savings", 500, "move", "checking", "savings"),
        nessie.activity_item("2026-10-12", "[event] market move -10%", 300, "out", "investing"),
    ]
    flow = money_route.money_flow(feed)
    oct_, sep = flow["months"]
    assert {b["key"]: b["amount"] for b in oct_["bars"]} == {"paychecks": 2000, "invested": 200, "bills": 1000,
                                                            "spending": 50}
    assert {b["key"]: b["amount"] for b in sep["bars"]}["spending"] == 25
    assert oct_["bars"][0]["width"] == 100.0 and money_route.money_flow([])["months"] == []


def test_zero_dollar_fund_and_no_activity(monkeypatch):
    make_fund("sam", starting_amount=0, paycheck_pct=0)
    monkeypatch.setattr(nessie, "recent_activity", lambda cid, limit=8: [])
    page = client.get("/p/sam/money").text
    assert "$0 invested so far" in page and "No bank activity yet." in page and "No activity yet." in page


def test_money_unknown_persona_404():
    assert client.get("/p/nobody/money").status_code == 404


def test_money_in_live_mode_shows_account_last4_and_live_badge(monkeypatch, tmp_path):
    from core import profile, seed
    from core import state as core_state
    from tests.fake_nessie import FakeNessie
    f = FakeNessie()
    monkeypatch.setattr(nessie, "_send", f.request)
    nessie.clear_cache()
    monkeypatch.setattr(core_state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(profile, "CACHE_DIR", tmp_path / "cache")
    nessie.reset()
    seed.seed()
    monkeypatch.setenv("NESSIE_MODE", "live")
    try:
        make_fund("jordan")
        client.post("/p/jordan/paycheck")
        page = client.get("/p/jordan/money").text
        ids = nessie.load_ids()["jordan"]["accounts"]
        assert "Live: Capital One Nessie" in page
        for kind in ("checking", "savings", "card", "investing"):
            assert f"••••{ids[kind][-4:]}" in page, kind
        p = get_profile("jordan")
        assert money(p.checking_balance) in page and "Moved to investing</span>" in page
    finally:
        monkeypatch.setenv("NESSIE_MODE", "fixture")
        nessie.reset()
