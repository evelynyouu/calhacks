"""Live mode against an in-memory fake Nessie: seeding, the live profile, and the demo buttons.

The key check: after seeding, and after every button, the live profile matches the fixture
profile for the same steps (money within $1, everything else exactly).
"""
import json
from datetime import timedelta

import pytest
import requests

from core import live, nessie, profile, seed, state
from core.payday import apply_life_event, fast_forward_to_payday, reset_persona
from core.profile import diff, get_profile
from tests.fake_nessie import FakeNessie

PERSONAS = ["maria", "jordan", "sam", "alex"]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeNessie()
    monkeypatch.setattr(nessie, "_send", f.request)   # under the GET cache, so the cache is tested too
    nessie.clear_cache()
    monkeypatch.setattr(nessie, "IDS_PATH", tmp_path / "nessie_ids.json")
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(profile, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    nessie.reset()
    seed.seed()
    monkeypatch.setenv("NESSIE_MODE", "live")
    yield f
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    nessie.reset()   # the fixture stub is module state; leave it clean for other tests


def in_mode(monkeypatch, mode, fn, *args):
    monkeypatch.setenv("NESSIE_MODE", mode)
    try:
        return fn(*args)
    finally:
        monkeypatch.setenv("NESSIE_MODE", "live")


def assert_same(monkeypatch, cid):
    live = get_profile(cid)
    fixture = in_mode(monkeypatch, "fixture", get_profile, cid)
    assert diff(live, fixture) == [], cid


# ---------- seeding ----------

def test_seeded_profiles_match_fixture(fake, monkeypatch):
    for cid in PERSONAS:
        assert_same(monkeypatch, cid)


def test_seed_is_idempotent(fake):
    before = len([c for c in fake.calls if c[0] == "POST"])
    seed.seed()
    assert len([c for c in fake.calls if c[0] == "POST"]) == before


def test_accounts_and_tags(fake):
    ids = nessie.load_ids()
    sam = ids["sam"]["accounts"]
    assert fake.accounts[sam["investing"]]["type"] == "Savings"
    assert fake.accounts[sam["investing"]]["nickname"] == "FirstFund Investing"
    assert fake.accounts[ids["sam"]["accounts"]["card"]]["type"] == "Credit Card"
    assert fake.accounts[sam["card"]]["balance"] == 0
    assert len(ids["_merchants"]) == len(seed.MERCHANTS)
    assert len(fake.merchants) == len(seed.MERCHANTS)   # created once, not per persona
    payroll = [d for d in fake.txns["deposits"].values() if d["account_id"] == sam["checking"]]
    assert [d["transaction_date"] for d in payroll][-1] == "2026-10-10"   # last payday, 1 day before the next
    assert len(payroll) == seed.PAYCHECKS
    for kind in ("deposits", "withdrawals", "purchases", "transfers"):
        for t in fake.txns[kind].values():
            assert t["description"].startswith(nessie.OUR_TAGS), t


def test_never_touches_data(fake, monkeypatch):
    fast_forward_to_payday("sam")
    reset_persona("sam")
    assert not [c for c in fake.calls if c[1].split("?")[0].rstrip("/") == "/data"]


# ---------- buttons, live vs fixture ----------

def test_fast_forward_matches_fixture(fake, monkeypatch):
    for cid in PERSONAS:
        live = fast_forward_to_payday(cid)
        fixture = in_mode(monkeypatch, "fixture", fast_forward_to_payday, cid)
        assert (live.paycheck_detected, live.planned_amount, live.invested_amount) == \
               (fixture.paycheck_detected, fixture.planned_amount, fixture.invested_amount), cid
        assert live.reasons == fixture.reasons, cid
        assert_same(monkeypatch, cid)


def test_sam_live_fast_forward_invests_50(fake):
    r = fast_forward_to_payday("sam")
    assert (r.planned_amount, r.invested_amount) == (150, 50)
    assert profile.live_clock("sam")[0].isoformat() == "2026-10-24"   # read from Nessie, not a local file


@pytest.mark.parametrize("cid,events", [
    ("sam", ["car_repair"]),
    ("sam", ["raise", "payday"]),
    ("alex", ["market_drop"]),
    ("jordan", ["car_repair", "car_repair", "car_repair"]),   # third one spills onto the card
    ("sam", ["payday", "car_repair", "raise", "market_drop"]),
])
def test_events_match_fixture(fake, monkeypatch, cid, events):
    for e in events:
        step = fast_forward_to_payday if e == "payday" else (lambda c, e=e: apply_life_event(c, e))
        step(cid)
        in_mode(monkeypatch, "fixture", step, cid)
        assert_same(monkeypatch, cid)


def test_live_numbers(fake):
    assert apply_life_event("alex", "market_drop").invested_balance == 45500
    assert apply_life_event("jordan", "raise").paycheck_amount == 1600
    apply_life_event("sam", "raise")
    assert fast_forward_to_payday("sam").invested_amount == 200
    assert apply_life_event("sam", "car_repair").cushion_months < 3


def test_fast_forward_with_fund_transfers_to_investing(fake):
    from core.portfolio import build_portfolio
    r = fast_forward_to_payday("sam", build_portfolio(2, []))
    assert r.invested_amount == 50 and r.transfer_id in fake.txns["deposits"]   # the deposit side of the move
    assert get_profile("sam").invested_balance == 3050


# ---------- reset, cache, errors ----------

def test_reset_restores_and_only_touches_that_persona(fake, monkeypatch):
    ids = nessie.load_ids()
    start = get_profile("sam")
    fast_forward_to_payday("sam")
    apply_life_event("sam", "raise")
    apply_life_event("alex", "market_drop")
    old_sam = set(ids["sam"]["accounts"].values())
    alex_before = get_profile("alex")

    after = reset_persona("sam")
    new_ids = nessie.load_ids()
    assert after == start
    assert state.get("sam") == {}
    assert old_sam.isdisjoint(fake.accounts)   # Sam's old accounts are gone
    assert new_ids["alex"] == ids["alex"] and get_profile("alex") == alex_before
    deletes = [c[1] for c in fake.calls if c[0] == "DELETE"]
    assert sorted(deletes) == sorted(f"/accounts/{a}" for a in old_sam)


def test_cache_fallback_when_nessie_errors(fake, monkeypatch):
    good = get_profile("sam")

    def down(*a, **k):
        raise requests.ConnectionError("wifi")

    monkeypatch.setattr(nessie, "request", down)
    assert get_profile("sam") == good                  # the last live numbers
    assert nessie.offline_reason()
    nessie.go_online()
    start = in_mode(monkeypatch, "fixture", get_profile, "maria")
    nessie.go_offline("test")
    assert get_profile("maria") == start               # never seen live: starts from her starting numbers


def test_unseeded_persona_is_keyerror(fake, monkeypatch):
    ids = nessie.load_ids()
    del ids["jordan"]
    nessie.save_ids(ids)
    with pytest.raises(KeyError):
        get_profile("jordan")


def test_dry_run_prints_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nessie, "IDS_PATH", tmp_path / "nessie_ids.json")
    real = nessie.request
    with seed.dry_run():
        seed.seed(["sam"], save=False)
    assert nessie.request is real
    out = capsys.readouterr().out
    assert "POST /customers " in out and '"nickname": "FirstFund Investing"' in out
    assert out.count("[payroll] paycheck") == seed.PAYCHECKS and out.count("[spend]") == seed.PURCHASES
    assert not (tmp_path / "nessie_ids.json").exists()


def test_diff_tolerates_cents_but_not_days_or_apr():
    import dataclasses
    from core.models import FinancialProfile
    p = FinancialProfile("x", "X", 3000, 1200, 1000, 500.0, 7000, 0, 25.0, 0, 1500, 7)
    assert diff(dataclasses.replace(p, checking_balance=500.5), p) == []
    assert diff(dataclasses.replace(p, checking_balance=502), p) != []
    assert diff(dataclasses.replace(p, days_to_payday=8), p) != []
    assert diff(dataclasses.replace(p, card_apr=24.0), p) != []


@pytest.mark.parametrize("cid", PERSONAS)
def test_ten_paydays_match_fixture(fake, monkeypatch, cid):
    """Crosses several month ends (Oct has 31 days, Nov 30), so bills must recur by the calendar in both modes."""
    for n in range(10):
        live = fast_forward_to_payday(cid)
        fixture = in_mode(monkeypatch, "fixture", fast_forward_to_payday, cid)
        assert live.reasons == fixture.reasons, (cid, n)
        assert diff(get_profile(cid), in_mode(monkeypatch, "fixture", get_profile, cid)) == [], (cid, n)


def test_reset_plus_other_personas_still_recreates_the_reset_one(fake):
    from scripts.seed_nessie import main
    main(["--reset", "sam", "alex"])
    assert "sam" in nessie.load_ids()
    assert get_profile("sam").checking_balance == 575


def test_failed_reset_keeps_the_old_persona_and_cleans_up(fake, monkeypatch):
    ids = nessie.load_ids()
    ids["_spares"].pop("sam")            # no spare, so reset takes the slow rebuild path
    nessie.save_ids(ids)
    old = nessie.load_ids()["sam"]
    before = get_profile("sam")
    accounts_before = set(fake.accounts)
    real = fake.request

    def flaky(method, path, body=None, params=None):
        if method == "POST" and path.endswith("/deposits"):
            raise nessie.NessieError(method, path, 500, {"message": "boom"})
        return real(method, path, body, params)

    monkeypatch.setattr(nessie, "_send", flaky)
    assert reset_persona("sam") == before               # a 5xx is an outage: the demo resets on demo data
    assert nessie.offline_reason()
    monkeypatch.setattr(nessie, "_send", real)
    assert nessie.load_ids()["sam"] == old             # Nessie still points at the working accounts
    assert set(fake.accounts) == accounts_before        # half-made accounts were deleted
    nessie.go_online()
    assert get_profile("sam") == before                 # and live Sam is untouched


@pytest.mark.parametrize("cid,steps", [
    ("sam", ["payday_fund", "car_repair", "raise", "market_drop", "payday"]),
    ("jordan", ["car_repair", "car_repair", "car_repair", "payday"]),   # card spill
    ("alex", ["payday", "payday", "market_drop"]),
])
def test_activity_feed_matches_fixture(fake, monkeypatch, cid, steps):
    from core.portfolio import build_portfolio
    fund = build_portfolio(2, [])
    for s in steps:
        if s == "payday_fund":
            step = lambda c: fast_forward_to_payday(c, fund)
        elif s == "payday":
            step = fast_forward_to_payday
        else:
            step = lambda c, s=s: apply_life_event(c, s)
        step(cid)
        in_mode(monkeypatch, "fixture", step, cid)
    live = nessie.recent_activity(cid, limit=None)
    fixture = in_mode(monkeypatch, "fixture", nessie.recent_activity, cid, None)
    assert live == fixture
    assert len(live) > 20


def test_live_survives_a_restart_that_wipes_local_state(fake, monkeypatch):
    """Render's free plan wipes local files on restart; the sim clock and raises must come from Nessie."""
    fast_forward_to_payday("sam")
    apply_life_event("sam", "raise")
    before = get_profile("sam")
    state.clear()                    # what a restart does to data/state.json
    nessie.clear_cache()
    assert get_profile("sam") == before
    live = fast_forward_to_payday("sam")
    in_mode(monkeypatch, "fixture", fast_forward_to_payday, "sam")
    in_mode(monkeypatch, "fixture", apply_life_event, "sam", "raise")
    fixture = in_mode(monkeypatch, "fixture", fast_forward_to_payday, "sam")
    assert (live.paycheck_detected, live.invested_amount) == (fixture.paycheck_detected, fixture.invested_amount)
    assert_same(monkeypatch, "sam")


def test_clock_and_paycheck_are_read_from_nessie(fake):
    from core.profile import live_clock
    assert live_clock("sam") == (state.SIM_START, 2050)
    fast_forward_to_payday("sam")
    apply_life_event("sam", "raise")
    assert live_clock("sam") == (state.SIM_START + timedelta(days=1), 2250)


# ---------- Nessie going down mid-demo ----------

def _nessie_goes_down(monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("venue wifi died")
    monkeypatch.setattr(nessie, "_send", down)
    nessie.clear_cache()


def test_buttons_keep_working_when_nessie_goes_down(fake, monkeypatch):
    fast_forward_to_payday("sam")                      # live, on Nessie
    apply_life_event("sam", "raise")
    shown = get_profile("sam")
    _nessie_goes_down(monkeypatch)
    assert get_profile("sam") == shown                 # last numbers on screen, no jump
    assert nessie.offline_reason()                     # the app knows it's running on demo data now
    after = apply_life_event("sam", "car_repair")      # a button press with Nessie down works
    assert after.cash_cushion == shown.cash_cushion - 600
    r = fast_forward_to_payday("sam")                  # and payday too
    assert r.paycheck_detected == 2250
    # it carries on exactly like demo mode would from the same numbers
    for step in (lambda c: fast_forward_to_payday(c), lambda c: apply_life_event(c, "car_repair"),
                 lambda c: apply_life_event(c, "raise")):
        in_mode(monkeypatch, "fixture", step, "sam")
    nessie.go_online()
    in_mode(monkeypatch, "fixture", nessie.reset, "sam")


def test_failure_on_first_click_falls_back_too(fake, monkeypatch):
    before = get_profile("sam")
    _nessie_goes_down(monkeypatch)
    r = fast_forward_to_payday("sam")
    assert (r.planned_amount, r.invested_amount) == (150, 50)   # the demo line still works offline
    assert get_profile("sam").checking_balance == before.checking_balance - 35 + 2050
    nessie.go_online()


def test_bugs_in_our_requests_are_not_hidden(fake, monkeypatch):
    def bad_request(*a, **k):
        raise nessie.NessieError("POST", "/accounts/x/deposits", 400, "amount: field required")
    get_profile("sam")
    monkeypatch.setattr(nessie, "_send", bad_request)
    nessie.clear_cache()
    with pytest.raises(nessie.NessieError):
        apply_life_event("sam", "raise")
    assert not nessie.offline_reason()


def test_app_buttons_answer_200_when_nessie_is_down(fake, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/p/sam").status_code == 200
    _nessie_goes_down(monkeypatch)
    for event in ("payday", "car_repair", "raise", "market_drop"):
        assert c.post(f"/p/sam/event/{event}").status_code == 200, event
    assert c.get("/p/alex").status_code == 200        # other personas too
    nessie.go_online()


# ---------- fast reset (pre-built spares) ----------

def test_seed_builds_a_spare_for_each_persona(fake):
    spares = nessie.load_ids()["_spares"]
    assert sorted(spares) == sorted(PERSONAS)
    assert all(spares[c]["accounts"]["checking"] != nessie.load_ids()[c]["accounts"]["checking"] for c in PERSONAS)


def test_reset_swaps_in_the_spare_without_rebuilding(fake, monkeypatch):
    start = get_profile("sam")
    fast_forward_to_payday("sam")
    apply_life_event("sam", "market_drop")
    spare = nessie.load_ids()["_spares"]["sam"]
    old = nessie.load_ids()["sam"]
    seed.PAUSE_BACKGROUND = True                      # hold the rebuild so we can see the fast path alone
    try:
        n = len(fake.calls)
        assert reset_persona("sam") == start
        posts = [c for c in fake.calls[n:] if c[0] == "POST"]
        assert posts == []                            # nothing rebuilt on the click itself
        assert nessie.load_ids()["sam"] == spare
    finally:
        seed.PAUSE_BACKGROUND = False
    seed.wait_for_background()                         # then: old accounts deleted, a new spare built
    ids = nessie.load_ids()
    assert set(old["accounts"].values()).isdisjoint(fake.accounts)
    assert ids["_spares"]["sam"] not in (spare, old) and ids["_spares"]["sam"]["accounts"]["checking"] in fake.accounts
    assert get_profile("sam") == start


def test_reset_without_a_spare_rebuilds(fake):
    ids = nessie.load_ids()
    ids["_spares"].pop("sam")
    nessie.save_ids(ids)
    start = get_profile("sam")
    fast_forward_to_payday("sam")
    assert reset_persona("sam") == start
    seed.wait_for_background()
    assert "sam" in nessie.load_ids()["_spares"]        # and a spare is ready for next time


def test_badge_says_when_nessie_dropped_out(fake, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert "Live: Capital One Nessie" in c.get("/p/sam").text
    _nessie_goes_down(monkeypatch)
    page = c.post("/p/sam/event/payday").text
    assert "Nessie offline" in page and "Live: Capital One Nessie" not in page


# ---------- fixes from the 2026-10-24 review ----------

def test_offline_reason_never_contains_the_api_key(fake, monkeypatch):
    """requests puts the URL (with ?key=) in its error messages; that text reaches the page badge."""
    monkeypatch.setenv("NESSIE_API_KEY", "SECRETKEY123")

    def down(*a, **k):
        raise requests.ConnectionError("HTTPSConnectionPool: Max retries exceeded with url: "
                                       "/accounts/a1/deposits?key=SECRETKEY123")
    monkeypatch.setattr(nessie, "_send", down)
    nessie.clear_cache()
    get_profile("sam")
    assert nessie.offline_reason() and "SECRETKEY123" not in nessie.offline_reason()


def test_two_raises_in_one_day_match_fixture(fake, monkeypatch):
    for _ in range(2):
        apply_life_event("sam", "raise")
        in_mode(monkeypatch, "fixture", apply_life_event, "sam", "raise")
    assert_same(monkeypatch, "sam")
    live = fast_forward_to_payday("sam")
    fixture = in_mode(monkeypatch, "fixture", fast_forward_to_payday, "sam")
    assert live.paycheck_detected == fixture.paycheck_detected == 2450


def test_outage_right_after_the_paycheck_lands_still_completes_payday(fake, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    before = get_profile("sam")
    st = {"down": False}

    def flaky(method, path, body=None, params=None):
        if st["down"]:
            raise requests.ConnectionError("venue wifi died")
        r = fake.request(method, path, body, params)
        if method == "POST" and body and body.get("description") == "[payroll] paycheck":
            st["down"] = True
        return r
    monkeypatch.setattr(nessie, "_send", flaky)
    r = TestClient(app, raise_server_exceptions=False).post("/p/sam/event/payday")
    assert r.status_code == 200 and "invested $50 of $150" in r.text
    assert get_profile("sam").checking_balance == before.checking_balance - 35 + 2050


def test_a_one_off_read_failure_does_not_drop_the_click(fake, monkeypatch):
    before = get_profile("sam")
    n = {"fail": 1}

    def blip(method, path, body=None, params=None):
        if method == "GET" and n["fail"]:
            n["fail"] -= 1
            raise nessie.NessieError(method, path, 503, "Service Unavailable")
        return fake.request(method, path, body, params)
    monkeypatch.setattr(nessie, "_send", blip)
    nessie.clear_cache()
    after = apply_life_event("sam", "car_repair")
    assert after.cash_cushion == before.cash_cushion - 600       # the screen shows the click


def test_stale_ids_after_a_restart_fall_back_instead_of_500(fake, monkeypatch):
    """Render restores the committed nessie_ids.json on restart, which can point at deleted accounts."""
    from fastapi.testclient import TestClient
    from app.main import app
    committed = nessie.IDS_PATH.read_text()
    reset_persona("sam")
    seed.wait_for_background()                                   # old accounts deleted
    nessie.IDS_PATH.write_text(committed)                        # the restart
    nessie.clear_cache()
    r = TestClient(app, raise_server_exceptions=False).get("/p/sam")
    assert r.status_code == 200
    assert nessie.offline_reason() and "re-seed" in nessie.offline_reason()


def test_ids_file_is_never_read_half_written(fake):
    import threading
    stop, errors = threading.Event(), []

    def writer():
        ids = nessie.load_ids()
        while not stop.is_set():
            nessie.save_ids(ids)
    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(3000):
            try:
                nessie.load_ids()["sam"]
            except Exception as e:   # noqa: BLE001
                errors.append(e)
    finally:
        stop.set()
        t.join()
    assert errors == []


def test_a_corrupt_cache_file_is_ignored(fake):
    profile.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (profile.CACHE_DIR / "sam.json").write_text('{"customer_id": "sam", "not_a_field": 1}')
    assert profile.cached_snapshot("sam") is None
    nessie.go_offline("test")
    assert get_profile("sam").name == "Sam"


def test_two_quick_resets_leave_no_orphan_accounts(fake):
    seed.PAUSE_BACKGROUND = True
    reset_persona("sam")
    reset_persona("sam")                                          # before the first rebuild ran
    seed.PAUSE_BACKGROUND = False
    seed.wait_for_background()
    ids = nessie.load_ids()
    referenced = {a for k, v in ids.items() if not k.startswith("_") for a in v["accounts"].values()}
    referenced |= {a for v in ids["_spares"].values() for a in v["accounts"].values()}
    assert set(fake.accounts) <= referenced


def test_bills_are_written_before_the_spending_that_moves_the_clock(fake):
    n = len(fake.calls)
    nessie.advance_days("sam", 14)                               # rent and phone fall due
    writes = [c[2]["description"] for c in fake.calls[n:] if c[0] == "POST"]
    assert writes[-1] == "[spend] day-to-day spending" and any(w.startswith("[bill]") for w in writes[:-1])



def test_a_page_read_racing_a_reset_retries_with_the_new_ids(fake, monkeypatch):
    """A request that loaded the ids just before a reset swapped them gets a 404 on the deleted
    accounts; it should re-read the ids, not take the whole app offline."""
    old = nessie.load_ids()
    reset_persona("sam")
    seed.wait_for_background()            # the old accounts are now deleted
    real_load, calls = nessie.load_ids, {"n": 0}

    def load_ids_once_stale():
        calls["n"] += 1
        return old if calls["n"] == 1 else real_load()
    monkeypatch.setattr(nessie, "load_ids", load_ids_once_stale)
    nessie.clear_cache()
    assert get_profile("sam").checking_balance == 575
    assert not nessie.offline_reason()


def test_a_read_from_another_request_mid_click_does_not_double_count(fake, monkeypatch):
    """Double-click or a second tab: a read lands after the click's Nessie write but before the
    click updates the backup copy. The backup must still count the click once."""
    from core import live
    get_profile("alex")
    real = live.record_purchase

    def click_then_other_request_reads(c, name, amt):
        real(c, name, amt)
        get_profile(c)                      # the other request's read, mid-click
    monkeypatch.setattr(live, "record_purchase", click_then_other_request_reads)
    nessie.record_purchase("alex", "Car repair", 600)
    truth = profile.derive_live("alex", nessie.load_ids()["alex"])
    assert nessie._SHADOW["alex"]["cash_cushion"] == truth.cash_cushion


def test_offline_feed_for_a_persona_not_opened_this_run_keeps_its_clicks(fake):
    """Fresh server run, Nessie down before Sam's page was opened: the cached live feed is used."""
    get_profile("sam")
    apply_life_event("sam", "car_repair")
    get_profile("sam")                                   # the cache now holds the post-click numbers and feed
    nessie._SHADOW.pop("sam")                            # as if this server run never opened Sam
    nessie.go_offline("test")
    assert get_profile("sam").cash_cushion == 8400
    assert any(i["text"] == "Car repair" for i in nessie.recent_activity("sam", None))


def test_concurrent_cache_writes_for_one_persona_never_fail(fake):
    import threading
    p = get_profile("sam")
    errors = []

    def write():
        for _ in range(300):
            try:
                profile._save_cache(p, state.SIM_START)
            except Exception as e:   # noqa: BLE001
                errors.append(e)
    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and profile.cached_snapshot("sam") is not None


# ---------- fixes from the third review (its reproductions, kept as tests) ----------


def test_first_click_after_restart_then_outage_keeps_the_click(fake, monkeypatch):
    get_profile("sam")                   # earlier run: cache holds pre-click numbers
    nessie._SHADOW.clear()               # server restart; page already open in the browser
    real_send = nessie._send
    state = {"down": False}
    real_rp = live.record_purchase
    def rp(*a):
        real_rp(*a); state["down"] = True  # Nessie drops right after the click's writes
    monkeypatch.setattr(live, "record_purchase", rp)
    def send(m, p, b, q):
        if state["down"]:
            raise nessie.requests.ConnectionError("down")
        return fake.request(m, p, b, q)
    monkeypatch.setattr(nessie, "_send", send)
    p = apply_life_event("sam", "car_repair")
    _ = ("offline:", nessie.offline_reason(), "cash_cushion shown:", p.cash_cushion,
          "car repair in feed:", any(i["text"] == "Car repair" for i in nessie.recent_activity("sam", None)))
    assert p.cash_cushion == 8400, p.cash_cushion


def test_cache_balances_and_feed_always_agree(fake, monkeypatch):
    get_profile("alex")
    real = live.record_purchase
    def click_then_other_read(c, n, a):
        real(c, n, a); get_profile(c)       # second tab reads mid-click
    monkeypatch.setattr(live, "record_purchase", click_then_other_read)
    nessie.record_purchase("alex", "Car repair", 600)
    snap = profile.cached_snapshot("alex")[0]
    feed = profile.cached_activity("alex")
    truth = profile.derive_live("alex", nessie.load_ids()["alex"])
    _ = ("cache cash_cushion", snap.cash_cushion, "truth", truth.cash_cushion,
          "feed has car repair:", any(i["text"] == "Car repair" for i in feed))
    assert any(i["text"] == "Car repair" for i in feed) == (snap.cash_cushion == truth.cash_cushion)


def test_cache_keeps_its_feed_when_a_click_is_the_first_read(fake, monkeypatch):
    get_profile("sam")
    assert profile.cached_activity("sam") is not None
    nessie._SHADOW.clear()               # restart
    nessie.advance_days("sam", 1)        # click: its pre-write read saves the cache with no shadow feed
    _ = ("cached feed after click:", profile.cached_activity("sam"))
    assert profile.cached_activity("sam") is not None


def test_reset_as_first_click_then_outage_shows_the_reset_numbers(fake, monkeypatch):
    get_profile("sam"); apply_life_event("sam", "car_repair"); get_profile("sam")
    nessie._SHADOW.clear()                    # restart
    nessie.reset("sam")                       # live reset succeeds
    def down(*a): raise nessie.requests.ConnectionError("down")
    monkeypatch.setattr(nessie, "_send", down)
    nessie.clear_cache()
    p = get_profile("sam")
    feed = nessie.recent_activity("sam", None)
    _ = ("after reset+outage: cash_cushion", p.cash_cushion, "car repair in feed:", any(i["text"] == "Car repair" for i in feed))
    assert p.cash_cushion == 9000



# ---------- fixes from the fourth review ----------

def test_reset_as_first_click_after_a_restart_fixes_stale_ids(fake):
    """Render restored an old ids file (accounts deleted, spare available): reset must still swap
    in the spare and stay live, not go offline on the pre-click read."""
    committed = nessie.IDS_PATH.read_text()
    reset_persona("sam")
    seed.wait_for_background()
    ids = json.loads(committed)
    ids["_spares"] = nessie.load_ids()["_spares"]       # the current spare survives in Nessie
    nessie.IDS_PATH.write_text(json.dumps(ids))
    nessie._SHADOW.clear()                              # restart; page already open
    nessie.clear_cache()
    assert reset_persona("sam").checking_balance == 575
    assert not nessie.offline_reason()


def test_reset_seeds_a_persona_missing_from_the_ids_file(fake):
    ids = nessie.load_ids()
    del ids["jordan"]
    nessie.save_ids(ids)
    nessie._SHADOW.clear()
    nessie.reset("jordan")                              # used to reseed; must not KeyError
    assert "jordan" in nessie.load_ids() and get_profile("jordan").checking_balance == 600
