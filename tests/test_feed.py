"""Real market data: Nasdaq responses parse, failures fall back, and real closes join the simulated path.
No network: _get is replaced with canned responses."""
from datetime import date, timedelta

import pytest

from core import feed, market


class Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content, self.ok = data, content, True

    def json(self):
        return self._data


HIST = {"data": {"tradesTable": {"rows": [
    {"date": "10/23/2026", "close": "$100.00"}, {"date": "10/22/2026", "close": "$98.00"},
    {"date": "10/20/2026", "close": "$97.50"}]}}}
QUOTES = {"data": [{"symbol": "ZZZ", "companyName": "Zed Corp", "lastSalePrice": "$100.00", "netChange": "2.00",
                    "previousClosePrice": 98.0, "lastTradeTimestamp": "Oct 23, 2026", "marketStatus": "Closed"}]}
RSS = b"""<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:nasdaq="http://nasdaq.com/reference/feeds/1.0">
<channel><item><title>Zed Corp beats</title><link>https://www.nasdaq.com/articles/zed</link>
<pubDate>Sat, 24 Oct 2026 21:09:00 +0000</pubDate><dc:creator>Wire</dc:creator><nasdaq:tickers>ZZZ,ZZZ</nasdaq:tickers></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item></channel></rss>"""


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("MARKET_DATA", "live")
    for cache in (feed._history, feed._quotes, feed._news):
        cache.clear()
    market._real.clear()

    def fake_get(url, params=None):
        if "historical" in url:
            return Resp(HIST)
        if "watchlist" in url:
            return Resp(QUOTES)
        if "rssoutbound" in url:
            return Resp(content=RSS)
        return None
    monkeypatch.setattr(feed, "_get", fake_get)
    yield
    for cache in (feed._history, feed._quotes, feed._news):
        cache.clear()
    market._real.clear()


def test_history_parses_dollars_and_sorts(live):
    assert feed.history("ZZZ") == [(date(2026, 10, 20), 97.5), (date(2026, 10, 22), 98.0), (date(2026, 10, 23), 100.0)]


def test_quotes_parse_change(live):
    q = feed.quotes(["zzz"])["ZZZ"]
    assert q["price"] == 100 and q["change"] == 2 and q["change_pct"] == pytest.approx(2.04) and q["name"] == "Zed Corp"


def test_news_keeps_only_https_links_and_dedupes_tickers(live):
    items = feed.news(["ZZZ"])
    assert [n["title"] for n in items] == ["Zed Corp beats"] and items[0]["tickers"] == ["ZZZ"]


def test_market_uses_real_closes_then_joins_the_simulated_path(live):
    assert market.source("ZZZ") == "live" and market.today() == date(2026, 10, 23)   # SPY gets the same canned history
    assert market.raw_price("ZZZ", date(2026, 10, 21)) == 97.5          # no trading that day: last close before it
    later = market.raw_price("ZZZ", date(2026, 10, 24))
    assert later != 100 and abs(later / 100 - 1) < 0.1                 # simulated, starting from the real close


def test_everything_falls_back_when_nasdaq_is_down(monkeypatch):
    monkeypatch.setenv("MARKET_DATA", "live")
    monkeypatch.setattr(feed, "_get", lambda *a, **k: None)
    feed._history.clear()
    assert feed.history("QQQ") is None and feed.quotes(["QQQ"]) == {} and feed.news(["QQQ"]) == []
    assert market.source("QQQ") == "simulated"
    assert market.quote("QQQ", market.SIM_START)["source"] == "simulated"


def test_sim_mode_never_calls_out(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used in sim mode")
    monkeypatch.setattr(feed, "_get", boom)
    assert feed.history("SPY") is None and feed.quotes(["SPY"]) == {} and feed.news() == [] and feed.search("a") == []
