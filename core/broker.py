"""Trade execution.  OWNER: Saahil.

A broker is anything with these methods; the app only calls get_broker():
  submit(cid, symbol, side, notional, on) -> Trade
  positions(cid) -> {symbol: shares}
  cost_basis(cid) -> {symbol: dollars paid for the shares still held}
  orders(cid) -> [{"on", "symbol", "side", "shares", "notional", "price"}]   (oldest first)
  reset(cid=None)

SimBroker (default) fills instantly at the simulated price in core/market.py.
AlpacaBroker sends real orders through alpaca-py: set BROKER=alpaca plus ALPACA_API_KEY and
ALPACA_SECRET_KEY. It trades the paper account unless ALPACA_PAPER=false. One Alpaca account backs
every demo customer, and its fills use real prices while the report uses simulated ones, so real
trading also needs real market data behind core/market.price().
"""
from __future__ import annotations

import os
import uuid
from datetime import date

from core import market
from core.models import Trade, TradeSide

DUST = 1e-6   # shares below this count as sold out


class SimBroker:
    mode = "simulated"

    def __init__(self) -> None:
        self._accounts: dict[str, dict] = {}

    def _acct(self, cid: str) -> dict:
        return self._accounts.setdefault(cid, {"shares": {}, "cost": {}, "orders": []})

    def submit(self, cid: str, symbol: str, side: TradeSide, notional: float, on: date) -> Trade:
        a = self._acct(cid)
        px = market.raw_price(symbol, on)
        held = a["shares"].get(symbol, 0.0)
        shares = notional / px
        if side == "sell":
            shares = min(shares, held)
            notional = shares * px
            if held:
                a["cost"][symbol] = a["cost"].get(symbol, 0.0) * (1 - shares / held)
            a["shares"][symbol] = held - shares
            if a["shares"][symbol] < DUST:
                a["shares"].pop(symbol)
                a["cost"].pop(symbol, None)
        else:
            a["shares"][symbol] = held + shares
            a["cost"][symbol] = a["cost"].get(symbol, 0.0) + notional
        a["orders"].append({"on": on, "symbol": symbol, "side": side, "shares": shares,
                            "notional": round(notional, 2), "price": round(px, 2)})
        return Trade(symbol, side, round(notional, 2), "filled", f"sim-{uuid.uuid4().hex[:8]}", round(px, 2))

    def positions(self, cid: str) -> dict[str, float]:
        return dict(self._acct(cid)["shares"])

    def cost_basis(self, cid: str) -> dict[str, float]:
        return dict(self._acct(cid)["cost"])

    def orders(self, cid: str) -> list[dict]:
        return list(self._acct(cid)["orders"])

    def reset(self, cid: str | None = None) -> None:
        if cid is None:
            self._accounts.clear()
        else:
            self._accounts.pop(cid, None)


class AlpacaBroker:
    """Real orders via alpaca-py (pip install alpaca-py). Notional market orders, good for the day."""

    def __init__(self) -> None:
        from alpaca.trading.client import TradingClient
        self.paper = os.getenv("ALPACA_PAPER", "true").lower() != "false"
        self.mode = "Alpaca paper" if self.paper else "Alpaca live"
        self._client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=self.paper)

    def submit(self, cid: str, symbol: str, side: TradeSide, notional: float, on: date) -> Trade:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        order = self._client.submit_order(MarketOrderRequest(
            symbol=symbol, notional=round(notional, 2), time_in_force=TimeInForce.DAY,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL))
        return Trade(symbol, side, round(notional, 2), str(getattr(order.status, "value", order.status)), str(order.id))

    def positions(self, cid: str) -> dict[str, float]:
        return {p.symbol: float(p.qty) for p in self._client.get_all_positions()}

    def cost_basis(self, cid: str) -> dict[str, float]:
        return {p.symbol: float(p.cost_basis) for p in self._client.get_all_positions()}

    def orders(self, cid: str) -> list[dict]:
        return []   # value history isn't rebuilt from a real account

    def reset(self, cid: str | None = None) -> None:
        pass        # never wipe a real account from a demo button


_broker = None


def get_broker():
    global _broker
    if _broker is None:
        mode = os.getenv("BROKER", "sim").lower()
        if mode == "robinhood" and os.getenv("ROBINHOOD_CRYPTO_API_KEY") and os.getenv("ROBINHOOD_CRYPTO_PRIVATE_KEY"):
            from core.broker_robinhood import RobinhoodCryptoBroker
            _broker = RobinhoodCryptoBroker()
        elif mode == "alpaca" and os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
            _broker = AlpacaBroker()
        else:
            _broker = SimBroker()
    return _broker


def values(cid: str, on: date) -> dict[str, float]:
    """{symbol: dollars} for the customer's holdings on a date."""
    return {s: sh * market.raw_price(s, on) for s, sh in get_broker().positions(cid).items()}


# ---------- Legacy: core/payday.py (the old bills-first payday flow) still calls these ----------

_FAKE_LAST_CLOSE = {"AOK": 38.0, "AOM": 44.0, "AOR": 58.0, "AOA": 76.0,
                    "QQQ": 480.0, "XLK": 220.0, "XLV": 145.0, "ICLN": 13.0}


def buy_notional(symbol: str, dollars: float) -> Trade:
    return Trade(symbol, "buy", round(dollars, 2), "accepted", f"stub-{uuid.uuid4().hex[:8]}",
                 _FAKE_LAST_CLOSE.get(symbol))


def get_positions(customer_id: str) -> dict[str, float]:
    return {}
