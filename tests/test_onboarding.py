"""Start as a new customer, and the demo bank link every customer goes through before the intake.
Fixture mode through the web routes, and live mode against tests/fake_nessie.py (like test_live.py)."""
from __future__ import annotations

import html
import math

import pytest
from fastapi.testclient import TestClient

import agent.claude_agent as claude_agent
from app import state
from app.main import app
from core import nessie, onboarding, profile, seed
from core.profile import diff, get_profile
from tests.test_live import fake, in_mode  # noqa: F401  (the fake-Nessie fixture)
from tests.test_routes import INTAKE

client = TestClient(app)
MAYA = {"name": "Maya", "paycheck": "$2,050", "frequency": "biweekly", "checking": "600",
        "savings": "1,500", "bills": "1200"}
LINK = {"checking": "checking", "investing": "investing"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_agent._client = None


def new_customers() -> list[str]:
    return [c for c in nessie._SEED if nessie.is_new_customer(c)]


def sign_up(**overrides) -> str:
    r = client.post("/start", data={**MAYA, **overrides}, follow_redirects=False)
    assert r.status_code == 303, r.text
    cid = r.headers["location"].removeprefix("/p/").removesuffix("/link")
    assert r.headers["location"] == f"/p/{cid}/link"
    return cid


def link_and_answer(cid: str):
    client.post(f"/p/{cid}/link", data=LINK)
    for form, _ in INTAKE:
        r = client.post(f"/p/{cid}/answer", data=form)
        assert r.status_code == 200
    assert state.get(cid).done


# ---------- the landing page and the form ----------

def test_landing_page_offers_a_new_customer_next_to_the_personas():
    page = client.get("/").text
    assert "Start as a new customer" in page and 'href="/start"' in page
    assert 'href="/p/sam"' in page
    cid = sign_up()
    assert f'href="/p/{cid}"' not in client.get("/").text   # new customers aren't listed as demo personas
    form = client.get("/start").text
    assert "mock bank" in form and "Demo only" in form and 'type="password"' not in form


@pytest.mark.parametrize("change,field,message", [
    ({"name": ""}, "name", "Enter your first name."),
    ({"name": "<script>"}, "name", "Use letters only"),
    ({"paycheck": "lots"}, "paycheck", "Type a dollar amount, like 1,850."),
    ({"paycheck": "0"}, "paycheck", "has to be more than $0"),
    ({"paycheck": "-50"}, "paycheck", "can't be negative"),
    ({"paycheck": ""}, "paycheck", "Enter an amount"),
    ({"paycheck": "90000"}, "paycheck", "$50,000 or less"),
    ({"frequency": "yearly"}, "frequency", "Pick how often you're paid."),
    ({"checking": ""}, "checking", "Enter an amount, like 600 (0 is fine)."),
    ({"savings": "a lot"}, "savings", "Type a dollar amount"),
])
def test_form_errors_are_plain_english_and_make_no_customer(change, field, message):
    r = client.post("/start", data={**MAYA, **change}, follow_redirects=False)
    assert r.status_code == 400 and message in html.unescape(r.text)
    assert "Please fix the field marked below." in r.text
    assert new_customers() == []
    with pytest.raises(onboarding.SignupError) as e:
        onboarding.parse_form(**{**MAYA, **change})
    assert list(e.value.errors) == [field]


def test_form_keeps_what_was_typed_after_an_error():
    r = client.post("/start", data={**MAYA, "paycheck": "abc"})
    assert 'value="Maya"' in r.text and 'value="abc"' in r.text


def test_optional_fields_can_be_left_blank_and_amounts_round_to_whole_dollars():
    c = onboarding.parse_form(name="  jo  ", paycheck="1,234.60", frequency="weekly", checking="0")
    assert (c.name, c.paycheck, c.checking, c.savings, c.bills) == ("Jo", 1235.0, 0.0, 0.0, 0.0)


# ---------- the new customer's mock bank ----------

@pytest.mark.parametrize("frequency,per_month,period", [("weekly", 4, 7), ("biweekly", 2, 14),
                                                         ("semimonthly", 2, 15), ("monthly", 1, 30)])
def test_new_customer_gets_a_profile_like_a_persona(frequency, per_month, period):
    cid = sign_up(frequency=frequency)
    assert cid.startswith(nessie.NEW_PREFIX) and cid not in ("maria", "jordan", "sam", "alex")
    p = get_profile(cid)
    assert (p.name, p.paycheck_amount, p.checking_balance, p.cash_cushion) == ("Maya", 2050, 600, 1500)
    assert (p.invested_balance, p.card_debt, p.monthly_bills) == (0, 0, 1200)
    assert p.monthly_income == 2050 * per_month and p.days_to_payday == onboarding.DAYS_TO_PAYDAY
    assert [b.name for b in p.upcoming_bills] == ["Rent and bills"]
    tags = {a["tag"] for a in nessie.recent_activity(cid, None)}
    assert {"payroll", "spend", "bill", "seed"} <= tags   # a little history: paychecks, spending, rent, savings
    paydays = [a for a in nessie.recent_activity(cid, None) if a["tag"] == "payroll"]
    assert len(paydays) == seed.paychecks_in_history(period)


def test_every_opening_balance_adds_up_for_odd_inputs():
    for pay, chk, sav, bills in [(400, 0, 0, 3000), (50_000, 1_000_000, 0, 0), (1, 0, 0, 0), (999, 37, 5, 1)]:
        for f in onboarding.FREQUENCIES:
            c = onboarding.NewCustomer("Al", pay, f, chk, sav, bills)
            plan = seed.plan_from(nessie._with_other_bills(onboarding.starting_numbers(c)))   # raises if not
            assert all(v >= 0 for v in plan["opening"].values())


# ---------- the link step ----------

def test_new_customer_links_then_lands_in_the_intake():
    cid = sign_up()
    r = client.get(f"/p/{cid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{cid}/link"

    bank = client.get(f"/p/{cid}/link").text
    assert "Capital One" in bank and "Nessie sandbox" in bank and "Not available in the demo" in bank
    assert f'href="/p/{cid}/link?step=signin"' in bank and "Demo · mock bank" in bank

    signin = client.get(f"/p/{cid}/link?step=signin").text
    assert "Continue as Maya (demo account)" in signin
    assert 'type="password"' not in signin and "no password" in signin

    accounts = client.get(f"/p/{cid}/link?step=accounts").text
    for text in ("Maya Checking", "$600.00", "FirstFund Investing", "$0.00", "Maya Savings", "$1,500.00",
                 "Read balances and transactions", "Touch your savings", "without your Confirm",
                 "$2,050 every 2 weeks", "Link account"):
        assert text in accounts, text

    r = client.post(f"/p/{cid}/link", data=LINK, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{cid}"
    page = client.get(f"/p/{cid}").text
    linked = state.get(cid).linked
    assert f"Linked: Capital One checking ••{linked['checking']}" in page
    assert "Paycheck $2,050 every 2 weeks." in page
    assert "Hi Maya!" in page and "Question 1 of 8" in page
    assert client.get(f"/p/{cid}/link", follow_redirects=False).status_code == 303   # already linked


def test_link_masks_are_stable_and_four_digits():
    cid = sign_up()
    first = onboarding.accounts(cid)
    assert [a["mask"] for a in first] == [a["mask"] for a in onboarding.accounts(cid)]
    assert all(len(a["mask"]) == 4 and a["mask"].isdigit() for a in first)


def test_link_rejects_other_accounts():
    r = client.post("/p/sam/link", data={"checking": "savings", "investing": "investing"})
    assert r.status_code == 400 and state.get("sam").linked is None


def test_new_customer_full_intake_and_paycheck_moves_money_to_investing():
    cid = sign_up()
    link_and_answer(cid)
    before = get_profile(cid)
    r = client.post(f"/p/{cid}/paycheck")
    assert r.status_code == 200 and "Paycheck landed: $2,050" in r.text
    after = get_profile(cid)
    invested = math.floor(2050 * 12 / 100)   # the intake picked 12%
    assert after.invested_balance == before.invested_balance + invested == 246
    moves = [a for a in nessie.recent_activity(cid, None) if a["tag"] == "firstfund"]
    assert [(m["account"], m["to"], m["amount"]) for m in moves] == [("Checking", "Investing", invested)]
    assert after.cash_cushion == before.cash_cushion   # savings untouched
    assert after.days_to_payday == 14


def test_monthly_customer_next_payday_is_a_month_out():
    cid = sign_up(frequency="monthly")
    link_and_answer(cid)
    client.post(f"/p/{cid}/paycheck")
    assert get_profile(cid).days_to_payday == 30


# ---------- personas ----------

@pytest.mark.parametrize("cid", ["maria", "jordan", "sam"])
def test_personas_link_before_the_intake(cid):
    r = client.get(f"/p/{cid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{cid}/link"
    assert client.post(f"/p/{cid}/answer", data={"value": "A house"}).status_code == 400
    assert state.get(cid).transcript == []   # no greeting until linked
    accounts = client.get(f"/p/{cid}/link?step=accounts").text
    assert "FirstFund Investing" in accounts and "Card" in accounts
    client.post(f"/p/{cid}/link", data=LINK)
    page = client.get(f"/p/{cid}").text
    assert "Linked: Capital One checking" in page and "Question 1 of 8" in page


def test_sam_link_shows_his_balances():
    page = client.get("/p/sam/link?step=accounts").text
    assert "Sam Checking" in page and "$575.00" in page and "$3,000.00" in page and "$9,000.00" in page


def test_alex_skips_the_link():
    r = client.get("/p/alex", follow_redirects=False)
    assert r.status_code == 200 and "Welcome back, Alex" in r.text
    r = client.get("/p/alex/link", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/alex"


def test_reset_unlinks():
    client.post("/p/sam/link", data=LINK)
    assert state.get("sam").linked
    client.post("/p/sam/reset")
    assert state.get("sam").linked is None
    assert client.get("/p/sam", follow_redirects=False).headers["location"] == "/p/sam/link"


def test_new_customer_reset_restores_their_starting_numbers_and_unlinks():
    cid = sign_up()
    link_and_answer(cid)
    client.post(f"/p/{cid}/paycheck")
    client.post(f"/p/{cid}/reset")
    p = get_profile(cid)
    assert (p.checking_balance, p.invested_balance) == (600, 0) and state.get(cid).linked is None


def test_unknown_customer_link_404():
    assert client.get("/p/new-nobody/link").status_code == 404
    assert client.get("/p/nobody/link").status_code == 404


# ---------- live mode (fake Nessie) ----------

@pytest.mark.parametrize("frequency", list(onboarding.FREQUENCIES))
def test_live_new_customer_is_written_to_nessie_and_matches_fixture(fake, monkeypatch, frequency):
    cid = sign_up(frequency=frequency)
    entry = nessie.load_ids()[cid]
    assert set(entry["accounts"]) == {"checking", "savings", "investing", "card"}
    assert entry["persona"]["name"] == "Maya" and entry["persona"]["pay_frequency"] == frequency
    accts = {k: fake.accounts[a] for k, a in entry["accounts"].items()}
    assert accts["checking"]["nickname"] == "Maya Checking" and accts["checking"]["type"] == "Checking"
    assert accts["investing"]["nickname"] == "FirstFund Investing"
    assert fake.customers[entry["customer_id"]]["first_name"] == "Maya"
    for kind in ("deposits", "withdrawals", "purchases"):
        for t in fake.txns[kind].values():
            assert t["description"].startswith(nessie.OUR_TAGS), t
    assert diff(get_profile(cid), in_mode(monkeypatch, "fixture", get_profile, cid)) == []
    assert get_profile(cid).checking_balance == 600


def test_live_new_customer_full_flow(fake):
    cid = sign_up()
    accounts = client.get(f"/p/{cid}/link?step=accounts").text
    assert "Maya Checking" in accounts and "Nessie sandbox (a test bank" in accounts
    link_and_answer(cid)
    client.post(f"/p/{cid}/paycheck")
    assert get_profile(cid).invested_balance == 246
    investing = nessie.load_ids()[cid]["accounts"]["investing"]
    assert [d["description"] for d in fake.txns["deposits"].values()
            if d["account_id"] == investing] == ["[firstfund] payday auto-invest"]
    assert not [c for c in fake.calls if c[1].split("?")[0].rstrip("/") == "/data"]


def test_live_new_customer_survives_a_restart(fake):
    cid = sign_up()
    before = get_profile(cid)
    for store in (nessie._SEED, nessie._STATE):   # a restart: memory gone, the ids file (and registry) remain
        store.pop(cid)
    nessie.clear_cache()
    assert diff(get_profile(cid), before) == []


def test_live_new_customer_reset_builds_fresh_accounts(fake):
    cid = sign_up()
    link_and_answer(cid)
    client.post(f"/p/{cid}/paycheck")
    old = nessie.load_ids()[cid]["accounts"]
    client.post(f"/p/{cid}/reset")
    new = nessie.load_ids()[cid]["accounts"]
    assert set(old.values()).isdisjoint(new.values())
    assert not set(old.values()) & set(fake.accounts)   # old accounts deleted
    assert get_profile(cid).invested_balance == 0 and get_profile(cid).checking_balance == 600
    seed.wait_for_background()
    assert cid not in nessie.load_ids().get("_spares", {})   # new customers get no spare copies


def test_live_link_creates_a_missing_investing_account(fake):
    def drop(ids):
        ids["sam"]["accounts"].pop("investing")
        ids["sam"]["seed"].pop("investing")
    seed._update_ids(drop)
    onboarding.ensure_investing("sam")
    aid = nessie.load_ids()["sam"]["accounts"]["investing"]
    assert fake.accounts[aid]["nickname"] == "FirstFund Investing"


def test_live_outage_on_sign_up_says_so(fake, monkeypatch):
    def down(*a, **k):
        raise nessie.NessieError("POST", "/customers", 503, "down")
    monkeypatch.setattr(nessie, "create_customer", down)
    r = client.post("/start", data=MAYA)
    assert r.status_code == 503 and "didn't answer" in html.unescape(r.text) and new_customers() == []
