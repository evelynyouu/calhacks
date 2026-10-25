"""Return math for the fund report: replay the broker's orders, then work out what the fund did.  OWNER: Ethan.

Pure functions: prices come in as a function `px(ticker, date)`, so every number here can be checked by hand.

Why replay orders instead of "today's weights x each price change": paychecks buy shares during the period,
so a fund that doubled because of deposits did not double. Replaying the orders gives, for any window:
  - the dollars: value at the start + new money put in + market gain/loss = value at the end (exact)
  - the time-weighted return (TWR): how the fund itself did, with deposit timing removed. Comparable to a benchmark.
  - the money-weighted return (MWR, an IRR): how the customer's own dollars did, timing included.
  - the gain per holding, which adds up to the market gain/loss.
Estimated income (fund distributions) is kept apart from the price numbers: it is approximate and is not in
the broker's balance, so it never breaks the "adds up" identities above.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

Price = Callable[[str, date], float]
Order = dict   # {"on": date, "symbol": str, "side": "buy"|"sell", "shares": float, ...} as the broker records them

DAYS_PER_YEAR = 365
FLOW_DUST = 0.005    # dollars; net new money smaller than half a cent is rounding, not a deposit


@dataclass
class Segment:
    """The stretch between two order dates, during which no shares were bought or sold."""
    start: date
    end: date
    v_start: float                  # value just after the orders on `start`
    v_end: float                    # value just before the orders on `end`
    flow: float                     # net new money on `end` (buys minus sells), dollars
    gains: dict[str, float]         # price gain per ticker over the segment
    held: dict[str, float]          # dollars held per ticker at the start of the segment
    income: dict[str, float]        # estimated income per ticker over the segment (approximate)


@dataclass
class Replay:
    start: date
    end: date
    start_value: float
    end_value: float
    segments: list[Segment] = field(default_factory=list)

    @property
    def flows(self) -> list[tuple[date, float]]:
        return [(s.end, s.flow) for s in self.segments if abs(s.flow) >= FLOW_DUST]

    @property
    def deposits(self) -> float:
        """Net new money put in over the window."""
        return sum(f for _, f in self.flows)

    @property
    def market_gain(self) -> float:
        """Dollars gained or lost from price moves: end - start - deposits."""
        return self.end_value - self.start_value - self.deposits

    @property
    def income(self) -> float:
        return sum(sum(s.income.values()) for s in self.segments)


def net_flows(orders: list[Order], px: Price) -> dict[date, float]:
    """New money per day: buys minus sells, at that day's price (shares x price, so the books balance exactly)."""
    out: dict[date, float] = {}
    for o in orders:
        sign = 1 if o["side"] == "buy" else -1
        out[o["on"]] = out.get(o["on"], 0.0) + sign * o["shares"] * px(o["symbol"], o["on"])
    return out


def replay(orders: list[Order], px: Price, start: date, end: date,
           yields: dict[str, float] | None = None) -> Replay:
    """Walk the orders from `start` to `end`. Orders on `start` count as already held at the start;
    pass the day before the first order to count the first deposit as new money.
    `yields` is {ticker: yearly income as a fraction}, accrued daily, to estimate fund distributions."""
    yields = yields or {}
    shares: dict[str, float] = {}
    todo = sorted(orders, key=lambda o: o["on"])
    i = 0
    while i < len(todo) and todo[i]["on"] <= start:
        _apply(shares, todo[i])
        i += 1

    def value(on: date) -> float:
        return sum(s * px(t, on) for t, s in shares.items())

    out = Replay(start, end, value(start), 0.0)
    prev, prev_value = start, out.start_value
    days = sorted({o["on"] for o in todo[i:] if o["on"] <= end} | {end})
    for day in days:
        if day <= prev:
            continue
        gains = {t: s * (px(t, day) - px(t, prev)) for t, s in shares.items()}
        held = {t: s * px(t, prev) for t, s in shares.items()}
        income = {t: s * px(t, prev) * ((1 + yields[t]) ** ((day - prev).days / DAYS_PER_YEAR) - 1)
                  for t, s in shares.items() if yields.get(t)}
        v_end = value(day)
        flow = 0.0
        while i < len(todo) and todo[i]["on"] <= day:
            o = todo[i]
            flow += (1 if o["side"] == "buy" else -1) * o["shares"] * px(o["symbol"], day)
            _apply(shares, o)
            i += 1
        out.segments.append(Segment(prev, day, prev_value, v_end, flow, gains, held, income))
        prev, prev_value = day, value(day)
    out.end_value = prev_value if out.segments else out.start_value
    return out


def _apply(shares: dict[str, float], o: Order) -> None:
    sign = 1 if o["side"] == "buy" else -1
    shares[o["symbol"]] = shares.get(o["symbol"], 0.0) + sign * o["shares"]
    if shares[o["symbol"]] < 1e-9:
        shares.pop(o["symbol"])


def twr(r: Replay, with_income: bool = False) -> float | None:
    """Time-weighted return over the window, as a fraction. Chains the return of each stretch between orders,
    so when money arrives doesn't matter. None if nothing was invested during the window."""
    growth, seen = 1.0, False
    for s in r.segments:
        if s.v_start <= 0:
            continue
        seen = True
        growth *= (s.v_end + (sum(s.income.values()) if with_income else 0.0)) / s.v_start
    return growth - 1 if seen else None


def money_weighted(r: Replay, with_income: bool = False) -> float | None:
    """Money-weighted return over the window, as a fraction: the single rate that turns the customer's own
    deposits (and the starting value) into the ending value. A deposit just before a drop drags it below the TWR.
    It is the period rate; xirr() below gives the same thing as a yearly rate."""
    flows = [(r.start, -r.start_value)] if r.start_value > 0 else []
    flows += [(d, -f) for d, f in r.flows]
    flows.append((r.end, r.end_value + (r.income if with_income else 0.0)))
    return period_irr(flows)


def period_irr(flows: list[tuple[date, float]]) -> float | None:
    """The rate r over the whole span (first to last date) with sum(cf x (1+r)^(time left / span)) = 0.
    Money in is negative, money out (or the ending value) is positive. None if there's no sign change."""
    flows = sorted(flows)
    if not flows or not any(cf < 0 for _, cf in flows) or not any(cf > 0 for _, cf in flows):
        return None
    t0, t1 = flows[0][0], flows[-1][0]
    span = (t1 - t0).days
    if span <= 0:
        return None

    def f(rate: float) -> float:
        return sum(cf * (1 + rate) ** ((t1 - d).days / span) for d, cf in flows)

    lo, hi = -0.999999, 1.0
    while f(lo) * f(hi) > 0 and hi < 1e6:   # widen until the root is bracketed
        hi *= 4
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Yearly IRR for dated cash flows (the spreadsheet XIRR: 365-day years)."""
    r = period_irr(flows)
    if r is None:
        return None
    span = (max(d for d, _ in flows) - min(d for d, _ in flows)).days
    return (1 + r) ** (DAYS_PER_YEAR / span) - 1


def attribution(r: Replay) -> dict[str, float]:
    """Dollars each holding added (or cost) over the window; the values add up to r.market_gain."""
    out: dict[str, float] = {}
    for s in r.segments:
        for t, g in s.gains.items():
            out[t] = out.get(t, 0.0) + g
    return out


def excess_over(r: Replay, ticker: str, alternative: dict[str, float], px: Price) -> float:
    """Dollars `ticker` made beyond what the same dollars would have made in `alternative` ({ticker: weight}),
    stretch by stretch. This is how a pick did against the core fund it replaced."""
    total = 0.0
    for s in r.segments:
        held = s.held.get(ticker, 0.0)
        if held:
            alt = sum(w * (px(t, s.end) / px(t, s.start) - 1) for t, w in alternative.items())
            total += s.gains.get(ticker, 0.0) - held * alt
    return total


def income_by_ticker(r: Replay) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in r.segments:
        for t, v in s.income.items():
            out[t] = out.get(t, 0.0) + v
    return out


def shadow_orders(flows: dict[date, float], weights: dict[str, float], px: Price) -> list[Order]:
    """Orders for a pretend portfolio that gets the same new money on the same days as the real fund and puts
    it into `weights` (new money buys at those weights; withdrawals sell in proportion to what it holds).
    Replaying it gives the fund's benchmark: same deposits, same dates, none of the customer's choices."""
    held: dict[str, float] = {}
    orders: list[Order] = []
    for day in sorted(flows):
        f = flows[day]
        if abs(f) < FLOW_DUST:
            continue
        if f > 0:
            plan = {t: f * w for t, w in weights.items() if w > 0}
        else:
            total = sum(s * px(t, day) for t, s in held.items())
            plan = {t: max(f, -total) * (s * px(t, day) / total) for t, s in held.items()} if total > 0 else {}
        for t, dollars in plan.items():
            shares = dollars / px(t, day)
            orders.append({"on": day, "symbol": t, "side": "buy" if shares > 0 else "sell", "shares": abs(shares)})
            held[t] = held.get(t, 0.0) + shares
    return orders


def day_before(d: date) -> date:
    return d - timedelta(days=1)
