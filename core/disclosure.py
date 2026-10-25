"""The standard disclosure: one slim line under the page header and the full text in the footer (base.html).

It carries real state, so it stays accurate: live vs demo bank (Nessie), real vs simulated prices
(market.price_label, the wording every screen uses), and the simulated vs Alpaca broker. Pages don't show their
own badges for these; inline labels stay only where one number is simulated and its neighbours aren't.
"""
from __future__ import annotations

from datetime import date

from core import market, nessie
from core.broker import get_broker


def disclosure(on: date | None = None) -> dict:
    """What the header line and the footer say, for the demo date `on` (None: the real today)."""
    broker = get_broker().mode
    offline = nessie.offline_reason()
    if offline:
        bank = {"label": "Nessie offline: demo data", "live": False, "title": offline,
                "now": "Nessie can't be reached, so the app is showing saved demo data"}
    elif nessie.mode() == nessie.LIVE:
        bank = {"label": "Live: Capital One Nessie sandbox", "live": True,
                "title": "Pretend money in Capital One's sandbox", "now": "connected to the live Nessie sandbox"}
    else:
        bank = {"label": "Bank: demo data", "live": False, "title": "A mock bank: built-in demo data",
                "now": "built-in demo data"}
    real_prices = market.source("SPY") == "live" and not (on is not None and on > market.today())
    prices = {"label": market.price_label(on), "live": real_prices}
    if broker == "simulated":
        trades = {"label": "Trades: simulated", "live": False,
                  "full": "Trades go to a simulated practice broker: no real money is invested, and orders fill "
                          "instantly at the prices shown."}
    elif "paper" in broker:
        trades = {"label": "Trades: Alpaca paper", "live": True,
                  "full": "Orders go to an Alpaca paper (practice) account; no real money is invested."}
    else:
        trades = {"label": "Trades: Alpaca live", "live": True,
                  "full": "Orders go to a real Alpaca brokerage account and use real money."}
    return {
        "items": [trades, bank, prices],
        "full": [
            trades["full"],
            "The bank is a mock: Capital One's Nessie sandbox (pretend money), or built-in demo data when it "
            f"isn't connected. Right now: {bank['now']}.",
            f"Prices shown: {prices['label']}. Nasdaq quotes are delayed. Past the last real close (the demo "
            "fast-forwards on paydays), and for tickers Nasdaq doesn't cover, prices follow a simulated path. "
            "Where one simulated number sits next to real ones, it's labeled.",
            "Approximations: what's inside each fund (companies and sectors), estimated income, the 10-year range "
            "and the worst-year figures use approximate data and stated assumptions. They aren't predictions.",
            "FirstFund is a demo, not a brokerage or a bank. Nothing here is financial, tax or legal advice: Python "
            "computes every number, Claude explains them, and you decide what to do.",
        ],
    }
