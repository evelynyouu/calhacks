"""Every test starts in fixture mode (bank) and sim mode (market) with its ids/state/cache files in a temp dir and real HTTP
blocked, so running pytest with NESSIE_MODE=live in the environment can never touch the shared
Nessie key or the real data/ files. Tests that want live mode opt in (see tests/test_live.py)."""
import pytest

import agent.claude_agent as claude_agent
from app import state as app_state
from core import feed, market, nessie, profile, seed, state
from core.broker import get_broker


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    monkeypatch.setenv("MARKET_DATA", "sim")   # no real prices or news in tests: simulated, deterministic
    monkeypatch.setenv("BROKER", "sim")        # no real broker calls in tests
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # app.main loads .env: tests must never call (or pay for) the real Claude API
    monkeypatch.setattr(claude_agent, "_client", None)
    monkeypatch.setenv("AUTH_DISABLED", "1")   # skip the sign-in gate; tests/test_auth.py turns it back on
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "users.db"))
    monkeypatch.setattr(feed, "CACHE_DIR", tmp_path / "prices")
    monkeypatch.setattr(nessie, "IDS_PATH", tmp_path / "nessie_ids.json")
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(profile, "CACHE_DIR", tmp_path / "cache")

    def no_network(*args, **kwargs):
        raise RuntimeError("real HTTP is blocked in tests; patch nessie._http or nessie._send")

    monkeypatch.setattr(nessie, "_http", no_network)
    monkeypatch.setattr(nessie, "_registry", {"account": None, "pulled": False})
    nessie.clear_cache()
    nessie.go_online()   # a test that simulated an outage must not leave the app "offline"
    nessie.forget_new_customers()   # customers made with "Start as a new customer"
    nessie.reset()
    import core.broker as _broker_mod
    _broker_mod._broker = None        # force re-init as SimBroker under BROKER=sim
    _broker_mod._broker_mode = None
    app_state._SESSIONS.clear()   # chat sessions, simulated trades and market shocks are in memory too
    get_broker().reset()
    market.clear_shocks()
    yield
    seed.PAUSE_BACKGROUND = False
    seed.wait_for_background()   # a background rebuild must finish inside the test that started it
    nessie.go_online()
    nessie.forget_new_customers()
    nessie.reset()
