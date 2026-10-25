"""The ticker strip pinned to the top of every page."""
import re
from datetime import timedelta

from fastapi.testclient import TestClient

from app import state
from app.main import app
from core import market
from app.routes.pages import TAPE_HOLDINGS
from core.broker import get_broker
from tests.fund_helpers import make_fund

client = TestClient(app)


def test_strip_works_before_a_persona_is_picked():
    r = client.get("/tape")
    assert r.status_code == 200 and 'id="tape-strip"' in r.text and "S&amp;P 500" in r.text
    assert "<a " not in r.text   # no persona, so nothing to link to


def test_persona_strip_links_to_markets_and_includes_holdings():
    make_fund("sam")
    r = client.get("/p/sam/tape")
    assert 'href="/p/sam/markets?s=VXUS"' in r.text and 'tabindex="-1"' in r.text and 'aria-hidden="true"' in r.text


def test_refresh_returns_only_the_cards():
    r = client.get("/tape?part=groups")
    assert 'class="tape-group"' in r.text and 'id="tape-strip"' not in r.text and "hx-trigger" not in r.text


def test_change_and_percent_are_green_up_red_down():
    r = client.get("/tape").text
    q = market.quote("SPY", market.today())
    cls = "tape-up" if q["change"] >= 0 else "tape-down"
    assert f'<span class="{cls}">({q["change_pct"] + 0.0:+.2f}%)</span>' in r


def test_unknown_persona_404():
    assert client.get("/p/nobody/tape").status_code == 404


def test_pages_render_the_loader_with_the_same_id():
    r = client.get("/")
    assert 'id="tape" class="tape tape-loading"' in r.text and 'hx-get="/tape"' in r.text


def test_every_screen_uses_the_same_price_label():
    """Ticker, Portfolio, chat and Markets all say where prices come from in the same words."""
    make_fund("sam")
    label = market.price_label(market.today())
    assert label == "Simulated prices"   # tests run offline
    for path in ("/p/sam/tape", "/p/sam", "/p/sam/chat", "/p/sam/markets?s=VTI"):
        assert label in client.get(path).text, path
    later = market.today() + timedelta(days=14)
    assert market.price_label(later).startswith("Simulated prices · demo date ")


# ---------- What the strip shows ----------

def _names(html: str) -> list[str]:
    """Card names in the first (real) scrolling copy, without repeats."""
    first = html.split('<div class="tape-group"', 2)[1]
    return list(dict.fromkeys(re.findall(r'class="tape-name">([^<]+)<', first)))


def _by_weight(s) -> list[str]:
    """The fund's biggest TAPE_HOLDINGS positions (a custom index holds ~60; the strip shows the biggest)."""
    return [h.ticker for h in sorted(s.fund.portfolio.holdings, key=lambda h: -h.weight)][:TAPE_HOLDINGS]


def test_landing_page_strip_shows_the_major_indexes_only():
    names = _names(client.get("/tape").text)
    assert names[0] == "S&amp;P 500" and len(names) == 3   # S&P 500, Nasdaq (COMP, or QQQ offline), Dow
    for t in ("AAPL", "MSFT", "BND", "GLD", "VTI"):
        assert t not in names


def test_before_a_fund_is_built_the_strip_shows_indexes():
    state.get("maria")   # starts at the bank link, no fund yet
    r = client.get("/p/maria/tape").text
    assert _names(r) == _names(client.get("/tape").text) and 'aria-label="Market prices"' in r


def test_with_a_fund_the_strip_shows_only_holdings_in_weight_order():
    s = make_fund("sam", stocks=["AAPL"])
    assert len(s.fund.portfolio.holdings) > TAPE_HOLDINGS
    s.watchlist = ["TSLA", "META"]
    r = client.get("/p/sam/tape").text
    assert _names(r) == _by_weight(s) and "AAPL" in _by_weight(s)
    assert "S&amp;P 500" not in r and "TSLA" not in r and "GLD" not in r
    assert 'aria-label="Your holdings"' in r


def test_anything_the_broker_holds_outside_the_fund_shows_too():
    s = make_fund("sam")
    assert "GLD" not in {h.ticker for h in s.fund.portfolio.holdings}
    get_broker().submit("sam", "GLD", "buy", 50.0, s.fund.today)
    names = _names(client.get("/p/sam/tape").text)
    assert names == _by_weight(s) + ["GLD"]


def test_a_short_list_repeats_to_fill_the_loop():
    make_fund("sam", interests={"themes": [], "unmatched": []})
    n = TAPE_HOLDINGS   # fewer cards than fill a wide screen
    r = client.get("/p/sam/tape").text
    first = r.split('<div class="tape-group"', 2)[1]
    cards = first.count('class="tape-item') - 1   # minus the price note
    assert cards >= 14 and cards % n == 0
    # only the first pass is focusable and read aloud
    assert first.count('tabindex="-1"') == cards - n and first.count("tape-dup") == cards - n
    assert re.search(r'id="tape"[^>]*style="animation-duration:\d+s"', r)


def test_refresh_keeps_holdings():
    make_fund("sam")
    r = client.get("/tape?cid=sam&part=groups").text
    assert "BND" in r and "S&amp;P 500" not in r and r.count('class="tape-group"') == 2
