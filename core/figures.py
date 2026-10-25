"""The return figures every screen shows, computed once.

Portfolio, Money, Report and Markets all take these numbers from here, so the same label always means the
same calculation. The math is in core/returns.py; templates only format what this returns.

  Today             the holdings' move since the previous close (each price's day change)
  Since you started value now minus everything the customer has put in (the "total")
  Since you bought  the same thing for one holding: its value now minus what was paid for it
The report adds the time-weighted and money-weighted returns over a dated window (core/report.py); their
one-line definitions live in DEFINITIONS below, next to the others.
"""
from __future__ import annotations

from core import market, returns
from core.broker import get_broker, values

DEFINITIONS = {
    "today": "Today: how much your holdings moved since the previous market close.",
    "total": "Since you started: what your fund is worth now minus all the money you've put in.",
    "since_bought": "Since you bought: what this holding is worth now minus what you paid for it.",
    "twr": "Time-weighted return: how the fund itself did, with the timing of your deposits taken out. "
           "It's the fair number to compare with a benchmark.",
    "mwr": "Money-weighted return: how your own dollars did, counting when each deposit landed, so a deposit "
           "just before a dip drags it down.",
}


def fund_figures(acct) -> dict:
    """Value, total gain since the start, today's move, and each holding's value and quote, for one account."""
    cid, on = acct.customer_id, acct.today
    vals = values(cid, on)
    quotes = market.quotes(list(vals), on)
    rows = [{"ticker": t, "info": market.info(t), "value": v, "quote": quotes.get(t)}
            for t, v in sorted(vals.items(), key=lambda kv: -kv[1])]
    value = round(sum(vals.values()), 2)
    day, day_pct = returns.day_change(vals, {t: q["change_pct"] for t, q in quotes.items() if q})
    orders = get_broker().orders(cid)
    return {"value": value, **total(value, acct.contributed), "day_change": day, "day_pct": day_pct,
            "rows": rows, "as_of": on, "started": min((o["on"] for o in orders), default=None)}


def total(value: float, contributed: float) -> dict:
    """Since you started: the fund's value minus the money put in (for panels that already know the value)."""
    put_in = round(contributed, 2)
    gain, pct = returns.total_gain(round(value, 2), put_in)
    return {"put_in": put_in, "total_gain": gain, "total_gain_pct": pct}


def holding_gain(value: float, cost: float) -> dict:
    """One holding since you bought it: same definition as the fund's total."""
    gain, pct = returns.total_gain(value, cost)
    return {"gain": gain, "gain_pct": pct}
