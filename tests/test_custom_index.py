"""The customer's own index: ~55 US companies by size, VXUS and BND, plus where they spend, their
interests and companies they added; one position per company, none at 8% of the fund.  OWNER: Evelyn."""
import pytest

from core import universe
from core.portfolio import (COMPANY_CAP, CORE_NAME_CAP, INTL_SHARE, MIN_PICK, SPEND_CAP_BY_RISK, SPEND_PICKS,
                            THEME_PICKS, build_portfolio)
from core.spending import Spend

TARGET = Spend("Target", "Shopping", 600.0, "TGT", "brand")
SBUX = Spend("Starbucks", "Restaurants", 200.0, "SBUX", "brand")


def weights(p):
    return {h.ticker: h.weight for h in p.holdings}


def reasons(p, kind, ticker="any"):
    return [r for r in p.reasons if r.kind == kind and (ticker == "any" or r.ticker == ticker)]


# ---------- the US index ----------

def test_core_is_individual_companies_weighted_by_size_and_capped():
    p = build_portfolio(3, [])
    core = p.by_slice()["us_core"]
    assert [h.ticker for h in core] == universe.core_companies()
    assert all(h.sector in universe.SECTORS and h.role == "core" for h in core)
    us = p.slice_weights()["us_core"]
    assert max(h.weight for h in core) <= CORE_NAME_CAP * us + 1e-4
    assert core[0].weight > core[-1].weight


def test_bonds_and_international_stay_funds_at_their_shares():
    p = build_portfolio(2, [])
    w = p.slice_weights()
    assert w["bonds"] == pytest.approx(0.45) and w["intl"] == pytest.approx(0.55 * INTL_SHARE, abs=1e-3)
    assert [h.ticker for h in p.by_slice()["intl"]] == ["VXUS"] and [h.ticker for h in p.by_slice()["bonds"]] == ["BND"]
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-4) and list(w) == [k for k, _ in __import__("core.models", fromlist=["SLICES"]).SLICES]


def test_every_core_company_has_a_reason_naming_its_sector():
    p = build_portfolio(3, [])
    for h in p.by_slice()["us_core"]:
        r = next(r for r in reasons(p, "core", h.ticker))
        assert h.sector in r.text


# ---------- where you spend ----------

def test_spend_slice_follows_dollars_within_its_cap():
    p = build_portfolio(3, [], spending=[TARGET, SBUX])
    s = p.by_slice()["spend"]
    parts = {r.ticker: r.facts["weight"] for r in reasons(p, "spend") if r.ticker}
    assert {h.ticker for h in s} == {"TGT", "SBUX"}
    assert sum(parts.values()) == pytest.approx(SPEND_CAP_BY_RISK[3], abs=1e-3)
    assert parts["TGT"] == pytest.approx(3 * parts["SBUX"], rel=0.01)
    assert "isn't a prediction" in reasons(p, "spend", "TGT")[0].text


def test_no_spending_means_no_spend_slice_and_says_so():
    p = build_portfolio(3, [], spending=[])
    assert not p.by_slice()["spend"] and reasons(p, "spend", None)
    assert p.slice_weights()["us_core"] == pytest.approx(build_portfolio(3, []).slice_weights()["us_core"], abs=1e-4)


def test_a_private_merchant_buys_its_categorys_leaders():
    p = build_portfolio(3, [], spending=[Spend("Trader Joe's", "Groceries", 400.0, None, "category")])
    assert {h.ticker for h in p.by_slice()["spend"]} | {"WMT", "COST"} >= {"WMT", "COST", "KR"}
    assert any("Trader Joe's" in r.text and "isn't a public company" in r.text for r in reasons(p, "spend"))


def test_tiny_spend_picks_are_dropped_and_at_most_8_are_kept():
    many = [Spend(f"Shop {t}", "Shopping", d, t, "brand")
            for t, d in [("TGT", 900), ("NKE", 800), ("SBUX", 700), ("MCD", 600), ("UBER", 500), ("DIS", 400),
                         ("EBAY", 300), ("ABNB", 200), ("DASH", 100), ("YUM", 1)]]
    p = build_portfolio(3, [], spending=many)
    kept = [r for r in reasons(p, "spend") if r.ticker and not r.facts.get("dropped")]
    assert len(kept) <= SPEND_PICKS and all(r.facts["weight"] >= MIN_PICK for r in kept)
    assert {r.ticker for r in reasons(p, "spend") if r.facts.get("dropped")} >= {"YUM"}


# ---------- interests, added companies, one position each ----------

def test_interest_becomes_its_biggest_companies_or_its_fund_if_none():
    p = build_portfolio(4, ["clean_energy", "gold"])
    interest = {h.ticker for h in p.by_slice()["interest"]}
    assert interest == {*universe.theme_companies("clean_energy", THEME_PICKS), "GLD"}


def test_a_company_in_two_slices_is_one_position_with_both_reasons():
    # Costco is half the spending (5% of the fund) on top of its US-index part: under the cap, so both stay
    p = build_portfolio(3, [], spending=[Spend("Costco", "Groceries", 500.0, "COST", "brand"), Spend("Target", "Shopping", 500.0, "TGT", "brand")])
    assert sum(h.ticker == "COST" for h in p.holdings) == 1
    cost = next(h for h in p.holdings if h.ticker == "COST")
    assert cost.slice == "spend" and reasons(p, "core", "COST") and reasons(p, "spend", "COST")


def test_no_company_reaches_8_percent_of_the_fund_across_slices():
    p = build_portfolio(4, ["tech"], ["AAPL"], spending=[Spend("Apple Store", "Electronics", 5000.0, "AAPL", "brand")])
    assert max(w for t, w in weights(p).items() if t in universe.COMPANIES) < COMPANY_CAP
    assert reasons(p, "cap", "AAPL")


def test_weights_add_up_and_tickers_are_unique():
    p = build_portfolio(4, ["tech", "healthcare", "gold"], ["TSLA", "NVO"], spending=[TARGET, SBUX])
    assert sum(weights(p).values()) == pytest.approx(1.0, abs=1e-4)
    assert len(weights(p)) == len(p.holdings) and min(weights(p).values()) > 0


# ---------- wired into the app ----------

def test_a_customers_fund_uses_their_spending_from_the_bank():
    from tests.fund_helpers import make_fund
    s = make_fund("sam")
    spend = {h.ticker for h in s.fund.portfolio.by_slice()["spend"]}
    assert "TGT" in spend and "NFLX" in spend                    # Sam shops at Target and pays for Netflix
    assert any("Trader Joe's" in r.text for r in s.fund.portfolio.reasons)


def test_builder_passes_spending_through_its_limits():
    from core import builder
    a = {"risk_priority": "max", "risk_range": "widest", "max_drop": 20, "horizon": "15plus",
         "interests": {"themes": [], "unmatched": []}}
    p = builder.build(a, spending=[TARGET])
    assert p.by_slice()["spend"] and p.worst_year_return >= -20
