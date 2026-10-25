"""Simulated market, fund builder, broker, paychecks and the report.  OWNER: Lance."""
from datetime import timedelta

import pytest

from core import fund, market
from core.broker import SimBroker
from core.fund import FundAccount
from core.portfolio import MIN_TILT, TILT_CAP_BY_RISK, build_portfolio
from core.rebalance import rebalance_trades
from core.report import build_report
from core.state import SIM_START
from tests.fund_helpers import ANSWERS

# ---------- market ----------


def test_prices_are_deterministic_and_anchored_to_the_csv():
    assert market.price("VTI", SIM_START) == round(float(market.ETFS["VTI"]["price"]), 2)
    assert market.price("NVDA", SIM_START) == round(float(market.COMPANIES["NVDA"]["price"]), 2)
    later = SIM_START + timedelta(days=40)
    assert market.raw_price("XLK", later) == market.raw_price("XLK", later)


def test_shock_moves_stocks_by_beta_and_barely_moves_bonds():
    day = SIM_START + timedelta(days=10)
    vti, bnd, smh = (market.raw_price(t, day) for t in ("VTI", "BND", "SMH"))
    market.add_shock(day, -10)
    assert market.raw_price("VTI", day) / vti == pytest.approx(0.9)
    assert market.raw_price("BND", day) / bnd > 0.99
    assert market.raw_price("SMH", day) / smh < 0.9           # beta 1.5 falls further
    assert market.raw_price("VTI", day - timedelta(days=1)) != pytest.approx(vti * 0.9)   # the past is unchanged


# ---------- portfolio ----------


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_weights_add_up_and_interests_stay_within_the_cap(level):
    # Each interest now gets up to 8% (less if it swings more), so two interests no longer fill the cap.
    p = build_portfolio(level, ["tech", "healthcare"])
    assert sum(h.weight for h in p.holdings) == pytest.approx(1.0, abs=1e-4)
    assert 2 * MIN_TILT <= p.tilt_share <= TILT_CAP_BY_RISK[level] + 1e-4


def test_too_many_interests_are_trimmed_with_a_note():
    themes = ["tech", "healthcare", "biotech", "financials", "real_estate", "gold", "gaming", "utilities"]
    p = build_portfolio(1, themes)
    # interests are companies now (several per interest), so count the interests left out, not holdings
    left_out = [r for r in p.reasons if r.kind == "dropped"]
    assert len(themes) - len(left_out) == round(TILT_CAP_BY_RISK[1] / MIN_TILT)
    assert any("left out" in w for w in p.overlap_warnings)


def test_a_company_held_for_two_reasons_is_one_position_and_says_so():
    # interests are companies now, so "two funds hold the same company" became "one company, two reasons"
    p = build_portfolio(3, ["tech", "ai_chips"])
    assert sum(h.ticker == "NVDA" for h in p.holdings) == 1
    assert any(r.kind == "combined" and r.ticker == "NVDA" for r in p.reasons)


def test_single_companies_are_capped_and_come_out_of_stocks():
    p = build_portfolio(2, ["tech"], ["aapl", "NVDA", "MSFT", "AMZN", "META"])
    added = p.parts["added"]                      # the added part only; these companies are also in the US index
    assert set(added) == {"AAPL", "NVDA", "MSFT", "AMZN", "META"}             # no count cap; all five included
    assert sum(added.values()) == pytest.approx(0.08, abs=1e-3)
    assert next(h for h in p.holdings if h.ticker == "BND").weight == 0.45      # bonds untouched
    held = {h.ticker: h for h in p.holdings}
    assert all(held[t].expense_ratio == 0 for t in added)


def test_one_company_gets_at_most_5_percent():
    p = build_portfolio(4, [], ["AAPL"])
    assert p.parts["added"]["AAPL"] == 0.05       # the added part; Apple's US-index part comes on top


def test_no_interests_is_just_the_core():
    from core.universe import core_companies
    p = build_portfolio(2, [])
    assert [h.ticker for h in p.holdings] == ["BND", "VXUS", *core_companies()]   # bonds, international, the US index


def test_rebalance_sells_dropped_funds_and_nets_to_zero():
    p = build_portfolio(2, [])
    trades = rebalance_trades(p, {"VTI": 500, "VXUS": 100, "BND": 300, "XLK": 100})
    assert trades["XLK"] == -100
    assert sum(trades.values()) == pytest.approx(0, abs=0.05)


# ---------- broker ----------


def test_sim_broker_buys_and_caps_sells_at_what_you_own():
    b = SimBroker()
    buy = b.submit("x", "VTI", "buy", 600, SIM_START)
    assert buy.status == "filled" and b.positions("x")["VTI"] == pytest.approx(600 / market.raw_price("VTI", SIM_START))
    sell = b.submit("x", "VTI", "sell", 10_000, SIM_START)
    assert sell.notional == pytest.approx(600) and "VTI" not in b.positions("x")


# ---------- fund + paychecks ----------


def _account(**answers) -> FundAccount:
    a = FundAccount("sam", answers={**ANSWERS, **answers})
    fund.start(a)
    return a


def test_starting_amount_is_invested_on_target():
    a = _account(starting_amount=10_000)
    assert fund.value(a) == pytest.approx(10_000, abs=0.01)
    assert a.contributed == 10_000


def test_paycheck_invests_the_chosen_percent_in_whole_dollars():
    a = _account(paycheck_pct=15)
    d = fund.paycheck(a)
    assert d["paycheck"] == 2050 and d["invested"] == 307     # floor(2050 * 15%)
    assert sum(t.notional for t in d["trades"]) == pytest.approx(307, abs=0.05)
    assert a.today > SIM_START


def test_zero_percent_invests_nothing():
    a = _account(paycheck_pct=0)
    assert fund.paycheck(a)["invested"] == 0


def test_auto_review_queues_trades_only_when_allowed_and_due():
    a = _account(starting_amount=8_000, interests={"themes": ["ai_chips"], "unmatched": []})   # tier 2: monthly
    market.add_shock(a.today, 30)            # chips run far ahead of target
    a.auto_trade = True
    fund.paycheck(a)
    assert a.pending and a.last_review == a.today
    first = list(a.pending)
    fund.paycheck(a)                          # 14 days later: not due yet at a monthly review
    assert a.pending == first
    done = fund.confirm_pending(a)
    assert done[0].side == "sell" and not a.pending


# ---------- report ----------


def test_report_adds_up():
    a = _account(starting_amount=5_000)
    a.today += timedelta(days=60)
    rep = build_report(a)
    s = rep["summary"]
    assert s["value"] == pytest.approx(sum(h["value"] for h in rep["holdings"]), abs=0.05)
    assert s["gain"] == pytest.approx(s["value"] - s["contributed"], abs=0.01)
    assert sum(h["weight"] for h in rep["holdings"]) == pytest.approx(1, abs=1e-3)
    assert rep["companies"] and rep["companies"] == sorted(rep["companies"], key=lambda c: -c["dollars"])
    assert rep["history"]["value"][-1] == pytest.approx(s["value"], abs=0.05)


def test_quiet_market_says_stay_the_course():
    rep = build_report(_account(interests={"themes": ["healthcare"], "unmatched": []}))
    assert [r["action"] for r in rep["recommendations"]] == ["hold"]


def test_drift_above_5k_offers_rebalance_trades():
    a = _account(starting_amount=20_000, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today, 30)
    rec = next(r for r in build_report(a)["recommendations"] if r["action"] == "rebalance")
    assert {t["side"] for t in rec["trades"]} == {"buy", "sell"}


def test_drift_below_5k_steers_new_money_instead_of_selling():
    a = _account(starting_amount=3_000, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today, 30)
    rec = next(r for r in build_report(a)["recommendations"] if r["action"] == "steer")
    assert rec["trades"] == []


def test_market_dip_suggests_investing_more():
    a = _account(paycheck_pct=10)
    market.add_shock(a.today, -12)
    rec = next(r for r in build_report(a)["recommendations"] if r["action"] == "invest_more")
    assert rec["set_pct"] == 15


def test_tech_heavy_picks_suggest_steadier_sectors():
    a = _account(etf_mode=True, interests={"themes": ["tech", "ai_chips", "software"], "unmatched": []})
    rec = next(r for r in build_report(a)["recommendations"] if r["action"] == "explore")
    assert len(rec["add_interest"]) == 2


def test_high_paycheck_share_suggests_checking_cash():
    rec = next(r for r in build_report(_account(paycheck_pct=60))["recommendations"] if r["action"] == "invest_less")
    assert rec["set_pct"] == 50
