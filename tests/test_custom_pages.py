"""Every page that shows a custom-index fund (about 60 holdings): the pie, the lists, the report.  OWNER: Ethan."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core import slices
from core.report import benchmark_weights, build_report
from core.models import SLICES
from tests.fund_helpers import make_custom_fund, make_portfolio_fixture

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
    from core.portfolio import build_portfolio
    by_key = {s["key"]: s for s in slices.view(build_portfolio(2, ["tech"]))}
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
    from tests.fund_helpers import make_fund
    s = make_fund("sam")                                           # five or six holdings
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
    from tests.fund_helpers import make_fund
    make_fund("sam")
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
    w = benchmark_weights(p)
    assert sum(w.values()) == pytest.approx(1)
    assert w["BND"] == pytest.approx(0.25) and w["VXUS"] == pytest.approx(0.225) and w["VTI"] == pytest.approx(0.525)
    assert w["VTI"] == pytest.approx(1 - p.slice_weights()["bonds"] - p.slice_weights()["intl"])    # the US part, whatever is in it


def test_the_benchmark_matches_the_funds_stock_bond_split_for_every_bond_share():
    for bonds in (0.10, 0.301, 0.45, 0.65):
        p = make_portfolio_fixture()
        scale = (1 - bonds) / (1 - 0.25)
        for h in p.holdings:
            h.weight = bonds if h.ticker == "BND" else h.weight * scale
        w = benchmark_weights(p)
        assert w["BND"] == pytest.approx(bonds) and sum(w.values()) == pytest.approx(1)


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
