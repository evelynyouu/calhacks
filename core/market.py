"""Market prices: real daily closes when we have them, simulated otherwise.  OWNER: Lance (data) + Saahil (engine).

Real closes come from core/feed.py (Nasdaq). Past the last real close (the demo fast-forwards paydays
into the future) and for any ticker the feed can't reach, prices follow a simulated path: one shared
market factor plus each ticker's own noise, seeded so every run (and every test) sees the same market,
and joined onto the last real close so there's no jump. With no real data at all, each ticker's price on
SIM_START matches its csv price. The demo's "Market drops" button adds a shock from a date onward:
stocks move by their beta, bonds and gold barely move.

The rest of the app only calls the functions below; source(ticker) says which kind a price is.
"""
from __future__ import annotations

import csv
import math
import random
import zlib
from bisect import bisect_right
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from core import feed
from core.state import SIM_START

DATA = Path(__file__).resolve().parent.parent / "data"

MARKET_SEED = 7
MARKET_DRIFT = 0.07          # yearly, the shared market factor
MARKET_VOL = 0.16
COMPANY_RESID_VOL = 0.25     # single companies swing more than the funds that hold them
OTHER_PARAMS = (100.0, 1.0, 0.30)   # tickers outside the csvs: only used past their last real close
START = SIM_START - timedelta(days=730)
END = SIM_START + timedelta(days=1095)
_DAYS = (END - START).days
_DT = 1 / 365


def _rows(name: str) -> list[dict]:
    with (DATA / name).open() as f:
        return list(csv.DictReader(f))


ETFS: dict[str, dict] = {r["ticker"]: r for r in _rows("etfs.csv")}
COMPANIES: dict[str, dict] = {r["ticker"]: r for r in _rows("companies.csv")}
HOLDINGS: dict[str, list[tuple[str, float]]] = {}
for _r in _rows("holdings.csv"):
    HOLDINGS.setdefault(_r["etf"], []).append((_r["ticker"], float(_r["weight_pct"]) / 100))

for _t in ETFS:
    feed.set_asset_class(_t, "etf")
for _t in COMPANIES:
    feed.set_asset_class(_t, "stocks")

_shocks: list[tuple[date, float]] = []
_real: dict[str, tuple[int, list[date], list[float]]] = {}


def name(ticker: str) -> str:
    row = ETFS.get(ticker) or COMPANIES.get(ticker)
    return row["name"] if row else (feed.name(ticker) or ticker)


def info(ticker: str) -> dict:
    """What the UI shows about any ticker: name, short label, one-line description, role."""
    if ticker in ETFS:
        r = ETFS[ticker]
        return {"ticker": ticker, "name": r["name"], "label": r["label"], "about": r["about"],
                "role": r["role"], "theme": r["theme"], "kind": "fund"}
    nm = name(ticker)
    if feed.asset_class(ticker) == "etf":
        return {"ticker": ticker, "name": nm, "label": nm, "about": "A fund outside FirstFund's list.",
                "role": "stock", "theme": "", "kind": "fund"}
    home = COMPANIES.get(ticker, {}).get("etf")
    about = f"A single company ({ETFS[home]['label'].lower()})." if home else "A single company."
    return {"ticker": ticker, "name": nm, "label": nm, "about": about, "role": "stock", "theme": "", "kind": "stock"}


def beta(ticker: str) -> float:
    if ticker in ETFS:
        return float(ETFS[ticker]["beta"])
    if ticker in COMPANIES:
        return float(ETFS[COMPANIES[ticker]["etf"]]["beta"])
    return OTHER_PARAMS[1]


def _params(ticker: str) -> tuple[float, float, float]:
    if ticker in ETFS:
        r = ETFS[ticker]
        return float(r["price"]), float(r["beta"]), float(r["resid_vol"])
    if ticker in COMPANIES:
        return float(COMPANIES[ticker]["price"]), beta(ticker), COMPANY_RESID_VOL
    return OTHER_PARAMS


def _real_closes(ticker: str) -> tuple[list[date], list[float]] | None:
    rows = feed.history(ticker)
    if not rows:
        return None
    hit = _real.get(ticker)
    if not hit or hit[0] != id(rows):
        hit = (id(rows), [d for d, _ in rows], [c for _, c in rows])
        _real[ticker] = hit
    return hit[1], hit[2]


def source(ticker: str) -> str:
    return "live" if _real_closes(ticker) else "simulated"


def known(ticker: str) -> bool:
    """Can we price this ticker at all? (csv tickers always; others only with real data)."""
    return ticker in ETFS or ticker in COMPANIES or _real_closes(ticker) is not None


def today() -> date:
    """The demo's starting day: the latest real trading day if we have real data, else SIM_START."""
    real = _real_closes("SPY")
    return real[0][-1] if real else SIM_START


@lru_cache(maxsize=None)
def _market_steps() -> tuple[float, ...]:
    rng = random.Random(MARKET_SEED)
    mu, sd = (MARKET_DRIFT - MARKET_VOL ** 2 / 2) * _DT, MARKET_VOL * math.sqrt(_DT)
    return tuple(rng.gauss(mu, sd) for _ in range(_DAYS))


@lru_cache(maxsize=None)
def _log_path(ticker: str) -> tuple[float, ...]:
    price, b, resid = _params(ticker)
    rng = random.Random(zlib.crc32(ticker.encode()))
    mu, sd = -resid ** 2 / 2 * _DT, resid * math.sqrt(_DT)
    logs = [0.0]
    for step in _market_steps():
        logs.append(logs[-1] + b * step + rng.gauss(mu, sd))
    shift = math.log(price) - logs[(SIM_START - START).days]
    return tuple(x + shift for x in logs)


def _sim_log(ticker: str, on: date) -> float:
    return _log_path(ticker)[min(_DAYS, max(0, (on - START).days))]


def raw_price(ticker: str, on: date) -> float:
    """Unrounded price, for math. Display with price()."""
    real = _real_closes(ticker)
    if real and on <= real[0][-1]:
        dates, closes = real
        px = closes[max(0, bisect_right(dates, on) - 1)]   # the last close on or before `on`, exactly as reported
    elif real:   # past the last real close: the simulated path, joined onto it
        px = real[1][-1] * math.exp(_sim_log(ticker, on) - _sim_log(ticker, real[0][-1]))
    else:
        px = math.exp(_sim_log(ticker, on))
    b = beta(ticker)
    for when, pct in _shocks:
        if on >= when:
            px *= (1 + pct / 100) ** b
    return px


def price(ticker: str, on: date) -> float:
    return round(raw_price(ticker, on), 2)


def change_pct(ticker: str, start: date, end: date) -> float:
    """Percent change in price from start to end."""
    return round((raw_price(ticker, end) / raw_price(ticker, start) - 1) * 100, 2)


def volatility_pct(ticker: str, end: date, days: int = 30) -> float:
    """Yearly volatility (percent) from the last `days` daily moves."""
    px = [raw_price(ticker, end - timedelta(days=d)) for d in range(days, -1, -1)]
    rets = [math.log(b / a) for a, b in zip(px, px[1:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var * 365) * 100, 1)


def below_high_pct(ticker: str, end: date, days: int = 365) -> float:
    """How far below its highest price of the last `days` the ticker is (0 = at the high)."""
    high = max(raw_price(ticker, end - timedelta(days=d)) for d in range(days + 1))
    return round((raw_price(ticker, end) / high - 1) * 100, 2)


def add_shock(on: date, pct: float) -> None:
    """The whole market moves `pct` percent on `on` (scaled by each ticker's beta) and stays there."""
    _shocks.append((on, pct))


def clear_shocks() -> None:
    _shocks.clear()


def quote(ticker: str, on: date | None = None) -> dict:
    """Latest price and day change: the real market right now if we can reach it, else the day `on`."""
    return quotes([ticker], on).get(ticker) or _sim_quote(ticker, on)


def quotes(tickers: list[str], on: date | None = None) -> dict[str, dict]:
    """quote() for many tickers with one batched live request. Unpriceable tickers are left out.
    A day `on` after the last real close (the demo fast-forwarded) skips the live request."""
    fast_forwarded = on is not None and on > today()
    live = {} if fast_forwarded else feed.quotes(list(dict.fromkeys(tickers)))
    return {t: {**live[t], "source": "live"} if t in live else _sim_quote(t, on)
            for t in dict.fromkeys(tickers) if t in live or known(t)}


def _sim_quote(ticker: str, on: date | None) -> dict:
    on = on or today()
    prev, now = raw_price(ticker, on - timedelta(days=1)), raw_price(ticker, on)
    return {"symbol": ticker, "name": name(ticker), "price": round(now, 2), "prev_close": round(prev, 2),
            "change": round(now - prev, 4), "change_pct": round((now / prev - 1) * 100, 2),
            "as_of": on.isoformat(), "status": "", "source": source(ticker)}


def series(ticker: str, start: date, end: date) -> list[tuple[date, float]]:
    """Prices for a chart: real trading days where we have them, every day where simulated."""
    real = _real_closes(ticker)
    out = []
    if real:
        out = [(d, c) for d, c in zip(*real) if start <= d <= end]
        start = max(start, real[0][-1] + timedelta(days=1))
    d = start
    while d <= end and (not real or d > real[0][-1]):
        out.append((d, raw_price(ticker, d)))
        d += timedelta(days=1)
    if _shocks:
        out = [(d, raw_price(ticker, d)) for d, _ in out]
    return out
