"""Robinhood Crypto API broker.

Calls https://trading.robinhood.com directly with Ed25519 request signing.
Crypto-only (BTC-USD, ETH-USD, SOL-USD, DOGE-USD); all other symbols fall
back to SimBroker so the fund's equity holdings still simulate correctly.

Set ROBINHOOD_CRYPTO_API_KEY and ROBINHOOD_CRYPTO_PRIVATE_KEY in .env.
To get credentials: run `npx -p robinhood-mcp robinhood-keygen`, register
the PUBLIC key at robinhood.com/account/crypto (web classic only), and
Robinhood issues the API key.  Keep the private key secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from datetime import date
from urllib.parse import urlparse

import urllib.request

from core import market
from core.broker import SimBroker, DUST
from core.models import Trade, TradeSide

BASE_URL = "https://trading.robinhood.com"
CRYPTO_SYMBOLS = {"BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD"}


def _sign(api_key: str, private_key_b64: str, method: str, path: str, body: bytes) -> dict:
    """Return auth headers for one request."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = base64.b64decode(private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest() if body else ""
    message = f"{api_key}\n{ts}\n{path}\n{method.upper()}\n{body_hash}"
    sig = base64.b64encode(private_key.sign(message.encode())).decode()
    return {"x-api-key": api_key, "x-signature": sig, "x-timestamp": ts,
            "Content-Type": "application/json"}


def _req(method: str, path: str, body: dict | None = None) -> dict:
    api_key = os.environ["ROBINHOOD_CRYPTO_API_KEY"]
    private_key = os.environ["ROBINHOOD_CRYPTO_PRIVATE_KEY"]
    raw = json.dumps(body).encode() if body else b""
    headers = _sign(api_key, private_key, method, path, raw)
    req = urllib.request.Request(BASE_URL + path, data=raw or None, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


class RobinhoodCryptoBroker:
    """Live crypto orders via the Robinhood Crypto Trading API.
    Equity symbols fall through to an embedded SimBroker so the fund still works."""

    mode = "Robinhood Crypto"

    def __init__(self) -> None:
        self._sim = SimBroker()

    # ------------------------------------------------------------------
    def submit(self, cid: str, symbol: str, side: TradeSide, notional: float, on: date) -> Trade:
        rh_sym = _to_rh(symbol)
        if rh_sym is None:
            return self._sim.submit(cid, symbol, side, notional, on)
        try:
            body = {
                "client_order_id": uuid.uuid4().hex,
                "side": side,
                "type": "market",
                "symbol": rh_sym,
                "market_order_config": {"asset_quantity": None, "quote_amount": str(round(notional, 2))},
            }
            resp = _req("POST", "/api/v1/crypto/trading/orders/", body)
            order_id = resp.get("id", "rh-" + uuid.uuid4().hex[:8])
            status = resp.get("state", "pending")
            fill_price = float(resp.get("average_price") or market.raw_price(symbol, on))
            self._sim.submit(cid, symbol, side, notional, on)  # keep local ledger in sync
            return Trade(symbol, side, round(notional, 2), status, order_id, round(fill_price, 2))
        except Exception as exc:
            # Fall back to sim so a bad credential doesn't crash the demo
            t = self._sim.submit(cid, symbol, side, notional, on)
            t = Trade(t.symbol, t.side, t.notional, f"sim(rh-err:{exc})", t.order_id, t.price)
            return t

    def positions(self, cid: str) -> dict[str, float]:
        try:
            resp = _req("GET", "/api/v1/crypto/trading/holdings/")
            results = resp.get("results", [])
            live = {r["asset_code"] + "-USD": float(r["quantity_available_for_trading"])
                    for r in results if float(r.get("quantity_available_for_trading", 0)) > DUST}
        except Exception:
            live = {}
        merged = dict(self._sim.positions(cid))
        merged.update(live)
        return merged

    def cost_basis(self, cid: str) -> dict[str, float]:
        return self._sim.cost_basis(cid)

    def orders(self, cid: str) -> list[dict]:
        return self._sim.orders(cid)

    def reset(self, cid: str | None = None) -> None:
        self._sim.reset(cid)


def _to_rh(symbol: str) -> str | None:
    """Convert app ticker to Robinhood symbol, or None if not a supported crypto."""
    s = symbol.upper()
    if s in CRYPTO_SYMBOLS:
        return s
    # bare tickers without -USD
    for pair in CRYPTO_SYMBOLS:
        if s == pair.split("-")[0]:
            return pair
    return None
