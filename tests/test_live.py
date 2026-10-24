"""Live mode against an in-memory fake Nessie: seeding, the live profile, and the demo buttons.

The key check: after seeding, and after every button, the live profile matches the fixture
profile for the same steps (money within $1, everything else exactly).
"""
import pytest
import requests

from core import nessie, profile, seed, state
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
    assert state.as_of("sam").isoformat() == "2026-10-24"


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
    assert r.invested_amount == 50 and r.transfer_id in fake.txns["transfers"]
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
    assert get_profile("sam") == good
    with pytest.raises(requests.ConnectionError):
        get_profile("maria")   # never cached


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
    old = nessie.load_ids()["sam"]
    before = get_profile("sam")
    accounts_before = set(fake.accounts)
    real = fake.request

    def flaky(method, path, body=None, params=None):
        if method == "POST" and path.endswith("/deposits"):
            raise nessie.NessieError(method, path, 500, {"message": "boom"})
        return real(method, path, body, params)

    monkeypatch.setattr(nessie, "_send", flaky)
    with pytest.raises(nessie.NessieError):
        reset_persona("sam")
    monkeypatch.setattr(nessie, "_send", real)
    assert nessie.load_ids()["sam"] == old             # still points at the working accounts
    assert set(fake.accounts) == accounts_before        # half-made accounts were deleted
    assert get_profile("sam") == before


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
