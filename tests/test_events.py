"""Demo buttons in fixture mode: fast-forward, life events, reset."""
import pytest

from core import nessie
from core.payday import apply_life_event, fast_forward_to_payday, reset_persona
from core.profile import get_profile
from core.readiness import check_readiness


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    nessie.reset()
    yield
    nessie.reset()


def test_sam_fast_forward_invests_50():
    r = fast_forward_to_payday("sam")
    assert (r.planned_amount, r.invested_amount) == (150, 50)
    assert get_profile("sam").days_to_payday == 14


def test_car_repair_takes_sam_below_three_months():
    before = get_profile("sam")
    p = apply_life_event("sam", "car_repair")
    assert p.cash_cushion == before.cash_cushion - 600
    assert p.checking_balance == before.checking_balance and p.card_debt == 0
    assert before.cushion_months >= 3 > p.cushion_months
    assert check_readiness(p).status == "start_small"


def test_car_repair_goes_on_card_when_savings_runs_out():
    for _ in range(3):   # Jordan has $1,500 saved
        p = apply_life_event("jordan", "car_repair")
    assert (p.cash_cushion, p.card_debt) == (0, 300)


def test_raise_then_fast_forward_invests_full_planned():
    before = get_profile("sam")
    p = apply_life_event("sam", "raise")
    assert p.paycheck_amount == before.paycheck_amount + 200
    assert p.monthly_income == before.monthly_income + 400
    r = fast_forward_to_payday("sam")
    # Lance's readiness: 25% of the $1,600 surplus = $400/mo -> $200/paycheck, and bills leave room for all of it
    assert (r.planned_amount, r.invested_amount) == (200, 200)


def test_market_drop_takes_alex_to_45500():
    assert apply_life_event("alex", "market_drop").invested_balance == 45500


def test_reset_restores_everything():
    start = {cid: get_profile(cid) for cid in ("sam", "alex")}
    fast_forward_to_payday("sam")
    for event in ("car_repair", "raise", "market_drop"):
        apply_life_event("sam", event)
    apply_life_event("alex", "market_drop")
    assert reset_persona("sam") == start["sam"]
    assert get_profile("alex") != start["alex"]   # reset is per persona
    assert reset_persona("alex") == start["alex"]


def test_unknown_event():
    with pytest.raises(ValueError):
        apply_life_event("sam", "lottery")


def test_other_bills_fill_the_gap_to_monthly_bills():
    for cid in ("maria", "jordan", "sam", "alex"):
        p = get_profile(cid)
        assert sum(b.amount for b in p.upcoming_bills) == p.monthly_bills
    assert [b.name for b in get_profile("sam").upcoming_bills] == ["Rent", "Phone + utilities"]
    assert next(b for b in get_profile("maria").upcoming_bills if b.name == "Other bills").amount == 310


# ---------- bank activity feed ----------

def test_activity_starts_with_seeded_history():
    items = nessie.recent_activity("sam", limit=None)
    assert {i["tag"] for i in items} >= {"payroll", "spend", "bill", "seed"}
    assert items[0]["on"] <= "2026-10-23"
    assert items == sorted(items, key=lambda i: i["on"], reverse=True)


def test_activity_after_payday_and_events():
    from core.portfolio import build_portfolio
    fast_forward_to_payday("sam", build_portfolio(2, []))
    apply_life_event("sam", "car_repair")
    top = nessie.recent_activity("sam", limit=6)
    assert len(top) == 6
    summary = [(i["tag"], i["text"], i["amount"], i["flow"]) for i in top]
    assert ("payroll", "paycheck", 2050, "in") in summary
    assert ("firstfund", "payday auto-invest", 50, "move") in summary
    assert ("event", "Car repair", 600, "out") in summary
    assert ("event", "cover Car repair", 600, "move") in summary
    move = next(i for i in top if i["tag"] == "firstfund")
    assert (move["account"], move["to"]) == ("Checking", "Investing")


def test_raise_first_does_not_rewrite_past_paychecks():
    apply_life_event("sam", "raise")
    paychecks = [i["amount"] for i in nessie.recent_activity("sam", limit=None) if i["tag"] == "payroll"]
    assert paychecks == [2050] * 4   # the history is from before the raise


def test_dashboard_shows_badge_and_bank_activity():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    page = c.get("/p/sam").text
    assert "Demo data" in page and "Bank activity" in page and "[payroll]" in page
    r = c.post("/p/sam/event/car_repair")
    assert "[event]</code> Car repair" in r.text and "−$600" in r.text


def test_newest_action_is_on_top_of_the_feed():
    fast_forward_to_payday("sam")
    apply_life_event("sam", "car_repair")
    assert [i["tag"] for i in nessie.recent_activity("sam", limit=4)] == ["event", "event", "payroll", "spend"]


def test_payday_shows_breakdown_bar():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).post("/p/sam/event/payday")
    assert "Bills due before the next paycheck: $1,850" in r.text   # segment tooltip
    assert "width: 71.43%" in r.text    # $1,850 of $2,590
