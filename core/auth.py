"""Accounts and sign-in: email + password with TOTP 2FA, and Sign in with Google / Apple (OpenID Connect).

Storage is SQLite (stdlib) at AUTH_DB (default data/users.db, gitignored). Passwords are hashed with
argon2id; recovery codes (high-entropy, random) are stored as SHA-256 hashes; TOTP secrets are stored as-is
because the server must read them to check codes (protect the DB file like a key).

Failed-attempt throttling is in memory: it resets on restart and isn't shared across server processes.
Fine for one demo server; a real deployment would keep it in Redis or the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "users.db"
ISSUER = "FirstFund"
MIN_PASSWORD = 10
RECOVERY_CODES = 8
TOTP_STEP = 30

# Throttling: failures within the window; past the limit, that key is locked until the window passes.
WINDOW_SECONDS = 15 * 60
ACCOUNT_LIMIT = 5      # wrong passwords or codes for one account
IP_LIMIT = 20          # wrong passwords or codes from one IP, across accounts

# Common passwords that meet the length rule; refuse them outright.
COMMON_PASSWORDS = {
    "password123", "password1234", "1234567890", "qwertyuiop", "123456789a", "iloveyou12",
    "passw0rd123", "letmein123", "welcome123", "admin12345", "qwerty1234", "abc1234567",
    "1q2w3e4r5t", "football123", "baseball123", "monkey12345", "sunshine123", "princess123",
    "firstfund123", "password!1", "changeme123", "11111111111", "0000000000", "aaaaaaaaaa",
}

_ph = PasswordHasher()   # argon2id with the library's current recommended parameters
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
# A dummy hash so a login for an unknown email costs the same as a real one (no timing leak).
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))


class AuthError(ValueError):
    """Shown to the user as-is, so it never says which part was wrong."""


# ---------- database ----------

def db_path() -> Path:
    return Path(os.getenv("AUTH_DB") or DEFAULT_DB)


@contextmanager
def _db():
    """A connection that commits on success, rolls back on error, and always closes."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,                -- NULL for accounts that only sign in with Google/Apple
            totp_secret TEXT,                  -- set once 2FA enrollment is confirmed
            totp_pending TEXT,                 -- secret being enrolled, not yet confirmed
            totp_last_step INTEGER DEFAULT 0,  -- last accepted TOTP time step (blocks code reuse)
            session_version INTEGER DEFAULT 1, -- bumped on logout: every older session cookie stops working
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identities (
            provider TEXT NOT NULL,
            subject TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            email TEXT,
            PRIMARY KEY (provider, subject)
        );
        CREATE TABLE IF NOT EXISTS recovery_codes (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL,
            used_at REAL
        );
    """)
    return conn


@dataclass
class User:
    id: int
    email: str
    password_hash: str | None
    totp_secret: str | None
    totp_pending: str | None
    totp_last_step: int
    session_version: int

    @property
    def has_2fa(self) -> bool:
        return bool(self.totp_secret)


def _user(row) -> User | None:
    if row is None:
        return None
    return User(row["id"], row["email"], row["password_hash"], row["totp_secret"], row["totp_pending"],
                row["totp_last_step"] or 0, row["session_version"] or 1)


def get_user(user_id: int) -> User | None:
    with _db() as c:
        return _user(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_email(email: str) -> User | None:
    with _db() as c:
        return _user(c.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone())


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------- passwords ----------

def check_password_rules(password: str, email: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise AuthError("That password is too long.")
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(password)) < 4:
        raise AuthError("That password is too easy to guess. Try a short phrase of random words.")
    local = normalize_email(email).split("@")[0]
    if local and len(local) >= 4 and local in low:
        raise AuthError("Don't use your email address in your password.")


def create_user(email: str, password: str) -> User:
    email = normalize_email(email)
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        raise AuthError("Enter a valid email address.")
    check_password_rules(password, email)
    try:
        with _db() as c:
            cur = c.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                            (email, _ph.hash(password), time.time()))
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Don't confirm whether the address is registered.
        raise AuthError("We couldn't create an account with that email. Try signing in instead.") from None
    return get_user(user_id)


def verify_password(email: str, password: str) -> User | None:
    """The user if the password matches, else None. Same work either way."""
    user = get_user_by_email(email)
    stored = user.password_hash if user and user.password_hash else _DUMMY_HASH
    try:
        ok = _ph.verify(stored, password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        ok = False
    if not (ok and user and user.password_hash):
        return None
    if _ph.check_needs_rehash(user.password_hash):
        with _db() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_ph.hash(password), user.id))
    return user


# ---------- TOTP ----------

def start_totp_enrollment(user: User) -> str:
    """A fresh secret for the user to scan; it only counts once confirm_totp_enrollment succeeds."""
    secret = pyotp.random_base32()
    with _db() as c:
        c.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (secret, user.id))
    return secret


def provisioning_uri(user: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)


def qr_svg_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#0a0a0a")


def _matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step the code belongs to (this one, or one either side for clock drift), else None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    step = int(now // TOTP_STEP)
    for s in (step - 1, step, step + 1):   # valid_window=1
        if hmac.compare_digest(totp.at(s * TOTP_STEP), code):
            return s
    return None


def confirm_totp_enrollment(user: User, code: str) -> list[str]:
    """Turn on 2FA if the code matches the pending secret. Returns new recovery codes (shown once)."""
    user = get_user(user.id)
    if not user.totp_pending:
        raise AuthError("Start setup again.")
    step = _matching_step(user.totp_pending, code)
    if step is None:
        raise AuthError("That code didn't work. Check your authenticator app's clock and try the newest code.")
    with _db() as c:
        c.execute("UPDATE users SET totp_secret = totp_pending, totp_pending = NULL, totp_last_step = ? WHERE id = ?",
                  (step, user.id))
    return new_recovery_codes(user)


def verify_totp(user: User, code: str) -> bool:
    """A current code that hasn't been used yet. Each accepted code (time step) works once."""
    user = get_user(user.id)
    if not user or not user.totp_secret:
        return False
    step = _matching_step(user.totp_secret, code)
    if step is None or step <= user.totp_last_step:
        return False
    with _db() as c:
        # Compare-and-set so two simultaneous requests with the same code can't both win.
        cur = c.execute("UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                        (step, user.id, step))
        return cur.rowcount == 1


# ---------- recovery codes ----------

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alikes (i, l, o, 0, 1)
    codes = ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
             for _ in range(RECOVERY_CODES)]
    with _db() as c:
        c.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user.id,))
        c.executemany("INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
                      [(user.id, _hash_code(code)) for code in codes])
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _hash_code(code or "")
    with _db() as c:
        rows = c.execute("SELECT rowid, code_hash FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                         (user.id,)).fetchall()
        for r in rows:
            if hmac.compare_digest(r["code_hash"], h):
                cur = c.execute("UPDATE recovery_codes SET used_at = ? WHERE rowid = ? AND used_at IS NULL",
                                (time.time(), r["rowid"]))
                return cur.rowcount == 1
    return False


def recovery_codes_left(user: User) -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                         (user.id,)).fetchone()[0]


# ---------- sessions ----------

def bump_session_version(user_id: int) -> None:
    """Sign out every session of this user (cookies carry the version they were issued with)."""
    with _db() as c:
        c.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))


# ---------- throttling ----------

def _recent(key: str, now: float) -> list[float]:
    times = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    _failures[key] = times
    return times


def locked_for(email: str, ip: str) -> int:
    """Seconds until this account/IP may try again (0 = not locked)."""
    now = time.time()
    with _lock:
        waits = []
        for key, limit in ((f"acct:{normalize_email(email)}", ACCOUNT_LIMIT), (f"ip:{ip}", IP_LIMIT)):
            times = _recent(key, now)
            if len(times) >= limit:
                waits.append(int(WINDOW_SECONDS - (now - times[-limit])) + 1)
        return max(waits, default=0)


def record_failure(email: str, ip: str) -> None:
    now = time.time()
    with _lock:
        for key in (f"acct:{normalize_email(email)}", f"ip:{ip}"):
            _recent(key, now).append(now)


def clear_failures(email: str) -> None:
    with _lock:
        _failures.pop(f"acct:{normalize_email(email)}", None)


def reset_throttles() -> None:
    with _lock:
        _failures.clear()


# ---------- redirects ----------

def safe_next(target: str | None, default: str = "/") -> str:
    """Only same-site relative paths: "/p/sam" yes; "//evil.com", "https://evil.com", "/\\evil" no."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or any(ord(ch) < 32 for ch in target):
        return default
    return target


# ---------- Sign in with Google / Apple (OpenID Connect) ----------

PROVIDERS = {
    "google": {
        "name": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "jwks": "https://www.googleapis.com/oauth2/v3/certs",
        "issuers": {"https://accounts.google.com", "accounts.google.com"},
        "scope": "openid email profile",
        "pkce": True,
        "callback_method": "GET",
    },
    "apple": {
        "name": "Apple",
        "authorize": "https://appleid.apple.com/auth/authorize",
        "token": "https://appleid.apple.com/auth/token",
        "jwks": "https://appleid.apple.com/auth/keys",
        "issuers": {"https://appleid.apple.com"},
        "scope": "name email",
        # Apple's web flow doesn't document PKCE; it relies on state + nonce + a signed client secret.
        "pkce": False,
        "callback_method": "POST",   # response_mode=form_post when asking for name/email
    },
}
PENDING_SECONDS = 600
_pending: dict[str, dict] = {}   # state -> {provider, nonce, verifier, binding, next, at}


def public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")


def redirect_uri(provider: str) -> str:
    return f"{public_base_url()}/auth/{provider}/callback"


def configured(provider: str) -> bool:
    if provider == "google":
        return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    if provider == "apple":
        return bool(os.getenv("APPLE_CLIENT_ID") and os.getenv("APPLE_TEAM_ID") and os.getenv("APPLE_KEY_ID")
                    and (os.getenv("APPLE_PRIVATE_KEY") or os.getenv("APPLE_PRIVATE_KEY_PATH")))
    return False


def client_id(provider: str) -> str:
    return os.environ["GOOGLE_CLIENT_ID" if provider == "google" else "APPLE_CLIENT_ID"]


def client_secret(provider: str) -> str:
    if provider == "google":
        return os.environ["GOOGLE_CLIENT_SECRET"]
    # Apple: the client secret is a short-lived ES256 JWT signed with the Sign in with Apple key.
    import jwt
    key = os.getenv("APPLE_PRIVATE_KEY") or Path(os.environ["APPLE_PRIVATE_KEY_PATH"]).read_text()
    now = int(time.time())
    return jwt.encode({"iss": os.environ["APPLE_TEAM_ID"], "iat": now, "exp": now + 300,
                       "aud": "https://appleid.apple.com", "sub": os.environ["APPLE_CLIENT_ID"]},
                      key.replace("\\n", "\n"), algorithm="ES256", headers={"kid": os.environ["APPLE_KEY_ID"]})


def start_oidc(provider: str, next_url: str) -> tuple[str, str]:
    """(URL to send the browser to, binding value to put in a short-lived cookie)."""
    cfg = PROVIDERS[provider]
    state, nonce, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64) if cfg["pkce"] else None
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_SECONDS]:
            _pending.pop(k, None)
        _pending[state] = {"provider": provider, "nonce": nonce, "verifier": verifier, "binding": binding,
                           "next": safe_next(next_url), "at": now}
    params = {"client_id": client_id(provider), "redirect_uri": redirect_uri(provider), "response_type": "code",
              "scope": cfg["scope"], "state": state, "nonce": nonce}
    if verifier:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "apple":
        params["response_mode"] = "form_post"
    if provider == "google":
        params["prompt"] = "select_account"
    return f"{cfg['authorize']}?{urlencode(params)}", binding


def take_pending(provider: str, state: str, binding: str | None) -> dict:
    """The pending sign-in for this state, checked against the browser's binding cookie. Single use."""
    with _lock:
        p = _pending.pop(state or "", None)
    if (not p or p["provider"] != provider or time.time() - p["at"] > PENDING_SECONDS
            or not binding or not hmac.compare_digest(p["binding"], binding)):
        raise AuthError("That sign-in link expired or didn't come from this browser. Please try again.")
    return p


def exchange_code(provider: str, code: str, verifier: str | None) -> dict:
    """Trade the authorization code for tokens at the provider's token endpoint."""
    import httpx
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider),
            "client_id": client_id(provider), "client_secret": client_secret(provider)}
    if verifier:
        data["code_verifier"] = verifier
    r = httpx.post(PROVIDERS[provider]["token"], data=data, timeout=10, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise AuthError(f"{PROVIDERS[provider]['name']} didn't accept the sign-in. Please try again.")
    return r.json()


_jwks_clients: dict[str, object] = {}


def _signing_key(provider: str, id_token: str):
    import jwt
    if provider not in _jwks_clients:
        _jwks_clients[provider] = jwt.PyJWKClient(PROVIDERS[provider]["jwks"], cache_keys=True, lifespan=3600)
    return _jwks_clients[provider].get_signing_key_from_jwt(id_token).key


def verify_id_token(provider: str, id_token: str, nonce: str) -> dict:
    """Signature (provider's published keys), issuer, audience, expiry and nonce."""
    import jwt
    try:
        claims = jwt.decode(id_token, _signing_key(provider, id_token), algorithms=["RS256"],
                            audience=client_id(provider), leeway=60,
                            options={"require": ["iss", "aud", "exp", "iat", "sub"]})
    except jwt.PyJWTError:
        raise AuthError("We couldn't verify that sign-in. Please try again.") from None
    if claims.get("iss") not in PROVIDERS[provider]["issuers"]:
        raise AuthError("We couldn't verify that sign-in. Please try again.")
    if not claims.get("nonce") or not hmac.compare_digest(str(claims["nonce"]), nonce):
        raise AuthError("We couldn't verify that sign-in. Please try again.")
    return claims


def user_for_identity(provider: str, claims: dict) -> User:
    """The FirstFund user for this Google/Apple account: an existing link, else a verified-email match, else new."""
    sub = str(claims["sub"])
    email = normalize_email(claims.get("email", ""))
    verified = str(claims.get("email_verified", "")).lower() == "true"
    with _db() as c:
        row = c.execute("SELECT user_id FROM identities WHERE provider = ? AND subject = ?", (provider, sub)).fetchone()
    if row:
        return get_user(row["user_id"])
    user = get_user_by_email(email) if email and verified else None
    with _db() as c:
        if user is None:
            if not email:
                raise AuthError(f"{PROVIDERS[provider]['name']} didn't share an email address, which we need.")
            if not verified:
                raise AuthError(f"Your {PROVIDERS[provider]['name']} email isn't verified yet.")
            cur = c.execute("INSERT INTO users (email, created_at) VALUES (?, ?)", (email, time.time()))
            user_id = cur.lastrowid
        else:
            user_id = user.id
        c.execute("INSERT INTO identities (provider, subject, user_id, email) VALUES (?, ?, ?, ?)",
                  (provider, sub, user_id, email))
    return get_user(user_id)


def linked_providers(user: User) -> list[str]:
    with _db() as c:
        return [r["provider"] for r in c.execute("SELECT provider FROM identities WHERE user_id = ?", (user.id,))]
