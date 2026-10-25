"""The custom index's remaining edge cases: caps that hold after every adjustment, an index that stays an
index, seeded spending that matches the stated shares, and a note for a customer with no purchases.  OWNER: Evelyn."""
import pytest

from core import nessie, seed
from core.portfolio import CORE_NAME_CAP, MIN_INDEX_SHARE, US_SHARE, build_portfolio
from core.spending import Spend, spending_profile

EIGHT = [Spend(f"Shop {t}", "Shopping", d, t, "brand") for t, d in
         [("AAPL", 900), ("TGT", 800), ("SBUX", 700), ("MCD", 600), ("UBER", 500), ("NKE", 400), ("DIS", 300), ("EBAY", 200)]]


def test_no_index_company_passes_6_percent_of_the_index_after_the_8_percent_cap():
    p = build_portfolio(2, ["tech"], ["AAPL"], spending=EIGHT, bond_share=0.50)
    core = p.parts["us_core"]
    assert max(core.values()) <= CORE_NAME_CAP * sum(core.values()) + 1e-4


def test_the_us_index_keeps_at_least_a_third_of_the_us_stocks():
    many = ["tech", "healthcare", "financials", "oil_gas", "utilities", "staples", "industrials", "real_estate"]
    p = build_portfolio(4, many, ["TSLA", "NVO", "AMD", "PLTR", "CRWD", "NFLX", "DIS"], spending=EIGHT, bond_share=0.25)
    us = (1 - 0.25) * US_SHARE
    assert sum(p.parts["us_core"].values()) >= MIN_INDEX_SHARE * us - 1e-4
    assert len(p.parts["us_core"]) == 55
    assert any(r.kind == "cap" and r.ticker is None and "your US index" in r.text for r in p.reasons)


def test_seeded_spending_matches_each_merchants_share_in_dollars():
    for cid in ("maria", "jordan", "sam", "alex"):
        shares = {m["name"]: m["share"] for m in nessie.persona_seed(cid)["merchants"]}
        profile = {s.merchant: s.dollars for s in spending_profile(cid)}
        total = sum(profile.values())
        for name, share in shares.items():
            assert profile[name] / total == pytest.approx(share, abs=0.01), (cid, name)


def test_a_customer_the_bank_does_not_know_gets_the_no_purchases_note():
    from core import fund
    from core.fund import FundAccount
    a = FundAccount("nobody", answers={"risk_priority": "steady", "risk_range": "medium", "max_drop": 25,
                                        "horizon": "15plus", "interests": {"themes": [], "unmatched": []}})
    fund.build(a)
    assert any(r.kind == "spend" and r.facts.get("empty") for r in a.portfolio.reasons)
