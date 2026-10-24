"""Nessie HTTP client behavior, offline: httpx.request is faked."""
import httpx
import pytest

from core import nessie_api


@pytest.fixture
def fake(monkeypatch):
    """Queue responses (status code, JSON body) or exceptions; records each call."""
    calls, queue = [], []

    def request(method, url, params=None, json=None, timeout=None):
        calls.append((method, url, json))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setattr(nessie_api.httpx, "request", request)
    monkeypatch.setattr(nessie_api.time, "sleep", lambda _: None)
    return calls, queue


def test_get_retries_5xx(fake):
    calls, queue = fake
    queue += [(503, {}), (200, {"_id": "a1", "balance": 100})]
    assert nessie_api.get_account("a1")["balance"] == 100
    assert len(calls) == 2


def test_post_not_retried_on_5xx(fake):
    calls, queue = fake
    queue += [(500, {"message": "boom"})]
    with pytest.raises(nessie_api.NessieError):
        nessie_api.create_deposit("a1", 100, "2026-10-30", "[payroll]")
    assert len(calls) == 1


def test_post_not_retried_after_timeout(fake):
    calls, queue = fake
    queue += [httpx.ReadTimeout("slow")]
    with pytest.raises(httpx.ReadTimeout):
        nessie_api.create_deposit("a1", 100, "2026-10-30", "[payroll]")
    assert len(calls) == 1


def test_post_retried_when_never_connected(fake):
    calls, queue = fake
    queue += [httpx.ConnectError("down"), (201, {"objectCreated": {"_id": "d1"}})]
    assert nessie_api.create_deposit("a1", 100, "2026-10-30", "[payroll]")["_id"] == "d1"
    assert len(calls) == 2


def test_4xx_raises_with_detail(fake):
    _, queue = fake
    queue += [(404, {"message": "Invalid ID"})]
    with pytest.raises(nessie_api.NessieError) as e:
        nessie_api.get_account("nope")
    assert e.value.status == 404 and e.value.body == {"message": "Invalid ID"}


def test_refuses_data_endpoint(fake):
    calls, _ = fake
    with pytest.raises(ValueError):
        nessie_api.request("DELETE", "/data")
    assert calls == []


def test_missing_key(monkeypatch):
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        nessie_api.get_account("a1")
