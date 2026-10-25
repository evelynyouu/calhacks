"""Targets that follow the customer, not the market (core/targets.py).

hypothesis isn't installed, so the property tests draw their cases from a seeded random.Random: the same cases
every run, many more than a hand-written list."""
import copy
import random
from datetime import timedelta

import pytest

from app import state as app_state
from core import fund, market, nessie, targets
from core.broker import get_broker, values
from tests.fund_helpers import PERSONA_ANSWERS, make_fund

SEED = 20261024
PERSONAS = ("maria", "jordan", "sam")
RISKY = {"risk_priority": "max", "risk_range": "widest", "max_drop": 50, "override": True}


def weights(a):
    return {h.ticker: h.weight for h in a.portfolio.holdings}


def snapshot(a):
    """Everything a target change could touch."""
    return {"weights": weights(a), "label": a.portfolio.risk_label, "pct": a.invest_pct,
            "answers": copy.deepcopy(a.answers), "glide": (a.glide_bonds, a.glide_on), "target_date": a.target_date,
            "baseline": dict(a.baseline), "pending": copy.deepcopy(a.pending),
            "held": values(a.customer_id, a.today), "history": len(a.target_history)}


def fresh():
    """What conftest does between tests, for tests that run the same story twice."""
    nessie.reset()
    app_state._SESSIONS.clear()
    get_broker().reset()
    market.clear_shocks()


# ---------- goal date and the published schedule ----------

def test_goal_date_is_the_bucket_midpoint_from_the_start():
    s = make_fund("jordan", **PERSONA_ANSWERS["jordan"])   # 3to7
    a = s.fund
    assert a.started == a.today
    assert a.target_date == a.started + timedelta(days=round(5.0 * targets.DAYS_PER_YEAR))
    assert targets.goal_date("unsure", a.today) is None
    assert targets.goal_date("15plus", a.today).year == a.today.year + 20


def test_glide_schedule_is_monotone_and_only_inside_the_window():
    assert targets.glide_floor(None) is None
    assert targets.glide_floor(targets.GLIDE_WINDOW_YEARS + 0.01) is None
    years = sorted(targets.GLIDE_PATH)
    floors = [targets.GLIDE_PATH[y] for y in years]
    assert floors == sorted(floors, reverse=True)   # fewer years left -> more bonds
    assert all(abs(a - b) <= targets.GLIDE_MAX_STEP + 1e-9 for a, b in zip(floors, floors[1:]))
    assert targets.glide_floor(-3) == targets.GLIDE_PATH[0]


def test_no_glide_on_day_one_and_none_for_unsure():
    s = make_fund("jordan", **{**PERSONA_ANSWERS["jordan"], **RISKY})
    assert targets.review(s.fund) is None   # the builder already set day one; steps start a year in
    fresh()
    s = make_fund("jordan", **{**PERSONA_ANSWERS["jordan"], **RISKY, "horizon": "unsure"})
    s.fund.today += timedelta(days=3 * 365)
    assert s.fund.target_date is None and targets.review(s.fund) is None


# ---------- property: price moves alone never change targets ----------

def _story(cid, answers, rng_seed, shocks):
    """Run eight paychecks (and a raise halfway), optionally with random market moves in between; return every
    target proposal and the final targets."""
    rng, market_rng = random.Random(rng_seed), random.Random(rng_seed + 1)   # market draws don't shift choices
    s = make_fund(cid, **answers)
    a = s.fund
    seen = []
    for i in range(8):
        if shocks:
            for _ in range(market_rng.randint(0, 3)):
                fund.market_drop(a, market_rng.choice([-30, -20, -10, -5, 5, 10, 25, 40]))
        if i == 4:
            nessie.apply_raise(cid, 10)
        fund.paycheck(a)
        if a.proposal:
            seen.append({k: a.proposal[k] for k in ("kind", "title", "text", "apply")})
            (targets.accept if rng.random() < 0.5 else targets.dismiss)(a)
    return seen, weights(a), a.invest_pct, a.glide_bonds


@pytest.mark.parametrize("case", range(12))
def test_price_moves_alone_never_change_targets(case):
    rng = random.Random(SEED + case)
    cid = rng.choice(PERSONAS)
    answers = dict(PERSONA_ANSWERS[cid])
    if rng.random() < 0.5:
        answers.update(RISKY)
    calm = _story(cid, answers, SEED * 7 + case, shocks=False)
    fresh()
    stormy = _story(cid, answers, SEED * 7 + case, shocks=True)
    assert stormy == calm


@pytest.mark.parametrize("case", range(10))
def test_a_market_move_never_creates_a_proposal(case):
    rng = random.Random(SEED + 100 + case)
    cid = rng.choice(PERSONAS)
    s = make_fund(cid, **PERSONA_ANSWERS[cid])
    a = s.fund
    before = snapshot(a)
    for _ in range(rng.randint(1, 6)):
        market.add_shock(a.today + timedelta(days=rng.randint(-30, 30)), rng.uniform(-60, 60))
        assert targets.review(a) is None
    after = snapshot(a)
    before.pop("held"), after.pop("held")   # holdings' dollar values move with prices; targets don't
    assert after == before


# ---------- property: a glide step never exceeds the cap ----------

@pytest.mark.parametrize("case", range(15))
def test_glide_steps_never_exceed_the_cap(case):
    rng = random.Random(SEED + 200 + case)
    cid = rng.choice(PERSONAS)
    answers = {**PERSONA_ANSWERS[cid], "horizon": rng.choice(["lt3", "3to7", "7to15"])}
    if rng.random() < 0.7:
        answers.update(RISKY)
    s = make_fund(cid, **answers)
    a = s.fund
    last_step = a.started
    for _ in range(rng.randint(3, 12)):
        a.today += timedelta(days=rng.randint(20, 500))
        bonds = targets.bond_share(a.portfolio)
        prop = targets.review(a)
        if not prop:
            continue
        assert prop["kind"] == "glide"
        new = prop["bonds"]["after"]
        assert 0 < new - bonds <= targets.GLIDE_MAX_STEP + 1e-9
        assert new <= targets.glide_floor(targets.years_left(a)) + 1e-9   # never past the schedule
        assert (a.today - last_step).days >= targets.GLIDE_STEP_DAYS
        assert abs(sum(r["after"] for r in prop["rows"]) - 1) < 1e-3
        if rng.random() < 0.6:
            targets.accept(a)
            last_step = a.today
            assert targets.bond_share(a.portfolio) == pytest.approx(new)
            assert abs(sum(weights(a).values()) - 1) < 1e-3
        else:
            targets.dismiss(a)


def test_glide_step_waits_a_year_and_survives_a_rebuild():
    s = make_fund("jordan", **{**PERSONA_ANSWERS["jordan"], **RISKY})
    a = s.fund
    a.today += timedelta(days=targets.GLIDE_STEP_DAYS - 1)
    assert targets.review(a) is None
    a.today += timedelta(days=1)
    prop = targets.review(a)
    assert prop["kind"] == "glide" and prop["title"] == "Your mix moves from 10% to 15% bonds"
    targets.accept(a)
    assert targets.bond_share(a.portfolio) == 0.15
    assert targets.review(a) is None   # the next step is a year away
    fund.set_interests(a, ["tech"])   # a rebuild keeps the accepted step
    assert targets.bond_share(a.portfolio) == 0.15
    assert any(r.kind == "glide" for r in a.portfolio.reasons)


# ---------- property: "Not now" leaves the fund untouched ----------

def _make_proposal(kind, rng):
    cid = rng.choice(PERSONAS)
    answers = {**PERSONA_ANSWERS[cid], **(RISKY if kind == "glide" else {}),
               **({"horizon": "3to7"} if kind == "glide" else {})}
    s = make_fund(cid, **answers)
    a = s.fund
    if kind == "income":
        nessie.apply_raise(cid, rng.choice([6, 10, 25, 50]))
        fund.paycheck(a)
    elif kind == "spending":
        for _ in range(5):
            nessie.record_purchase(cid, "new couch", rng.choice([500, 800, 1200]))
            fund.paycheck(a)
            if a.proposal:
                break
    elif kind == "glide":
        a.today += timedelta(days=targets.GLIDE_STEP_DAYS + rng.randint(0, 100))
        targets.review(a)
    else:
        targets.propose_answers(a, {"risk_priority": rng.choice(["protect", "max"]),
                                    "horizon": rng.choice(["lt3", "15plus"])})
    return a


@pytest.mark.parametrize("case", range(16))
def test_not_now_leaves_the_fund_untouched(case):
    rng = random.Random(SEED + 300 + case)
    kind = ("income", "spending", "glide", "answers")[case % 4]
    a = _make_proposal(kind, rng)
    assert a.proposal and a.proposal["kind"] == kind, a.proposal
    before = snapshot(a)
    targets.dismiss(a)
    assert a.proposal is None
    assert snapshot(a) == before
    assert targets.review(a) is None   # and the same suggestion doesn't come straight back


# ---------- accepting ----------

def test_raise_suggests_a_higher_percent_and_accept_records_a_reason():
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    a = s.fund
    nessie.apply_raise("sam", 10)
    fund.paycheck(a)
    prop = a.proposal
    assert prop["kind"] == "income" and prop["pct"]["before"] == 10 and prop["pct"]["after"] > 10
    assert "went up 10%" in prop["text"]
    targets.accept(a)
    assert a.invest_pct == prop["pct"]["after"] and a.proposal is None
    h = targets.history(a)[0]
    assert h["kind"] == "income" and h["pct"]["after"] == a.invest_pct
    assert a.target_history[0]["reason"].kind == "income"
    fund.paycheck(a)
    assert a.proposal is None   # the new pay is the new baseline


def test_suggest_pct_keeps_the_share_of_spare_money():
    base = {"paycheck": 2000, "per_month": 2, "bills": 1600, "spending": 1000, "pct": 10}   # $700 spare, $200 in
    up = targets.suggest_pct(base, {**base, "paycheck": 2200})     # $900 spare
    down = targets.suggest_pct(base, {**base, "paycheck": 1800})   # $500 spare
    assert down < 10 < up
    assert targets.suggest_pct({**base, "pct": 0}, {**base, "paycheck": 2500}) == 0   # 0% stays 0%
    assert targets.suggest_pct(base, {**base, "spending": 3000}) == 0   # nothing spare now: suggest a pause
    assert targets.suggest_pct({**base, "spending": 3000}, base) is None   # nothing spare to begin with
    assert targets.suggest_pct(base, base) == 10


def test_one_big_purchase_is_not_a_spending_shift():
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    a = s.fund
    nessie.record_purchase("sam", "car repair", 600)
    for _ in range(6):
        fund.paycheck(a)
        assert a.proposal is None


def test_moving_the_percent_yourself_resets_the_baseline():
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    a = s.fund
    nessie.apply_raise("sam", 10)
    fund.set_invest_pct(a, 15)   # the customer already adjusted
    fund.paycheck(a)
    assert a.proposal is None


def test_changed_answers_rebuild_with_the_builder():
    from core.builder import build
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    a = s.fund
    assert targets.propose_answers(a, {"risk_priority": a.answers["risk_priority"]}) is None   # nothing changed
    before = snapshot(a)
    prop = targets.propose_answers(a, {"risk_priority": "protect", "risk_range": "narrow"})
    assert snapshot(a) == before   # a proposal changes nothing
    assert prop["kind"] == "answers" and "Your mix moves from Growth to" in prop["title"]
    targets.accept(a)
    assert a.answers["risk_priority"] == "protect"
    assert weights(a) == {h.ticker: h.weight for h in build(a.answers, spending=fund._spending(a.customer_id)).holdings}
    assert a.target_history[0]["reason"].kind == "answers"


def test_new_horizon_restarts_the_goal_date():
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    a = s.fund
    a.today += timedelta(days=100)
    targets.propose_answers(a, {"horizon": "lt3"})
    targets.accept(a)
    assert a.target_date == targets.goal_date("lt3", a.today)
    assert a.portfolio.risk_level == 1


def test_override_click_is_recorded():
    s = make_fund("jordan", **{**PERSONA_ANSWERS["jordan"], **RISKY})
    fund.set_override(s.fund, False)
    h = targets.history(s.fund)[0]
    assert h["kind"] == "override" and "because you chose our suggestion" in h["text"] and h["rows"]


# ---------- pages and routes ----------

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_portfolio_card_accept_shows_on_how_page_and_report():
    c = _client()
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    page = c.get("/p/sam").text
    assert 'id="target-proposal"' in page and "Goal date: about October 2037" in page
    nessie.apply_raise("sam", 10)
    c.post("/p/sam/paycheck")
    page = c.get("/p/sam").text
    assert "Your paycheck share moves from 10% to 12%" in page and "Not now" in page
    assert "A suggested change is waiting" in c.get("/p/sam/report").text
    r = c.post("/p/sam/targets/accept")
    assert r.status_code == 200 and 'id="target-proposal"' in r.text and 'id="fund-card"' in r.text
    assert "Done: your paycheck share moves from 10% to 12%" in r.text
    assert s.fund.invest_pct == 12
    how = c.get("/p/sam/how").text
    assert 'data-target-change="income"' in how and "Paycheck share 10% → 12%" in how
    assert "When your targets change" in how and "7 years out 30%" in how
    assert 'data-target-change="income"' in c.get("/p/sam/report").text


def test_not_now_route_changes_nothing():
    c = _client()
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    nessie.apply_raise("sam", 10)
    c.post("/p/sam/paycheck")
    before = snapshot(s.fund)
    r = c.post("/p/sam/targets/dismiss")
    assert r.status_code == 200 and "Your fund stays exactly as it is" in r.text
    assert snapshot(s.fund) == before and s.fund.proposal is None
    assert "Check my targets" in c.post("/p/sam/targets/check").text


def test_account_answers_form_makes_a_proposal_with_before_and_after():
    c = _client()
    s = make_fund("sam", **PERSONA_ANSWERS["sam"])
    assert 'action="/p/sam/targets/answers"' in c.get("/p/sam/account").text
    before = snapshot(s.fund)
    form = {"risk_priority": "protect", "risk_range": "narrow", "max_drop": "10", "horizon": "7to15"}
    r = c.post("/p/sam/targets/answers", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/sam#target-proposal"
    assert snapshot(s.fund) == before
    page = c.get("/p/sam").text
    assert "Your mix moves from Growth to" in page and 'data-target-row="BND"' in page
    c.post("/p/sam/targets/accept")
    assert s.fund.answers["risk_priority"] == "protect" and s.fund.portfolio.risk_label != "Growth"
    assert c.post("/p/sam/targets/answers", data={**form, "horizon": "soon"}).status_code == 400
    same = c.post("/p/sam/targets/answers", data=form, follow_redirects=False)
    assert same.headers["location"].endswith("saved=risk#risk")


def test_a_raise_shows_the_paycheck_card_right_away():
    from tests.test_routes import client
    make_fund("maria")
    r = client.post("/p/maria/raise")
    assert r.status_code == 200
    assert 'id="target-proposal" hx-swap-oob="true"' in r.text and "Accept" in r.text
    assert app_state.get("maria").fund.proposal["kind"] == "income"
