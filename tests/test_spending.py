"""Where the customer spends: Nessie purchases -> companies.  OWNER: Evelyn."""
import pytest
import requests

from core import nessie, spending
from core.payday import apply_life_event
from core.spending import CATEGORY_LEADERS, Spend, company_spend, spending_profile
from tests.test_live import fake, in_mode  # noqa: F401


def by_merchant(profile):
    return {s.merchant: s for s in profile}


def test_brand_purchases_map_to_their_company():
    p = by_merchant(spending_profile("sam"))
    assert (p["Target"].ticker, p["Target"].via) == ("TGT", "brand") and p["Target"].dollars > 0
    assert (p["Netflix"].ticker, p["Netflix"].via) == ("NFLX", "brand")


def test_private_merchants_fall_back_to_their_category():
    tj = by_merchant(spending_profile("sam"))["Trader Joe's"]
    assert (tj.ticker, tj.via, tj.category) == (None, "category", "Groceries")
    spend = company_spend([Spend("Berkeley Bowl", "Groceries", 300.0, None, "category")])
    assert set(spend) == set(CATEGORY_LEADERS["Groceries"]) and sum(spend.values()) == pytest.approx(300)


def test_profile_covers_the_seeded_spending_and_is_sorted():
    profile = spending_profile("sam")
    seeded = 2 * nessie.persona_seed("sam")["monthly_spending"]          # 8 weekly purchases = two months
    assert sum(s.dollars for s in profile) == pytest.approx(seeded, abs=1)
    assert [s.dollars for s in profile] == sorted((s.dollars for s in profile), reverse=True)
    assert len({s.merchant for s in profile}) == len(profile)


def test_one_off_events_dont_count():
    before = sum(s.dollars for s in spending_profile("sam"))
    apply_life_event("sam", "car_repair")
    assert sum(s.dollars for s in spending_profile("sam")) == pytest.approx(before)
    assert "Mike's Auto" not in by_merchant(spending_profile("sam"))


def test_fast_forward_spending_goes_to_the_usual_places_and_old_purchases_age_out():
    shares = {m["name"]: m["share"] for m in nessie.persona_seed("sam")["merchants"]}
    for _ in range(7):                                                   # 98 days: every seeded purchase is older than 90
        nessie.advance_days("sam", 14)
    p = by_merchant(spending_profile("sam"))
    total = sum(s.dollars for s in p.values())
    assert set(p) == set(shares)
    for name, share in shares.items():
        assert p[name].dollars / total == pytest.approx(share, abs=0.02)


def test_live_and_fixture_profiles_match(fake, monkeypatch):
    assert in_mode(monkeypatch, "fixture", spending_profile, "sam") == spending_profile("sam")
    nessie.advance_days("sam", 14)
    in_mode(monkeypatch, "fixture", nessie.advance_days, "sam", 14)
    assert in_mode(monkeypatch, "fixture", spending_profile, "sam") == spending_profile("sam")


def test_live_purchases_carry_the_brand_merchants(fake):
    names = {m["name"] for m in fake.merchants.values()}
    assert {"Target", "Trader Joe's", "Netflix", "Costco", "Walmart"} <= names


def test_nessie_down_falls_back_like_the_rest_of_the_app(fake, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(nessie, "_send", down)
    nessie.clear_cache()
    assert spending_profile("sam")                                       # demo data, no exception
