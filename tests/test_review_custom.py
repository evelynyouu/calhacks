"""Fixes from the custom-index review: what the demo, the reasons and Claude see match the fund.  OWNER: Evelyn."""
import pytest

from core import builder, explain
from core.portfolio import build_portfolio, worst_return_for
from core.rebalance import allocate_new_money, drift
from core.spending import Spend
from tests.fund_helpers import PERSONA_ANSWERS, make_fund

BOLD = {"risk_priority": "max", "risk_range": "widest", "horizon": "15plus", "interests": {"themes": [], "unmatched": []}}


def test_alex_preset_opens_with_no_rebalance():
    from fastapi.testclient import TestClient
    from app import state
    from app.main import app
    client = TestClient(app)
    # was: 57 trades because every index company drifted a little. An index lets prices move; only a personal
    # pick that really fell behind (Alex's Costco) may still trigger a rebalance, and its rebalance is the whole fund.
    from core.report import build_report
    client.get("/p/alex")
    rep = build_report(state.get("alex").fund)
    index_only = set(state.get("alex").fund.portfolio.parts["us_core"]) - {"COST", "AMZN", "TSLA", "AAPL", "MSFT",
                                                                        "LLY", "JNJ"}
    for r in rep["recommendations"]:
        assert not {t["what"] for t in r.get("trigger", [])} & index_only, r["title"]
    client.post("/p/alex/paycheck")
    assert len(state.get("alex").fund.pending) < 5


def test_price_moves_inside_the_us_index_are_not_drift():
    p = build_portfolio(3, [])
    held = {t: w * 10_000 for t, w in ((h.ticker, h.weight) for h in p.holdings)}
    held["NVDA"] *= 1.5                                              # NVIDIA rallies, the rest of the index doesn't
    total = sum(held.values())
    d = drift(p, held)
    # nothing inside the index trades against the rest of it: its companies drift only as the index does, in
    # proportion to their size (the index is now overweight against bonds, so they share that equally)
    k = d["NVDA"] / (held["NVDA"] / total)                           # the index's own drift, per unit of weight
    assert all(abs(d[t] - k * held[t] / total) <= 1.5e-4 for t in p.parts["us_core"])   # drift() rounds to 4 dp
    assert d["BND"] < 0                                              # the stock/bond split still drifts


def test_max_drop_counts_the_us_index_as_the_market_and_alex_is_back_to_30_percent_bonds():
    p = builder.build(PERSONA_ANSWERS["alex"])
    assert (p.score, p.parts["bonds"]["BND"]) == (67, 0.3)
    plain = build_portfolio(4, [], bond_share=0.10)
    assert plain.worst_year_return == pytest.approx(0.1 * 6.87 + 0.27 * -44.07 + 0.63 * -36.99, abs=0.15)


def test_the_ceiling_and_the_built_fund_agree_exactly():
    spend = [Spend(f"Shop {t}", "Shopping", d, t, "brand") for t, d in
             [("COST", 900), ("TGT", 800), ("SBUX", 700), ("MCD", 600), ("UBER", 500), ("NKE", 400), ("DIS", 300), ("EBAY", 200)]]
    for d in range(15, 51, 5):
        a = {**BOLD, "max_drop": d, "stocks": ["NVDA", "TSLA"]}
        p = builder.build(a, spending=spend)
        assert p.worst_year_return >= -d or p.parts["bonds"]["BND"] == 0.65, d
        assert p.worst_year_return == worst_return_for(p.risk_level, [], ["NVDA", "TSLA"],
                                                       bond_share=p.parts["bonds"]["BND"], spending=spend)


def test_spend_reasons_name_every_merchant_with_its_own_dollars():
    p = build_portfolio(3, [], spending=[Spend("McDonald's", "Restaurants", 550.0, "MCD", "brand"),
                                         Spend("Corner Cafe", "Restaurants", 549.0, None, "category")])
    mcd = next(r.text for r in p.reasons if r.kind == "spend" and r.ticker == "MCD")
    assert "$550 at McDonald's" in mcd and "$183 of your spending at Corner Cafe" in mcd
    sbux = next(r.text for r in p.reasons if r.kind == "spend" and r.ticker == "SBUX")
    assert "its category" not in sbux and "Corner Cafe" in sbux


def test_no_paycheck_order_is_under_a_dollar():
    p = build_portfolio(1, ["staples"], spending=[Spend("Walmart", "Groceries", 500.0, "WMT", "brand")])
    buys = allocate_new_money(p, {}, 80.0)
    assert min(buys.values()) >= 1.0 and sum(buys.values()) == pytest.approx(80.0, abs=0.001)


def test_claudes_facts_describe_the_slices_not_45_zero_percent_lines():
    from agent.claude_agent import fund_facts
    s = make_fund("maria", **PERSONA_ANSWERS["maria"])
    facts = " ".join(fund_facts(s))
    assert " 0%" not in facts and "Where you spend" in facts and "Walmart" in facts
    n = len(s.fund.portfolio.parts["us_core"])          # Walmart's index part went to the 8% cap, so 54 here
    assert "isn't a prediction" in facts and f"{n} companies" in facts


def test_benchmark_is_described_as_the_plain_mix():
    from core.report import build_report
    from tests.test_report import account
    facts = " ".join(build_report(account(starting_amount=5_000, started_days_ago=60))["facts"])   # a running fund
    assert "VTI/VXUS/BND" not in facts and "plain" in facts


def test_slices_list_each_companys_part_in_every_slice_it_is_in():
    s = make_fund("maria", **PERSONA_ANSWERS["maria"])
    for sl in explain.build(s.fund)["slices"]:
        assert sum(h["weight"] for h in sl["holdings"]) == pytest.approx(sl["weight"], abs=2e-4), sl["key"]
    names = {sl["key"]: {h["ticker"] for h in sl["holdings"]} for sl in explain.build(s.fund)["slices"]}
    assert "WMT" in names["spend"] and "WMT" in names["interest"]      # Walmart's index part went to the 8% cap
    assert "COST" in names["interest"] and "COST" in names["us_core"]   # Costco: an interest pick and an index company


def test_drop_reason_says_single_companies_are_estimated():
    for d in (5, 25, 50):
        p = builder.build({**BOLD, "max_drop": d}, spending=[Spend("Target", "Shopping", 400.0, "TGT", "brand")])
        text = next(r.text for r in p.reasons if r.kind == "drop")
        assert "estimated" in text and "worst real year" not in text, text
