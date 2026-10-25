"""Sign-in: email + password + TOTP, recovery codes, throttling, the gate, CSRF, safe redirects,
and the Google/Apple callbacks (token exchange and keys are mocked; no network)."""
import re
import time

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.main import app
from core import auth

PASSWORD = "maple river lantern 42"


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("LOGIN_ENABLED", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID",
              "APPLE_PRIVATE_KEY", "APPLE_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    auth.reset_throttles()
    auth._pending.clear()
    yield
    auth.reset_throttles()


def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def csrf(c: TestClient, path: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def signup(c: TestClient, email="sam@example.com") -> tuple[str, list[str]]:
    """Sign up and enroll 2FA; returns (TOTP secret, recovery codes). Leaves the client signed in."""
    r = c.post("/signup", data={"email": email, "password": PASSWORD, "confirm": PASSWORD, "csrf": csrf(c, "/signup")})
    assert r.status_code == 303 and r.headers["location"] == "/2fa/setup"
    page = c.get("/2fa/setup").text
    assert "data:image/svg+xml" in page
    secret = re.search(r'select-all[^>]*>([A-Z2-7 ]+)</code>', page).group(1).replace(" ", "")
    r = c.post("/2fa/setup", data={"code": pyotp.TOTP(secret).now(), "csrf": csrf(c, "/2fa/setup")})
    assert r.status_code == 200 and "Save your recovery codes" in r.text
    codes = re.findall(r'<li class="select-all">([a-z0-9-]+)</li>', r.text)
    return secret, codes


def code_for_next_step(secret: str) -> str:
    """A code from the next 30-second step: the current one was used during enrollment."""
    return pyotp.TOTP(secret).at(time.time() + 30)


def login(c: TestClient, email="sam@example.com", password=PASSWORD):
    return c.post("/login", data={"email": email, "password": password, "csrf": csrf(c, "/login")})


def test_gate_redirects_to_login_with_next():
    r = client().get("/p/sam/markets?s=AAPL")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/p/sam/markets%3Fs%3DAAPL"
    assert client().get("/").headers["location"] == "/login"
    assert client().get("/health").status_code == 200
    assert client().get("/static/app.css").status_code == 200


def test_htmx_requests_get_hx_redirect():
    r = client().get("/p/sam/tape", headers={"HX-Request": "true"})
    assert r.headers["HX-Redirect"].startswith("/login")


def test_signup_enroll_then_signed_in():
    c = client()
    _, codes = signup(c)
    assert len(codes) == 8 and len(set(codes)) == 8
    assert c.get("/").status_code == 200


def test_login_needs_password_then_code():
    c = client()
    secret, _ = signup(c)
    c = client()                                               # a fresh browser
    r = login(c)
    assert r.headers["location"] == "/2fa"
    assert c.get("/").status_code == 303                       # not signed in until the code step
    r = c.post("/2fa", data={"code": code_for_next_step(secret), "csrf": csrf(c, "/2fa")})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200


def test_wrong_password_is_generic():
    c = client()
    signup(c)
    c = client()
    wrong = login(c, password="not the password at all")
    unknown = login(client(), email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 400
    assert "match an account" in wrong.text and "match an account" in unknown.text


def test_wrong_and_reused_totp_rejected():
    c = client()
    secret, _ = signup(c)
    c = client()
    login(c)
    assert c.post("/2fa", data={"code": "000000", "csrf": csrf(c, "/2fa")}).status_code == 400
    code = code_for_next_step(secret)
    assert c.post("/2fa", data={"code": code, "csrf": csrf(c, "/2fa")}).status_code == 303
    c2 = client()
    login(c2)
    r = c2.post("/2fa", data={"code": code, "csrf": csrf(c2, "/2fa")})   # same code again
    assert r.status_code == 400


def test_recovery_code_works_once():
    c = client()
    _, codes = signup(c)
    c = client()
    login(c)
    r = c.post("/2fa", data={"code": codes[0].upper(), "csrf": csrf(c, "/2fa")})
    assert r.status_code == 200 and "<strong>7</strong> left" in r.text
    c = client()
    login(c)
    assert c.post("/2fa", data={"code": codes[0], "csrf": csrf(c, "/2fa")}).status_code == 400


def test_lockout_after_repeated_failures():
    c = client()
    signup(c)
    c = client()
    for _ in range(auth.ACCOUNT_LIMIT):
        assert login(c, password="wrong wrong wrong").status_code == 400
    r = login(c)                                              # even the right password waits now
    assert r.status_code == 429 and "Too many tries" in r.text


def test_csrf_required():
    c = client()
    c.get("/login")
    r = c.post("/login", data={"email": "a@b.co", "password": "x", "csrf": "forged"})
    assert r.status_code == 403
    assert client().post("/logout").status_code == 403


def test_open_redirects_rejected():
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "javascript:alert(1)", "http:/x"):
        assert auth.safe_next(bad) == "/"
    assert auth.safe_next("/p/sam/news?feed=market") == "/p/sam/news?feed=market"
    page = client().get("/login?next=//evil.com").text
    assert 'name="next" value="/"' in page


def test_weak_passwords_rejected():
    c = client()
    for pw in ("short", "password123", "aaaaaaaaaaaaa", "samsmith-secret-1"):
        r = c.post("/signup", data={"email": "samsmith@example.com", "password": pw, "confirm": pw,
                                    "csrf": csrf(c, "/signup")})
        assert r.status_code == 400, pw


def test_logout_ends_every_copy_of_the_session():
    c = client()
    signup(c)
    stolen = dict(c.cookies)
    assert c.get("/").status_code == 200
    assert c.post("/logout", data={"csrf": _session_csrf(c)}).headers["location"] == "/login"
    replay = TestClient(app, follow_redirects=False, cookies=stolen)
    assert replay.get("/").status_code == 303


def _session_csrf(c: TestClient) -> str:
    """Read the CSRF token out of the signed session cookie (the integrator's logout form renders it)."""
    import base64
    import json
    raw = c.cookies.get("ff_session").split(".")[0]
    return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))["csrf"]


def test_social_buttons_disabled_when_unconfigured():
    page = client().get("/login").text
    assert 'aria-disabled="true"' in page and "Continue with Apple" in page and "Continue with Google" in page
    assert "aren't set up on this server" in page
    r = client().get("/auth/google/start")
    assert r.headers["location"].startswith("/login?error=")


# ---------- Google / Apple with mocked provider ----------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setattr(auth, "_signing_key", lambda provider, token: KEY.public_key())
    tokens = {}
    monkeypatch.setattr(auth, "exchange_code", lambda provider, code, verifier: tokens)
    return tokens


def _id_token(nonce, **claims):
    now = int(time.time())
    body = {"iss": "https://accounts.google.com", "aud": "client-123", "sub": "g-1", "iat": now, "exp": now + 300,
            "nonce": nonce, "email": "sam@example.com", "email_verified": True} | claims
    return jwt.encode(body, KEY, algorithm="RS256")


def _start(c):
    r = c.get("/auth/google/start?next=/p/sam")
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    params = dict(p.split("=", 1) for p in r.headers["location"].split("?", 1)[1].split("&"))
    assert params["code_challenge_method"] == "S256"
    return params["state"], auth._pending[params["state"]]["nonce"]


def test_google_sign_in_completes(google):
    c = client()
    state, nonce = _start(c)
    google["id_token"] = _id_token(nonce)
    r = c.get(f"/auth/google/callback?code=abc&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/p/sam"
    assert c.get("/").status_code == 200


def test_google_callback_rejects_bad_state_nonce_and_audience(google):
    c = client()
    state, nonce = _start(c)
    assert c.get("/auth/google/callback?code=abc&state=forged").status_code == 400
    state, nonce = _start(c)
    google["id_token"] = _id_token("wrong-nonce")
    assert c.get(f"/auth/google/callback?code=abc&state={state}").status_code == 400
    state, nonce = _start(c)
    google["id_token"] = _id_token(nonce, aud="someone-else")
    assert c.get(f"/auth/google/callback?code=abc&state={state}").status_code == 400
    state, nonce = _start(client())                            # started in a different browser
    google["id_token"] = _id_token(nonce)
    assert c.get(f"/auth/google/callback?code=abc&state={state}").status_code == 400
    assert c.get("/").status_code == 303


def test_apple_callback_rejects_bad_state(monkeypatch):
    monkeypatch.setenv("APPLE_CLIENT_ID", "com.firstfund.web")
    monkeypatch.setenv("APPLE_TEAM_ID", "TEAM")
    monkeypatch.setenv("APPLE_KEY_ID", "KEY")
    monkeypatch.setenv("APPLE_PRIVATE_KEY", "unused")
    c = client()
    r = c.get("/auth/apple/start")
    assert "response_mode=form_post" in r.headers["location"]
    r = c.post("/auth/apple/callback", data={"code": "abc", "state": "forged"})
    assert r.status_code == 400 and "expired" in r.text


def test_login_switched_off_by_default_removes_the_pages_and_the_gate(monkeypatch):
    monkeypatch.delenv("LOGIN_ENABLED")
    c = TestClient(app)
    assert c.get("/login").status_code == 404 and c.get("/signup").status_code == 404
    r = c.get("/p/sam/link", follow_redirects=False)   # a persona's first stop: the demo bank link
    assert r.status_code == 200 and "Sign out" not in r.text
