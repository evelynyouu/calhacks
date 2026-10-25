"""Every page that shows a custom-index fund (about 60 holdings): the pie, the lists, the report.  OWNER: Ethan."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core import slices
from core.report import benchmark_weights, build_report
from core.models import SLICES
from tests.fund_helpers import make_custom_fund, make_legacy_etf_fund, make_legacy_fund, make_portfolio_fixture

client = TestClient(app)


def pie_payload(html: str) -> dict:
    m = re.search(r"data-chart=\"pie\" data-payload='(.*?)'", html, re.S)
    return json.loads(m.group(1).replace("&#34;", '"'))


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html, flags=re.S)).split())


# ---------- the slice view ----------

def test_the_pie_has_one_wedge_per_slice_never_more_than_six():
    pie = slices.pie(make_portfolio_fixture())
    assert pie["labels"] == [label for _, label in SLICES] and len(pie["values"]) == 6
    assert sum(pie["values"]) == pytest.approx(100, abs=0.2)


def test_an_unused_slice_has_no_wedge():
    p = make_portfolio_fixture()
    p.holdings = [h for h in p.holdings if h.slice != "spend"]
    assert "Where you spend" not in slices.pie(p)["labels"] and len(slices.pie(p)["labels"]) == 5


def test_the_us_index_groups_by_sector_biggest_first_and_other_slices_do_not():
    by_key = {s["key"]: s for s in slices.view(make_portfolio_fixture())}
    groups = by_key["us_core"]["sectors"]
    assert len(groups) == 11 and [g["weight"] for g in groups] == sorted((g["weight"] for g in groups), reverse=True)
    assert sum(g["count"] for g in groups) == 50 == by_key["us_core"]["count"]
    assert by_key["spend"]["sectors"] is None and by_key["bonds"]["sectors"] is None


def test_an_older_etf_fund_has_no_sector_groups():
    by_key = {s["key"]: s for s in slices.view(make_legacy_etf_fund())}
    assert by_key["us_core"]["sectors"] is None and by_key["us_core"]["rows"][0]["ticker"] == "VTI"


# ---------- the fund card ----------

def test_the_fund_card_lists_slices_that_open_into_holdings():
    make_custom_fund("sam")
    html = client.get("/p/sam").text
    pie = pie_payload(html)
    assert len(pie["labels"]) <= 6 and pie["labels"][0] == "Bonds"
    for key, label in SLICES:
        assert f'data-slice="{key}"' in html and label in html
    assert html.count("<details") >= 6 + 11                       # six slices, and the US index's eleven sectors
    assert "58 holdings" in text(html)


def test_the_card_does_not_list_sixty_flat_rows():
    make_custom_fund("sam")
    card = re.search(r'<section id="fund-card".*?</section>', client.get("/p/sam").text, re.S).group(0)
    assert 'id="fund-slices"' in card and card.count("<details") >= 17


def test_the_spend_slice_says_it_is_personalization_not_a_prediction():
    make_custom_fund("sam")
    card = re.search(r'<section id="fund-card".*?</section>', client.get("/p/sam").text, re.S).group(0)
    assert "personalizes your fund; it isn&#39;t a prediction" in card or "personalizes your fund; it isn't a prediction" in card


def test_the_heads_up_and_override_blocks_stay():
    s = make_custom_fund("sam")
    s.fund.portfolio.overlap_warnings = ["A note worth reading."]
    assert "Heads up:" in client.get("/p/sam").text and "A note worth reading." in client.get("/p/sam").text


# ---------- the Portfolio tab's list ----------

def test_the_list_shows_the_ten_biggest_with_a_show_all_for_the_rest():
    from app.routes import pages
    s = make_custom_fund("sam", 10_000)
    t = pages._today(s.fund)
    assert len(t["rows"]) == slices.TOP_POSITIONS == 10 and len(t["rest"]) == 48
    assert [r["value"] for r in t["rows"]] == sorted((r["value"] for r in t["rows"]), reverse=True)
    assert min(r["value"] for r in t["rows"]) >= max(r["value"] for r in t["rest"])
    html = client.get("/p/sam").text
    assert 'id="all-positions"' in html and "Show all 58 positions" in text(html)
    assert html.count('href="/p/sam/markets?s=') >= 58


def test_the_days_dollar_move_still_sums_every_holding_not_only_the_ten_shown(monkeypatch):
    from app.routes import pages
    from core import market
    s = make_custom_fund("sam", 10_000)
    quotes = lambda tickers, on=None: {t: {"change_pct": 1.0, "price": 1.0, "source": "sim"} for t in tickers}   # noqa: E731
    monkeypatch.setattr(market, "quotes", quotes)
    t = pages._today(s.fund)
    everything = t["rows"] + t["rest"]
    total = sum(r["value"] for r in everything)
    assert t["day_change"] == pytest.approx(total * 0.01 / 1.01, rel=1e-6)           # a flat +1% on all 58 positions
    shown, hidden = (sum(r["value"] for r in part) * 0.01 / 1.01 for part in (t["rows"], t["rest"]))
    assert hidden > 0 and t["day_change"] == pytest.approx(shown + hidden)          # the 48 behind "Show all" count too


def test_a_small_fund_has_no_show_all():
    from app.routes import pages
    s = make_legacy_fund("sam")                                    # five holdings
    assert pages._today(s.fund)["rest"] == [] and 'id="all-positions"' not in client.get("/p/sam").text


def test_each_position_names_the_slice_it_belongs_to():
    make_custom_fund("sam")
    page = text(client.get("/p/sam").text)
    assert "Target" in page and "Where you spend" in page and "Your US index" in page


# ---------- "How your fund was built" ----------

def test_the_how_page_groups_the_fund_by_slice_with_each_holdings_reasons():
    make_custom_fund("sam")
    r = client.get("/p/sam/how")
    assert r.status_code == 200
    html = r.text
    assert [m for m in re.findall(r'data-slice="(\w+)"', html)] == [k for k, _ in SLICES]
    assert len(re.findall(r'data-holding="', html)) == 58
    assert pie_payload(html)["labels"] == [label for _, label in SLICES]
    t = text(html)
    assert "NVIDIA (NVDA): the 1st biggest US company we hold, in Technology." in t
    assert "You spent $300 at Target" in t and "This personalizes your fund; it isn't a prediction" in t.replace("&#39;", "'")


def test_the_us_index_collapses_to_sectors_on_the_how_page():
    make_custom_fund("sam")
    html = client.get("/p/sam/how").text
    sectors = re.findall(r'data-sector="([^"]+)"', html)
    assert len(sectors) == 11 and "Technology" in sectors
    us = html[html.index('data-slice="us_core"'):html.index('data-slice="spend"')]
    assert us.count("<details") == 11 and us.count('data-holding="') == 50


def test_the_how_page_still_works_for_an_older_etf_fund():
    make_legacy_fund("sam")
    html = client.get("/p/sam/how").text
    assert [m for m in re.findall(r'data-slice="(\w+)"', html)][:3] == ["bonds", "intl", "us_core"]
    assert 'data-holding="VTI"' in html and 'data-sector=' not in html


def test_explains_own_slices_win_over_the_fallback(monkeypatch):
    from core import explain
    real = explain.build
    def with_slices(acct):
        how = real(acct)
        how["slices"] = [{"key": "bonds", "label": "Bonds (from explain.py)", "weight": 1.0, "blurb": "b", "personalized": False,
                          "holdings": [], "sectors": None}]
        return how
    monkeypatch.setattr(explain, "build", with_slices)
    make_custom_fund("sam")
    assert "Bonds (from explain.py)" in client.get("/p/sam/how").text


# ---------- the report ----------

def test_the_benchmark_of_a_custom_fund_needs_no_vti_holding():
    p = make_portfolio_fixture()
    assert not any(h.ticker == "VTI" for h in p.holdings)
    w = benchmark_weights(p)                                    # a fund that records its parts: bonds, VXUS, the plain US index
    assert sum(w.values()) == pytest.approx(1, abs=1e-4)
    assert w["BND"] == pytest.approx(0.25) and w["VXUS"] == pytest.approx(0.225)
    us = {t: x for t, x in w.items() if t not in ("BND", "VXUS")}
    assert sum(us.values()) == pytest.approx(1 - 0.25 - 0.225, abs=1e-4) and len(us) > 20
    assert not {"TGT", "SBUX", "VRTX", "PYPL"} & set(w)         # nothing personal: that's what "vs benchmark" measures


def test_the_older_funds_three_fund_benchmark_is_unchanged_for_any_bond_share():
    for bonds in (0.10, 0.301, 0.45, 0.65):
        p = make_legacy_etf_fund()
        scale = (1 - bonds) / (1 - 0.45)
        for h in p.holdings:
            h.weight = bonds if h.ticker == "BND" else h.weight * scale
        w = benchmark_weights(p)
        assert w["BND"] == pytest.approx(bonds) and sum(w.values()) == pytest.approx(1)
        assert set(w) == {"VTI", "VXUS", "BND"}                  # every non-bond, non-VXUS holding counts as US stock


def test_the_report_of_a_custom_fund_builds_and_its_benchmark_gets_the_same_deposits():
    s = make_custom_fund("sam", 10_000, starting_amount=10_000)
    rep = build_report(s.fund)
    assert rep["returns"]["benchmark"] == benchmark_weights(s.fund.portfolio)
    since = rep["returns"]["since_start"]
    assert since["deposits"] == pytest.approx(10_000, abs=1) and since["benchmark_value"] == pytest.approx(since["end_value"], abs=1)


def custom_report(amount: float = 10_000):
    s = make_custom_fund("sam", amount, starting_amount=amount)
    return s, build_report(s.fund)


def test_the_holdings_table_is_grouped_by_slice_with_subtotals_that_add_up():
    _, rep = custom_report()
    groups = rep["by_slice"]
    assert [g["key"] for g in groups] == [k for k, _ in SLICES]
    assert sum(g["value"] for g in groups) == pytest.approx(rep["summary"]["value"], abs=0.1)
    assert sum(g["count"] for g in groups) == 58 and sum(g["weight"] for g in groups) == pytest.approx(1, abs=0.01)
    for g in groups:
        assert g["value"] == pytest.approx(sum(r["value"] for r in g["rows"]), abs=0.01)
    us = next(g for g in groups if g["key"] == "us_core")
    assert len(us["sectors"]) == 11 and sum(x["value"] for x in us["sectors"]) == pytest.approx(us["value"], abs=0.1)
    assert all(g["sectors"] is None for g in groups if g["key"] != "us_core")


def test_the_report_page_shows_the_slice_groups_and_their_subtotals():
    custom_report()
    html = client.get("/p/sam/report").text
    assert [m for m in re.findall(r'data-slice="(\w+)"', html)] == [k for k, _ in SLICES]
    assert len(re.findall(r'data-sector="', html)) == 11
    t = text(html)
    assert "58 holdings" not in t and "50 holdings" in t and "Your US index" in t
    assert "Picked from where you spend. This personalizes your fund" in t.replace("&#39;", "'")


def test_each_pick_vs_its_core_now_covers_the_spend_and_interest_slices():
    from datetime import timedelta
    s, _ = custom_report()
    s.fund.today += timedelta(days=40)
    picks = build_report(s.fund)["returns"]["since_start"]["picks"]
    assert {x["ticker"] for x in picks} == {"TGT", "SBUX", "COST", "VRTX", "REGN", "PYPL"}
    assert {x["slice"] for x in picks} == {"Where you spend", "Your interests", "Companies you added"}
    assert all(x["vs_core_pts"] == pytest.approx(x["move_pct"] - x["core_pct"], abs=0.01) for x in picks)


def test_companies_held_directly_count_directly_in_the_look_through():
    s, rep = custom_report()
    p = s.fund.portfolio
    held = {h.ticker: h.weight for h in p.holdings}
    top = rep["companies"][0]
    assert top["via"] == ["direct"] and top["share"] == pytest.approx(held[top["ticker"]], abs=0.001)


def test_sector_exposure_uses_each_holdings_own_sector_and_looks_through_only_the_funds():
    from core.report import CORE_SECTORS, _own_sectors, _sector_exposure
    s, rep = custom_report()
    p = s.fund.portfolio
    vals = {h.ticker: h.weight for h in p.holdings}
    mix = _sector_exposure(vals, 1.0, _own_sectors(p))
    own_tech = sum(h.weight for h in p.holdings if h.sector == "Technology")                  # companies: their own sector
    via_vxus = vals["VXUS"] * CORE_SECTORS["VXUS"]["Technology"]                              # the fund: looked through (approximate)
    assert mix["Technology"] == pytest.approx(own_tech + via_vxus)
    assert "Technology" in [x["sector"] for x in rep["sectors"]]
    assert "Bonds" not in mix and sum(mix.values()) == pytest.approx(1 - vals["BND"], abs=0.001)


def test_a_fresh_custom_fund_gets_no_diversify_recommendation():
    _, rep = custom_report()
    assert max(c["share"] for c in rep["companies"]) < 0.08
    assert "diversify" not in [r["action"] for r in rep["recommendations"]]


def test_a_company_that_grows_past_eight_percent_still_trips_the_diversify_rule():
    from core.broker import get_broker
    s, _ = custom_report()
    get_broker().submit("sam", "TGT", "buy", 1_500, s.fund.today)                             # a spend pick that became big
    rep = build_report(s.fund)
    assert rep["companies"][0]["ticker"] == "TGT" and rep["companies"][0]["share"] >= 0.08
    assert "diversify" in [r["action"] for r in rep["recommendations"]]


def test_income_counts_dividends_from_companies_by_sector():
    from datetime import timedelta
    from core import report as report_module
    s, _ = custom_report()
    s.fund.today += timedelta(days=365)
    w = build_report(s.fund)["returns"]["since_start"]
    rate = sum(h.weight * report_module._yields(s.fund.portfolio).get(h.ticker, 0.0) for h in s.fund.portfolio.holdings)
    assert rate > 0.012 and w["income_estimate"] == pytest.approx(10_000 * rate, rel=0.2)


# ---------- every page, for every persona ----------

@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_every_page_renders_for_every_persona_with_a_58_holding_fund(cid):
    make_custom_fund(cid, 10_000)
    for path in ("", "/how", "/report", "/chat", "/account", "/backtest"):
        r = client.get(f"/p/{cid}{path}")
        assert r.status_code == 200, (cid, path)
    page = client.get(f"/p/{cid}").text
    assert len(pie_payload(page)["labels"]) <= 6 and "Show all 58 positions" in text(page)


# ---------- chat tools ----------

def test_get_fund_returns_slices_with_their_holdings_not_a_flat_list():
    from agent.tools import run_tool
    s = make_custom_fund("sam")
    out, _ = run_tool(s, "get_fund", {})
    json.dumps(out)
    assert "holdings" not in out and out["holding_count"] == 58
    assert [x["key"] for x in out["slices"]] == [k for k, _ in SLICES]
    by = {x["key"]: x for x in out["slices"]}
    assert [h["ticker"] for h in by["bonds"]["holdings"]] == ["BND"] and len(by["spend"]["holdings"]) == 3
    assert "personalizes" in by["spend"]["note"] and "isn't a prediction" in by["spend"]["note"]
    us = by["us_core"]
    assert "holdings" not in us and len(us["sectors"]) == 11 and len(us["biggest_holdings"]) == 10 and us["count"] == 50
    assert len(json.dumps(out)) < 12_000                                    # a flat list of sixty would be far longer


def test_get_fund_still_lists_a_small_fund_in_full():
    from agent.tools import run_tool
    s = make_legacy_fund("sam")
    out, _ = run_tool(s, "get_fund", {})
    by = {x["key"]: x for x in out["slices"]}
    assert [h["ticker"] for h in by["us_core"]["holdings"]] == ["VTI"] and "sectors" not in by["us_core"]


def test_why_do_i_own_x_names_the_slice_and_sector_and_gives_the_rule():
    from agent.tools import run_tool
    s = make_custom_fund("sam")
    out, _ = run_tool(s, "get_fund_explanation", {"ticker": "tgt"})
    assert out["slice"] == "Where you spend" and out["sector"] == "Consumer" and out["target_pct"] == 4.0
    assert out["reasons"] and all(r["ticker"] == "TGT" for r in out["reasons"])
    assert "personalizes your fund; it isn't a prediction" in out["reasons"][0]["text"]
    nvda, _ = run_tool(s, "get_fund_explanation", {"ticker": "NVDA"})
    assert nvda["slice"] == "Your US index" and nvda["sector"] == "Technology" and "biggest US company" in nvda["reasons"][0]["text"]


def test_the_explanation_without_a_ticker_gives_the_slice_weights():
    from agent.tools import run_tool
    out, _ = run_tool(make_custom_fund("sam"), "get_fund_explanation", {})
    assert out["slice_weights_pct"]["Bonds"] == 25.0 and out["slice_weights_pct"]["Where you spend"] == 8.0


def test_the_prompt_says_the_engine_picked_by_rule_and_claude_does_not_endorse():
    from agent.prompts import SYSTEM_PROMPT
    p = " ".join(SYSTEM_PROMPT.split())
    assert "six parts, called slices" in p and "never say a company is a good buy" in p
    assert "personalization, not a prediction" in p and "stated rule" in p and "get_fund_explanation" in p


def test_the_offline_chat_explains_a_holding_from_its_reasons_and_never_lists_sixty_tickers(monkeypatch):
    from agent import claude_agent
    monkeypatch.setattr(claude_agent, "_get_client", lambda: None)
    s = make_custom_fund("sam")
    one = claude_agent._offline_turn(s, "why do I own TGT?")
    assert one.startswith("TGT is in the “Where you spend” part") and "personalizes your fund" in one
    general = claude_agent._offline_turn(s, "why is my fund built this way?")
    assert "Your US index 36% (50 companies)" in general and "AAPL" not in general and general.count("(") <= 6
    facts = " ".join(claude_agent.fund_facts(s))
    assert "50 companies" in facts and "isn't a prediction" in facts and facts.count("(") < 25      # not one entry per company


def test_the_first_message_describes_the_parts_not_the_tickers(monkeypatch):
    from agent import claude_agent
    monkeypatch.setattr(claude_agent, "_get_client", lambda: None)
    msg = claude_agent.fund_ready(make_custom_fund("sam"))
    assert msg.startswith("Your fund is ready: growth.") and "Your US index: 35.5%, 50 companies" in msg
    assert "isn't a prediction" in msg and msg.count("(") < 14                                  # the biggest few, not all 58


def test_what_moved_your_fund_opens_with_the_ten_biggest_and_a_show_all():
    from datetime import timedelta
    s, _ = custom_report()
    s.fund.today += timedelta(days=40)
    html = client.get("/p/sam/report").text
    movers = html[html.index('id="movers"'):html.index('id="all-movers"')]
    assert movers.count("<li") == 10 and "Show all 58 holdings" in text(html)
    rep = build_report(s.fund)["returns"]["period"]
    assert sum(x["dollars"] for x in rep["attribution"]) == pytest.approx(rep["market_gain"], abs=0.05)    # all 58 add up


def test_a_company_row_in_the_report_shows_the_funds_name_for_it_not_its_ticker_twice():
    _, rep = custom_report()
    rows = {r["ticker"]: r for r in rep["holdings"]}
    assert rows["TGT"]["label"] == "Target" and rows["NVDA"]["label"] == "NVIDIA"
    assert rows["VXUS"]["label"] == "International stocks"                      # a fund keeps its short label
    assert "TGT TGT" not in text(client.get("/p/sam/report").text)
