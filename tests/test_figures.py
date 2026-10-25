"""Return figures: one calculation (core/figures.py, core/returns.py), the same number on every page."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import state
from app.main import app
from core import figures, fund, returns
from core.broker import get_broker, values
from core.report import build_report

client = TestClient(app)


def test_total_gain_is_value_minus_money_put_in():
    assert returns.total_gain(1_100.0, 1_000.0) == (100.0, 10.0)
    assert returns.total_gain(900.0, 1_000.0) == (-100.0, -10.0)
    assert returns.total_gain(0.0, 0.0) == (0.0, None)   # nothing put in: no percent


def test_day_change_backs_out_the_previous_close():
    # $110 now after +10% today was $100 yesterday: +$10; a holding with no quote counts as flat
    day, pct = returns.day_change({"A": 110.0, "B": 50.0}, {"A": 10.0})
    assert day == pytest.approx(10.0) and pct == pytest.approx(10 / 150 * 100)
    assert returns.day_change({}, {}) == (0.0, 0.0)


def _fig(html: str, which: str) -> str:
    """The number in a data-fig element (text before its label), whitespace squeezed."""
    m = re.search(rf'data-fig="{which}">(.*?)<span', html, re.S)
    assert m, which
    return " ".join(m.group(1).split())


def _pages(cid: str) -> dict[str, str]:
    return {p: client.get(f"/p/{cid}{p}").text for p in ("", "/money", "/report")}


def test_alex_total_since_start_matches_on_every_page():
    pages = _pages("alex")
    a = state.get("alex").fund
    f = figures.fund_figures(a)
    rep = build_report(a)
    assert rep["summary"]["gain"] == f["total_gain"] == round(f["value"] - a.contributed, 2)
    assert rep["summary"]["contributed"] == f["put_in"]
    want = f"+${f['total_gain']:,.2f} (+{f['total_gain_pct']:.2f}%)" if f["total_gain"] >= 0 else None
    shown = {p: _fig(html, "total") for p, html in pages.items()}
    assert len(set(shown.values())) == 1, shown
    if want:
        assert shown[""] == want
    for html in pages.values():
        assert "since you started" in html
    # The portfolio tab leads with the total; today's move comes second
    home = pages[""]
    assert home.index('data-fig="total"') < home.index('data-fig="today"')
    assert _fig(home, "today") == _fig(pages["/money"], "today")


def test_figures_still_match_after_a_paycheck_and_a_drop():
    client.get("/p/alex")
    client.post("/p/alex/paycheck")
    client.post("/p/alex/market_drop")
    shown = {p: _fig(html, "total") for p, html in _pages("alex").items()}
    assert len(set(shown.values())) == 1, shown
    a = state.get("alex").fund
    assert figures.fund_figures(a)["value"] == fund.value(a)


def test_holding_since_you_bought_matches_report_and_markets():
    client.get("/p/alex")
    a = state.get("alex").fund
    cost = get_broker().cost_basis("alex")
    vals = values("alex", a.today)
    rep = {h["ticker"]: h for h in build_report(a)["holdings"]}
    t = max(vals, key=vals.get)
    g = figures.holding_gain(vals[t], cost[t])
    assert rep[t]["gain_pct"] == g["gain_pct"]
    page = client.get(f"/p/alex/markets?s={t}").text
    assert f"{abs(g['gain']):,.2f}" in page and "since you bought" in page
