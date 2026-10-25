"""The fund report: windows, dollars, returns.  OWNER: Ethan.  (Lance's engine tests are in test_engine.py.)"""
from datetime import timedelta

import pytest

from core import fund, market
from core import report as report_module
from core.broker import get_broker
from core.fund import FundAccount
from core.models import Holding, Portfolio
from core.report import benchmark_weights, build_report
from core.rebalance import DRIFT_ABS, band, off_target
from core.state import SIM_START
from tests.fund_helpers import ANSWERS


def account(cid: str = "sam", started_days_ago: int = 0, **answers) -> FundAccount:
    a = FundAccount(cid, answers={**ANSWERS, **answers})
    fund.start(a, on=a.today - timedelta(days=started_days_ago))
    return a


def test_dollars_add_up_for_both_windows():
    a = account(starting_amount=5_000, started_days_ago=90)
    fund.paycheck(a)
    fund.paycheck(a)
    r = build_report(a)["returns"]
    for w in (r["period"], r["since_start"]):
        assert w["start_value"] + w["deposits"] + w["market_gain"] == pytest.approx(w["end_value"], abs=0.02)
    assert r["since_start"]["end_value"] == pytest.approx(fund.value(a), abs=0.01)
    assert r["since_start"]["deposits"] == pytest.approx(a.contributed, abs=0.01)


def test_period_runs_back_one_review_cadence_not_a_fixed_30_days():
    a = account("alex", started_days_ago=365, starting_amount=60_000)      # tier 4: weekly reviews
    rep = build_report(a)
    assert rep["period_days"] == 7 and rep["returns"]["period"]["start"] == a.today - timedelta(days=7)
    assert not rep["returns"]["period"]["from_start"]


def test_period_reaches_back_to_the_last_review_when_that_is_older():
    a = account(started_days_ago=200, starting_amount=3_000)               # tier 1: monthly
    a.last_review = a.today - timedelta(days=45)
    assert build_report(a)["period_days"] == 45


def test_a_fund_younger_than_the_period_says_so_instead_of_faking_30_days():
    a = account(started_days_ago=10, starting_amount=3_000)                # tier 1 cadence is 30 days
    rep = build_report(a)
    p = rep["returns"]["period"]
    assert p["from_start"] and p["days"] == 10 and p["start"] == a.today - timedelta(days=10)
    assert p["twr_pct"] == rep["returns"]["since_start"]["twr_pct"]
    assert rep["period_days"] == 10


def test_a_fund_with_nothing_in_it_has_no_returns_and_no_errors():
    a = account(starting_amount=0)
    rep = build_report(a)
    assert rep["returns"]["available"] is False and rep["returns"]["period"] is None
    assert rep["summary"]["value"] == 0 and rep["summary"]["gain"] == 0 and rep["summary"]["gain_pct"] == 0


def test_one_paycheck_and_no_starting_money_has_no_misleading_percent():
    a = account(starting_amount=0)
    fund.paycheck(a)                                                        # the first order is today: no time has passed
    w = build_report(a)["returns"]["since_start"]
    assert w["deposits"] == pytest.approx(a.contributed) and w["days"] == 0
    assert w["twr_pct"] is None and w["mwr_pct"] is None


def test_a_deposit_just_before_a_drop_shows_up_as_a_gap_between_the_two_returns():
    a = account(starting_amount=1_000, started_days_ago=30)
    fund.paycheck(a)
    market.add_shock(a.today + timedelta(days=1), -20)
    a.today += timedelta(days=2)
    w = build_report(a)["returns"]["since_start"]
    assert w["mwr_pct"] < w["twr_pct"] < 0      # the late deposit lost money too, so the customer's own return is worse


# ---------- benchmark and attribution ----------

def _portfolio(**weights) -> Portfolio:
    roles = {"VTI": "core", "VXUS": "core", "BND": "core"}
    return Portfolio(2, "Moderate", [Holding(t, t, w, roles.get(t, "tilt"), 0.1) for t, w in weights.items()],
                     0.1, 2022, -15.0)


def test_benchmark_puts_slices_back_into_the_stock_funds_and_keeps_the_bond_share():
    w = benchmark_weights(_portfolio(VTI=0.30, VXUS=0.13, BND=0.45, XLK=0.12))
    assert w["BND"] == pytest.approx(0.45) and sum(w.values()) == pytest.approx(1)
    # personal slices come out of the US part (core/portfolio.py), so they go back into US stocks
    assert w["VTI"] / w["VXUS"] == pytest.approx((0.30 + 0.12) / 0.13)


def test_a_custom_index_is_measured_against_its_plain_index():
    from core.portfolio import build_portfolio
    from core.spending import Spend
    p = build_portfolio(3, ["tech"], spending=[Spend("Target", "Shopping", 500.0, "TGT", "brand")])
    w = benchmark_weights(p)
    assert sum(w.values()) == pytest.approx(1, abs=1e-4) and w["BND"] == p.slice_weights()["bonds"]
    assert w["VXUS"] == pytest.approx(p.slice_weights()["intl"], abs=1e-4) and "TGT" not in w   # nothing personal


def test_benchmark_reads_target_weights_so_a_smooth_bond_share_works():
    w = benchmark_weights(_portfolio(VTI=0.489, VXUS=0.210, BND=0.301))
    assert w["BND"] == pytest.approx(0.301) and sum(w.values()) == pytest.approx(1)


def test_benchmark_for_a_fund_with_no_core_stocks_falls_back_to_70_30():
    w = benchmark_weights(_portfolio(BND=1.0))
    assert w == {"VTI": 0.0, "VXUS": 0.0, "BND": 1.0}


def test_benchmark_equals_the_fund_when_holdings_sit_at_core_weights_with_no_slices(monkeypatch):
    monkeypatch.setattr(fund, "_spending", lambda cid: [])              # no purchases: no "where you spend" slice
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": [], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 15)
    w = build_report(a)["returns"]["since_start"]
    assert w["benchmark_pct"] == pytest.approx(w["twr_pct"], abs=0.01) and w["vs_benchmark_pts"] == pytest.approx(0, abs=0.01)
    assert w["benchmark_value"] == pytest.approx(w["end_value"], abs=0.05)


def test_a_bond_heavy_fund_is_not_measured_against_the_s_and_p_500(monkeypatch):
    monkeypatch.setattr(fund, "_spending", lambda cid: [])              # nothing personal: the fund is its own mix
    a = account(starting_amount=10_000, started_days_ago=60, risk_priority="protect", risk_range="narrow",
                max_drop=0, horizon="lt3", interests={"themes": [], "unmatched": []})
    market.add_shock(a.today - timedelta(days=30), 20)                  # a rally the bond-heavy fund can't match
    w = build_report(a)["returns"]["since_start"]
    assert w["vs_spy_pts"] < -3                                          # it trails the S&P 500 ...
    assert abs(w["vs_benchmark_pts"]) < 0.01                             # ... but matches what its own mix would do


def test_vs_benchmark_isolates_the_customers_choices(monkeypatch):
    monkeypatch.setattr(fund, "_spending", lambda cid: [])              # only the chips interest differs from the plain index
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 30)                  # chips (beta 1.5) run ahead of the core
    w = build_report(a)["returns"]["since_start"]
    assert w["vs_benchmark_pts"] > 0 and w["twr_pct"] > w["benchmark_pct"]


def test_attribution_sums_to_the_market_gain_through_paychecks_and_a_rebalance():
    a = account(starting_amount=20_000, started_days_ago=120, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=60), 30)
    fund.paycheck(a)
    a.auto_trade = True
    fund.review_if_due(a)
    fund.confirm_pending(a)
    fund.paycheck(a)
    r = build_report(a)["returns"]
    for w in (r["period"], r["since_start"]):
        assert sum(x["dollars"] for x in w["attribution"]) == pytest.approx(w["market_gain"], abs=0.01)
    assert r["since_start"]["attribution"] == sorted(r["since_start"]["attribution"], key=lambda x: -abs(x["dollars"]))


def test_each_pick_is_compared_with_the_core_it_replaced():
    a = account(starting_amount=10_000, started_days_ago=60, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=20), 30)
    picks = build_report(a)["returns"]["since_start"]["picks"]
    pick = next(x for x in picks if x["ticker"] == "NVDA")               # chips is its biggest companies now, not SMH
    assert pick["vs_core_pts"] == pytest.approx(pick["move_pct"] - pick["core_pct"], abs=0.01)
    assert pick["vs_core_pts"] > 0 and pick["vs_core_dollars"] > 0 and pick["dollars"] > pick["vs_core_dollars"]


# ---------- price sources and income ----------

def real_closes_through(monkeypatch, last, skip=()):
    """Pretend the feed has real closes up to `last` for every ticker except `skip`."""
    days = [last - timedelta(days=n) for n in range(30, -1, -1)]

    def fake(t):
        return None if t in skip else (days, [100.0] * len(days))
    monkeypatch.setattr(market, "_real_closes", fake)


def test_label_says_everything_is_simulated_with_no_real_data():
    rep = build_report(account(starting_amount=3_000))
    assert rep["data_notes"]["price"].startswith("All prices here are simulated")
    assert all(h["simulated"] for h in rep["holdings"]) and rep["returns"]["since_start"]["uses_simulated_prices"]


def test_label_names_the_last_real_close_once_the_clock_is_past_it(monkeypatch):
    real_closes_through(monkeypatch, SIM_START - timedelta(days=3))
    a = account(starting_amount=3_000)
    a.today = SIM_START                                                  # the demo has moved 3 days past the last real close
    rep = build_report(a)
    assert rep["data_notes"]["price"].startswith("Prices after Oct 20 are simulated")
    assert rep["returns"]["since_start"]["uses_simulated_prices"] and all(h["simulated"] for h in rep["holdings"])


def test_no_simulated_label_while_the_clock_is_on_real_prices(monkeypatch):
    real_closes_through(monkeypatch, SIM_START + timedelta(days=5))
    rep = build_report(account(starting_amount=3_000))
    assert rep["data_notes"]["price"] == "Prices are real Nasdaq closing prices."
    assert not any(h["simulated"] for h in rep["holdings"]) and not rep["returns"]["since_start"]["uses_simulated_prices"]


def test_a_ticker_with_no_real_data_is_flagged_on_its_own_row(monkeypatch):
    real_closes_through(monkeypatch, SIM_START + timedelta(days=5), skip={"NTDOY"})
    a = account(starting_amount=3_000, stocks=["NTDOY"])
    rep = build_report(a)
    rows = {h["ticker"]: h["simulated"] for h in rep["holdings"]}
    assert rows["NTDOY"] and not rows["VXUS"]
    assert "NTDOY has no real price data" in rep["data_notes"]["price"]


def test_estimated_income_accrues_daily_and_stays_out_of_the_balance():
    a = account(starting_amount=10_000, started_days_ago=365, interests={"themes": [], "unmatched": []})
    rep = build_report(a)
    w = rep["returns"]["since_start"]
    assert w["start_value"] + w["deposits"] + w["market_gain"] == pytest.approx(w["end_value"], abs=0.02)   # price only
    # the fund's own yield: each holding's target weight x its yearly yield (data/yields.csv). Computed from the
    # targets because the mix now follows the answers smoothly (core/builder.py), not a fixed per-level table.
    yearly = sum(h.weight * report_module.YIELDS.get(h.ticker, 0.0) for h in a.portfolio.holdings)
    assert w["income_estimate"] == pytest.approx(10_000 * yearly, rel=0.15)
    assert w["twr_pct"] - w["twr_price_pct"] == pytest.approx(yearly * 100, abs=0.4)


# ---------- recommendations ----------

def test_bands_catch_a_small_slice_that_doubles_but_ignore_a_few_dollars():
    p = _portfolio(VTI=0.50, VXUS=0.20, BND=0.28, XLK=0.02)
    doubled = {"VTI": 4800.0, "VXUS": 2000.0, "BND": 2800.0, "XLK": 400.0}           # XLK 2% -> 4% of $10,000
    gap = off_target(p, doubled)["XLK"]
    assert gap == pytest.approx(0.02) and gap < DRIFT_ABS                              # the old 5-point rule missed it
    tiny = {"VTI": 50.0, "VXUS": 20.0, "BND": 26.0, "XLK": 4.0}                        # same shape on $100: a $2 gap
    assert "XLK" not in off_target(p, tiny)


def test_the_band_is_5_points_for_big_holdings_and_25_percent_for_small_ones():
    assert band(0.45) == DRIFT_ABS and band(0.02) == pytest.approx(0.005) and band(0.0) == 0.0


def test_a_big_holding_inside_5_points_does_not_trigger():
    p = _portfolio(VTI=0.50, VXUS=0.20, BND=0.30)
    assert off_target(p, {"VTI": 5400.0, "VXUS": 1800.0, "BND": 2800.0}) == {}         # 4 points off


def fresh() -> None:
    get_broker().reset()
    market.clear_shocks()


def all_recs(monkeypatch=None) -> list[tuple[str, dict]]:
    """A report for each situation that should raise a different recommendation."""
    out = []
    a = account(starting_amount=20_000, started_days_ago=90, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=30), 30)
    out.append(("rebalance", build_report(a)))
    fresh()
    a = account(starting_amount=3_000, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today, 30)
    out.append(("steer", build_report(a)))
    fresh()
    a = account(paycheck_pct=10)
    market.add_shock(a.today, -12)
    out.append(("invest_more", build_report(a)))
    fresh()
    a = account(started_days_ago=60, interests={"themes": ["ai_chips"], "unmatched": []})
    market.add_shock(a.today - timedelta(days=30), -30)
    out.append(("hold", build_report(a)))
    fresh()
    out.append(("invest_less", build_report(account(paycheck_pct=60))))
    fresh()
    out.append(("hold", build_report(account(interests={"themes": ["healthcare"], "unmatched": []}))))
    fresh()
    if monkeypatch:   # the builder decides how much tech a fund holds; put the fund-wide line below that so only it fires
        monkeypatch.setattr(report_module, "TECH_FUND_TRIGGER", 0.05)
    a = account(starting_amount=10_000, interests={"themes": ["tech", "financials"], "unmatched": []},
                stocks=["NVDA", "AAPL"], risk_priority="max", risk_range="widest", max_drop=50, horizon="15plus")
    out.append(("explore", build_report(a)))
    return out


def test_every_recommendation_shows_the_number_that_triggered_it_and_the_threshold(monkeypatch):
    seen = set()
    for _, rep in all_recs(monkeypatch):
        for rec in rep["recommendations"]:
            seen.add(rec["action"])
            assert rec["trigger"], rec["title"]
            for t in rec["trigger"]:
                assert t["text"] in rec["facts"]
                assert t["value_text"] in t["text"] and t["threshold_text"] in t["text"], t
    assert {"rebalance", "steer", "invest_more", "hold", "invest_less", "explore"} <= seen


def test_every_company_over_the_trigger_is_flagged_not_just_the_biggest(monkeypatch):
    monkeypatch.setattr(report_module, "CONCENTRATION_TRIGGER", 0.04)
    # a bold fund, so the companies are big enough to pass a 4% line (the default answers build 65% bonds)
    a = account(starting_amount=10_000, interests={"themes": ["tech"], "unmatched": []}, stocks=["NVDA", "AAPL", "MSFT"],
                risk_priority="max", risk_range="widest", max_drop=50, horizon="15plus")
    rep = build_report(a)
    rec = next(r for r in rep["recommendations"] if r["action"] == "diversify")
    over = [c for c in rep["companies"] if c["share"] >= 0.04]
    assert len(over) >= 2 and len(rec["trigger"]) == len(over)
    assert all(any(c["name"] in f for f in rec["facts"]) for c in over)


def test_tech_check_counts_the_whole_fund_not_just_the_interest_slice(monkeypatch):
    rep = dict(all_recs(monkeypatch))["explore"]
    rec = next(r for r in rep["recommendations"] if r["action"] == "explore")
    assert "whole fund" in rec["facts"][0] and "approximate" in rec["facts"][0]
    assert len(rec["trigger"]) == 1                    # the picks are half tech, below the 60% line: only the fund-wide check fired


def test_sector_exposure_looks_through_the_broad_funds():
    sectors = report_module._sector_exposure({"VTI": 600.0, "BND": 400.0}, 1000.0)
    assert sectors["Technology"] == pytest.approx(0.6 * 0.31) and "Bonds" not in sectors
    assert sum(sectors.values()) == pytest.approx(0.6, abs=0.001)


def test_the_dip_rec_is_about_the_funds_own_stocks_and_says_how_big_a_share_that_is():
    a = account(risk_priority="protect", risk_range="narrow", max_drop=0, horizon="lt3",
                interests={"themes": [], "unmatched": []})                         # about a third in stocks
    market.add_shock(a.today, -15)
    rep = build_report(a)
    rec = next(r for r in rep["recommendations"] if r["action"] == "invest_more")
    stocks = sum(h["weight"] for h in rep["holdings"] if h["ticker"] not in ("BND", "GLD"))
    assert f"Your stocks, {stocks:.0%} of your fund" in rec["facts"][0]
    assert any("on the whole fund" in f for f in rec["facts"])


def test_the_stock_sleeve_leaves_out_bonds_and_gold():
    holdings = [{"ticker": "VTI", "weight": 0.5}, {"ticker": "BND", "weight": 0.4}, {"ticker": "GLD", "weight": 0.1}]
    assert report_module._stock_sleeve(holdings, SIM_START)["share"] == 0.5
    assert report_module._stock_sleeve([{"ticker": "BND", "weight": 1.0}], SIM_START) is None


# ---------- the page, the tool, the facts ----------

import json                                                                  # noqa: E402
import re                                                                    # noqa: E402

from fastapi.testclient import TestClient                                     # noqa: E402

from agent import tools                                                       # noqa: E402
from app import state                                                         # noqa: E402
from app.main import app                                                      # noqa: E402
from tests.fund_helpers import make_fund                                      # noqa: E402

client = TestClient(app)


def page_text(cid: str) -> str:
    html = client.get(f"/p/{cid}/report").text
    return " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html, flags=re.S)).split())


@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_report_renders_for_every_persona(cid):
    client.get(f"/p/{cid}")                       # alex's year-old fund is created on the first visit
    if cid != "alex":
        make_fund(cid, starting_amount=4_000)
    r = client.get(f"/p/{cid}/report")
    assert r.status_code == 200 and "Your finances, and where the fund fits" in r.text and "None" not in page_text(cid).replace("None of", "")


def test_numbers_on_the_page_come_from_build_report():
    make_fund("sam", starting_amount=8_000)
    client.post("/p/sam/paycheck")
    rep = build_report(state.get("sam").fund)
    text = page_text("sam")
    p, since, fin = rep["returns"]["period"], rep["returns"]["since_start"], rep["finances"]
    for expected in (f"${rep['summary']['value']:,.2f}", f"${since['deposits']:,.2f}", f"${since['end_value']:,.2f}",
                     f"${fin['budget']['surplus']:,.0f}", f"{fin['safety']['months']:.1f}",
                     f"${rep['projection'].p50[-1]:,.0f}", f"${rep['projection_inputs']['monthly']:,.2f}"):
        assert expected in text, expected
    for h in rep["holdings"]:
        assert f"${h['value']:,.2f}" in text, h["ticker"]


def test_the_page_says_when_prices_are_simulated_and_when_a_fund_is_new():
    make_fund("sam", starting_amount=3_000)
    text = page_text("sam")
    assert "All prices here are simulated" in text and "Your fund started today" in text


def test_the_report_names_the_benchmark_it_uses():
    make_fund("sam", starting_amount=3_000)
    client.post("/p/sam/paycheck")
    assert "Your benchmark" in page_text("sam") and "isn't the fair yardstick" in page_text("sam")


def test_the_agent_tool_returns_json_with_python_numbers_only():
    s = make_fund("sam", starting_amount=5_000)
    client.post("/p/sam/paycheck")
    out = tools.report_summary(s)
    json.dumps(out)                                                           # dates and dataclasses are plain values
    rep = build_report(s.fund)
    assert out["period"]["twr_pct"] == rep["returns"]["period"]["twr_pct"]
    assert out["projection"]["typical_10y"] == rep["projection"].p50[-1] and out["facts"] == rep["facts"]
    assert out["finances"]["budget"]["per_paycheck"] == rep["finances"]["budget"]["per_paycheck"]


def test_facts_put_the_customers_own_fund_first_and_carry_the_dates_and_benchmark():
    a = account("alex", started_days_ago=365, starting_amount=60_000)
    facts = build_report(a)["facts"]
    assert facts[0].startswith("Fund value") and "plain mix" in facts[2] and f"{(a.today - timedelta(days=7)).strftime('%b')}" in facts[1]
    assert any("Looking ahead" in f for f in facts) and facts[-1].startswith("All prices here are simulated")
    assert max(i for i, f in enumerate(facts) if "Fund value" in f or "benchmark" in f) < min(i for i, f in enumerate(facts) if "market is" in f)


def test_offline_intro_reads_as_two_plain_paragraphs_from_the_same_numbers():
    a = account("alex", started_days_ago=365, starting_amount=60_000)
    rep = build_report(a)
    first, second = rep["narrative"].split("\n\n")
    assert f"${rep['summary']['value']:,.2f}" in first and "7 days" in first
    assert "cash cushion" in second and "simulated" in second


def test_picks_are_measured_from_the_date_shown_not_the_day_before():
    a = account(starting_amount=3_000, interests={"themes": ["tech"], "unmatched": []})     # started today
    pick = build_report(a)["returns"]["period"]["picks"][0]
    assert pick["move_pct"] == 0 and pick["core_pct"] == 0 and pick["dollars"] == 0


def test_a_fund_that_started_today_says_it_is_too_soon_instead_of_showing_returns():
    make_fund("sam", starting_amount=3_000)
    text = page_text("sam")
    assert "shows once your money has been invested for a day" in text and "Returns show up once your money" in text


# ---------- Claude's intro never comes back blank ----------

class _Resp:
    def __init__(self, blocks, stop_reason):
        self.content, self.stop_reason = blocks, stop_reason


class _Block:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class _Client:
    def __init__(self, resp):
        self.messages = self
        self._resp = resp

    def create(self, **kw):
        self.kw = kw
        return self._resp


def test_a_reply_with_no_text_falls_back_to_the_offline_intro(monkeypatch):
    from agent import claude_agent
    client = _Client(_Resp([_Block("thinking")], "max_tokens"))      # what a long prompt does when thinking uses the whole budget
    monkeypatch.setattr(claude_agent, "_get_client", lambda: client)
    assert claude_agent.explain(["a fact"], "intro", fallback="the offline intro") == "the offline intro"
    assert client.kw["max_tokens"] >= 1000


def test_a_reply_with_text_is_used(monkeypatch):
    from agent import claude_agent
    monkeypatch.setattr(claude_agent, "_get_client", lambda: _Client(_Resp([_Block("thinking"), _Block("text", " Hello. ")], "end_turn")))
    assert claude_agent.explain(["a fact"], "intro", fallback="offline") == "Hello."


def test_a_loss_puts_the_minus_sign_before_the_dollar_sign_in_the_facts():
    a = account(starting_amount=5_000, started_days_ago=40)
    market.add_shock(a.today - timedelta(days=10), -25)
    rep = build_report(a)
    text = " ".join(rep["facts"]) + rep["narrative"]
    assert rep["summary"]["gain"] < 0 and "$-" not in text and "$+" not in text
    assert f"gain {report_module._usd(rep['summary']['gain'])}" in rep["facts"][0] and "gain -$" in rep["facts"][0]


def test_usd_formats_signs_before_the_dollar_sign():
    assert report_module._usd(-192) == "-$192.00" and report_module._usd(1843.7, True) == "+$1,843.70"
    assert report_module._usd(5) == "$5.00" and report_module._usd(-0.0) == "$0.00"
