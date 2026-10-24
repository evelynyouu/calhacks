"""Every test starts in fixture mode with its ids/state/cache files in a temp dir and real HTTP
blocked, so running pytest with NESSIE_MODE=live in the environment can never touch the shared
Nessie key or the real data/ files. Tests that want live mode opt in (see tests/test_live.py)."""
import pytest

from core import nessie, profile, seed, state


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("NESSIE_MODE", "fixture")
    monkeypatch.setattr(nessie, "IDS_PATH", tmp_path / "nessie_ids.json")
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(profile, "CACHE_DIR", tmp_path / "cache")

    def no_network(*args, **kwargs):
        raise RuntimeError("real HTTP is blocked in tests; patch nessie._http or nessie._send")

    monkeypatch.setattr(nessie, "_http", no_network)
    nessie.clear_cache()
    nessie.go_online()   # a test that simulated an outage must not leave the app "offline"
    nessie.reset()
    yield
    seed.PAUSE_BACKGROUND = False
    seed.wait_for_background()   # a background rebuild must finish inside the test that started it
    nessie.go_online()
    nessie.reset()
