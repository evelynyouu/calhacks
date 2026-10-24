"""Alpaca paper trading.  OWNER: Saahil.

STUB: returns fake orders. Weekend orders won't fill on real Alpaca, so
est_fill_price (cached last close) is what the UI shows.
"""
from __future__ import annotations

import uuid

from core.models import Trade

_FAKE_LAST_CLOSE = {"AOK": 38.0, "AOM": 44.0, "AOR": 58.0, "AOA": 76.0,
                    "QQQ": 480.0, "XLK": 220.0, "XLV": 145.0, "ICLN": 13.0}


def buy_notional(symbol: str, dollars: float) -> Trade:
    return Trade(symbol, "buy", round(dollars, 2), "accepted", f"stub-{uuid.uuid4().hex[:8]}",
                 _FAKE_LAST_CLOSE.get(symbol))


def get_positions(customer_id: str) -> dict[str, float]:
    return {}
