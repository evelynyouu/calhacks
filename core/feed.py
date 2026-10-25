"""Real market data from Nasdaq's public site API (delayed quotes, daily closes, news RSS).  OWNER: Lance.

No key needed. Every call has a short timeout and a cache, and every failure returns None or [],
so callers fall back to simulated prices (core/market.py) and the demo never hangs on bad wifi.
MARKET_DATA=sim turns this off (tests do); the default is live.

  history(symbol)       -> [(date, close)] oldest first, about 2 years, or None
  quotes(symbols)       -> {symbol: {"price", "prev_close", "change", "change_pct", "as_of", "name"}}
  news(symbols)         -> [{"title", "link", "published", "source", "tickers"}] newest first
  search(text)          -> [{"symbol", "name", "asset"}]
"""
from __future__ import annotations

import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

API = "https://api.nasdaq.com/api"
RSS = "https://www.nasdaq.com/feed/rssoutbound"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130 Safari/537.36",
           "Accept": "application/json, text/xml;q=0.9, */*;q=0.8"}
TIMEOUT = (3, 6)
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "prices"
HISTORY_TTL = 6 * 3600      # daily closes change once a day
QUOTE_TTL = 60
NEWS_TTL = 600
RETRY_AFTER = 300           # a symbol that failed isn't tried again for 5 minutes
HISTORY_DAYS = 760
QUOTE_BATCH = 20

_lock = threading.Lock()
_history: dict[str, tuple[float, list[tuple[date, float]] | None]] = {}
_quotes: dict[str, tuple[float, dict]] = {}
_news: dict[str, tuple[float, list[dict]]] = {}
_asset: dict[str, str] = {"COMP": "index"}   # symbol -> "etf" / "stocks", learned as we go; Nasdaq Composite is an index
_names: dict[str, str] = {}


def enabled() -> bool:
    return os.getenv("MARKET_DATA", "live").lower() != "sim"


def set_asset_class(symbol: str, asset: str) -> None:
    _asset[symbol.upper()] = asset


def name(symbol: str) -> str | None:
    return _names.get(symbol.upper())


def asset_class(symbol: str) -> str | None:
    return _asset.get(symbol.upper())


def _get(url: str, params: dict | None = None) -> requests.Response | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        return r if r.ok else None
    except requests.RequestException:
        return None


def _money(text) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _classes(symbol: str) -> list[str]:
    known = _asset.get(symbol)
    return [known] if known else ["stocks", "etf"]


# ---------- daily closes ----------

def history(symbol: str) -> list[tuple[date, float]] | None:
    if not enabled():
        return None
    symbol = symbol.upper()
    hit = _history.get(symbol)
    if hit and time.time() - hit[0] < (HISTORY_TTL if hit[1] else RETRY_AFTER):
        return hit[1]
    rows = _load_disk(symbol)
    if rows is None:
        rows = _fetch_history(symbol)
        if rows:
            _save_disk(symbol, rows)
    with _lock:
        _history[symbol] = (time.time(), rows)
    return rows


def _fetch_history(symbol: str) -> list[tuple[date, float]] | None:
    today = date.today()
    for asset in _classes(symbol):
        r = _get(f"{API}/quote/{symbol}/historical", {
            "assetclass": asset, "fromdate": (today - timedelta(days=HISTORY_DAYS)).isoformat(),
            "todate": today.isoformat(), "limit": 9999})
        try:
            table = r.json()["data"]["tradesTable"]["rows"] if r else None
        except (ValueError, KeyError, TypeError):
            table = None
        if table:
            _asset[symbol] = asset
            rows = [(datetime.strptime(x["date"], "%m/%d/%Y").date(), _money(x["close"])) for x in table]
            return sorted((d, c) for d, c in rows if c)
    return None


def _load_disk(symbol: str) -> list[tuple[date, float]] | None:
    path = CACHE_DIR / f"{symbol.replace('/', '_')}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - data["fetched"] > HISTORY_TTL:
        return None
    return [(date.fromisoformat(d), c) for d, c in data["rows"]]


def _save_disk(symbol: str, rows: list[tuple[date, float]]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{symbol.replace('/', '_')}.json"
        path.write_text(json.dumps({"fetched": time.time(), "rows": [(d.isoformat(), c) for d, c in rows]}))
    except OSError:
        pass


def prefetch(symbols: list[str], workers: int = 6) -> None:
    """Warm the history cache in the background so the first page view doesn't wait."""
    if not enabled():
        return
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="feed")
    for s in symbols:
        pool.submit(history, s)
    pool.shutdown(wait=False)


# ---------- quotes ----------

def quotes(symbols: list[str]) -> dict[str, dict]:
    if not enabled():
        return {}
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    now = time.time()
    out = {}
    for s in symbols:
        hit = _quotes.get(s)
        if hit and now - hit[0] < QUOTE_TTL:
            out[s] = hit[1]
    todo = [s for s in symbols if s not in out]
    for i in range(0, len(todo), QUOTE_BATCH):
        batch = todo[i:i + QUOTE_BATCH]
        params = [("symbol", f"{s.lower()}|{_classes(s)[0]}") for s in batch]
        r = _get(f"{API}/quote/watchlist", params)
        try:
            rows = (r.json().get("data") or []) if r else []
        except ValueError:
            rows = []
        for row in rows:
            q = _parse_quote(row)
            if q:
                out[q["symbol"]] = q
                _quotes[q["symbol"]] = (now, q)
    return out


def _parse_quote(row: dict) -> dict | None:
    price, prev = _money(row.get("lastSalePrice")), _money(row.get("previousClosePrice"))
    if not price:
        return None
    symbol = row["symbol"].upper()
    _names[symbol] = row.get("companyName") or symbol
    change = _money(row.get("netChange"))
    if change is None and prev:
        change = price - prev
    prev = prev or (price - change if change is not None else None)
    return {"symbol": symbol, "name": _names[symbol], "price": price, "prev_close": prev,
            "change": round(change or 0.0, 4), "change_pct": round((change or 0.0) / prev * 100, 2) if prev else 0.0,
            "as_of": row.get("lastTradeTimestamp", ""), "status": row.get("marketStatus", "")}


# ---------- news ----------

def news(symbols: list[str] | None = None, limit: int = 30) -> list[dict]:
    """Headlines for the given symbols (or the markets feed), newest first, deduplicated."""
    if not enabled():
        return []
    keys = [f"symbol={s.upper()}" for s in (symbols or [])] or ["category=Markets"]
    items: dict[str, dict] = {}
    for key in keys:
        for it in _news_feed(key):
            items.setdefault(it["link"], it)
    return sorted(items.values(), key=lambda x: x["published"] or datetime.min, reverse=True)[:limit]


def _news_feed(key: str) -> list[dict]:
    hit = _news.get(key)
    if hit and time.time() - hit[0] < NEWS_TTL:
        return hit[1]
    k, v = key.split("=", 1)
    r = _get(RSS, {k: v})
    items = []
    if r:
        try:
            root = ET.fromstring(r.content)
            for x in root.iter("item"):
                link = (x.findtext("link") or "").strip()
                if not link.startswith("https://"):
                    continue
                try:
                    published = parsedate_to_datetime(x.findtext("pubDate") or "")
                except (TypeError, ValueError):
                    published = None
                tickers = x.findtext("{http://nasdaq.com/reference/feeds/1.0}tickers") or ""
                items.append({"title": (x.findtext("title") or "").strip(), "link": link, "published": published,
                              "source": (x.findtext("{http://purl.org/dc/elements/1.1/}creator") or "Nasdaq").strip(),
                              "tickers": sorted({t.strip() for t in tickers.split(",") if t.strip()})})
        except ET.ParseError:
            items = []
    _news[key] = (time.time(), items)
    return items


# ---------- search ----------

def search(text: str) -> list[dict]:
    if not enabled() or not text.strip():
        return []
    r = _get(f"{API}/autocomplete/slookup/10", {"search": text.strip()})
    try:
        rows = (r.json().get("data") or []) if r else []
    except ValueError:
        rows = []
    out = []
    for x in rows:
        asset = {"STOCKS": "stocks", "ETF": "etf"}.get(str(x.get("asset", "")).upper())
        if asset and x.get("symbol"):
            _asset[x["symbol"].upper()] = asset
            _names[x["symbol"].upper()] = x.get("name") or x["symbol"]
            out.append({"symbol": x["symbol"].upper(), "name": x.get("name") or x["symbol"], "asset": asset})
    return out
