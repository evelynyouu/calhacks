"""Every page that shows a custom-index fund (about 60 holdings): the pie, the lists, the report.  OWNER: Ethan."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core import slices
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
