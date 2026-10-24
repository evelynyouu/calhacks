"""Sign-in pages and the gate in front of the rest of the app.  OWNER: Lance.

Pages: /login, /signup, /2fa/setup (enroll an authenticator app), /2fa (enter a code), POST /logout,
and /auth/{google|apple}/start + /auth/{provider}/callback for Sign in with Google / Apple.

Email sign-ins always need a TOTP code (or a one-time recovery code). Google and Apple sign-ins rely on
the provider's own 2-step verification, which the login page says.

Sessions are a signed cookie (Starlette SessionMiddleware): HttpOnly, SameSite=Lax, Secure when HTTPS_ONLY=1.
A session is "full" only after the second step. It ends after IDLE_SECONDS without a request or
ABSOLUTE_SECONDS after sign-in, and logout bumps the user's session version so every copy of the cookie dies.

Use current_user(request) to show who's signed in; POST /logout (with csrf_token(request)) signs out.
Off by default for now: LOGIN_ENABLED=1 turns sign-in on (then every page needs it).
AUTH_DISABLED=1 forces it off (tests).
"""
from __future__ import annotations

import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from core import auth
from core.auth import AuthError

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

IDLE_SECONDS = 2 * 3600
ABSOLUTE_SECONDS = 12 * 3600
PENDING_SECONDS = 5 * 60          # between the password step and the code step
ENROLL_SECONDS = 15 * 60          # between creating an account and confirming the authenticator app
OIDC_COOKIE = "ff_oidc"
SIGN_IN_PATHS = ("/login", "/signup", "/2fa", "/logout", "/auth/")
OPEN_PATHS = SIGN_IN_PATHS + ("/static/", "/health")


# ---------- session helpers ----------

def auth_disabled() -> bool:
    """Sign-in is off unless LOGIN_ENABLED=1 (switched off for now, 2026-10-24). AUTH_DISABLED=1 forces it off (tests)."""
    return os.getenv("AUTH_DISABLED") == "1" or os.getenv("LOGIN_ENABLED") != "1"


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def _check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(403, "Your session expired. Reload the page and try again.")


def current_user(request: Request) -> auth.User | None:
    """The signed-in user (after both steps), or None."""
    s = request.session
    if s.get("stage") != "full" or not s.get("uid"):
        return None
    now = time.time()
    if now - s.get("auth_at", 0) > ABSOLUTE_SECONDS or now - s.get("seen_at", 0) > IDLE_SECONDS:
        return None
    user = auth.get_user(s["uid"])
    if not user or user.session_version != s.get("ver"):
        return None
    return user


def _sign_in(request: Request, user: auth.User) -> None:
    """A brand-new session (new CSRF token too), so nothing from before sign-in carries over."""
    request.session.clear()
    now = time.time()
    request.session.update(uid=user.id, ver=user.session_version, stage="full", auth_at=now, seen_at=now,
                           csrf=secrets.token_urlsafe(32))


def _pending_user(request: Request, stage: str, max_age: int) -> auth.User | None:
    s = request.session
    if s.get("stage") != stage or time.time() - s.get("pending_at", 0) > max_age:
        return None
    return auth.get_user(s.get("pending_uid", 0))


def _ip(request: Request) -> str:
    if os.getenv("TRUST_PROXY") == "1":   # behind Render's proxy: the client is the first X-Forwarded-For hop
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    ctx |= {"csrf": csrf_token(request), "google_ready": auth.configured("google"),
            "apple_ready": auth.configured("apple")}
    r = templates.TemplateResponse(request, f"auth/{name}", ctx, status_code=status)
    r.headers["Cache-Control"] = "no-store"
    return r


def _wait_text(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return f"Too many tries. Wait {minutes} minute{'s' if minutes != 1 else ''} and try again."


# ---------- the gate ----------

async def gate(request: Request, call_next):
    """Everything except sign-in pages, static files and /health needs a full session."""
    path = request.url.path
    if auth_disabled():   # no sign-in at all: the sign-in pages don't exist and everything else is open
        if path.startswith(SIGN_IN_PATHS):
            return Response("Not found", status_code=404)
        return await call_next(request)
    if path.startswith(OPEN_PATHS):
        return await call_next(request)
    user = current_user(request)
    if user is None:
        target = path + (f"?{request.url.query}" if request.url.query else "")
        login = "/login" if target == "/" else f"/login?next={_quote(target)}"
        if request.headers.get("hx-request"):
            return Response(status_code=200, headers={"HX-Redirect": login})
        return RedirectResponse(login, status_code=303)
    request.session["seen_at"] = time.time()
    request.state.user = user
    return await call_next(request)


def _quote(path: str) -> str:
    return quote(path, safe="/")


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")          # no clickjacking the login form
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ---------- email + password ----------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    if current_user(request):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return _page(request, "login.html", next=auth.safe_next(next), error=error)


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/"),
          csrf: str = Form("")):
    _check_csrf(request, csrf)
    nxt, ip = auth.safe_next(next), _ip(request)
    wait = auth.locked_for(email, ip)
    if wait:
        return _page(request, "login.html", 429, next=nxt, email=email, error=_wait_text(wait))
    user = auth.verify_password(email, password)
    if not user:
        auth.record_failure(email, ip)
        return _page(request, "login.html", 400, next=nxt, email=email,
                     error="That email and password don't match an account.")
    request.session.clear()
    request.session.update(pending_uid=user.id, pending_at=time.time(), next=nxt, csrf=secrets.token_urlsafe(32),
                           stage="2fa" if user.has_2fa else "enroll")
    return RedirectResponse("/2fa" if user.has_2fa else "/2fa/setup", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, next: str = "/"):
    return _page(request, "signup.html", next=auth.safe_next(next))


@router.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(""), password: str = Form(""), confirm: str = Form(""),
           next: str = Form("/"), csrf: str = Form("")):
    _check_csrf(request, csrf)
    nxt = auth.safe_next(next)
    if password != confirm:
        return _page(request, "signup.html", 400, next=nxt, email=email, error="The passwords don't match.")
    try:
        user = auth.create_user(email, password)
    except AuthError as e:
        return _page(request, "signup.html", 400, next=nxt, email=email, error=str(e))
    request.session.clear()
    request.session.update(pending_uid=user.id, pending_at=time.time(), stage="enroll", next=nxt,
                           csrf=secrets.token_urlsafe(32))
    return RedirectResponse("/2fa/setup", status_code=303)


# ---------- 2FA ----------

@router.get("/2fa/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    user = _pending_user(request, "enroll", ENROLL_SECONDS)
    if not user:
        return RedirectResponse("/login?error=Please+sign+in+again.", status_code=303)
    secret = user.totp_pending or auth.start_totp_enrollment(user)   # a reload keeps the QR they already scanned
    uri = auth.provisioning_uri(user, secret)
    return _page(request, "setup.html", email=user.email, qr=auth.qr_svg_data_uri(uri),
                 key=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))


@router.post("/2fa/setup", response_class=HTMLResponse)
def setup(request: Request, code: str = Form(""), csrf: str = Form("")):
    _check_csrf(request, csrf)
    user = _pending_user(request, "enroll", ENROLL_SECONDS)
    if not user:
        return RedirectResponse("/login?error=Please+sign+in+again.", status_code=303)
    try:
        codes = auth.confirm_totp_enrollment(user, code)
    except AuthError as e:
        pending = auth.get_user(user.id).totp_pending
        return _page(request, "setup.html", 400, email=user.email, error=str(e),
                     qr=auth.qr_svg_data_uri(auth.provisioning_uri(user, pending)) if pending else "",
                     key=" ".join(pending[i:i + 4] for i in range(0, len(pending), 4)) if pending else "")
    nxt = auth.safe_next(request.session.get("next", "/"))
    _sign_in(request, auth.get_user(user.id))
    # Shown once, in this response; only hashes are stored.
    return _page(request, "recovery.html", codes=codes, next=nxt)


@router.get("/2fa", response_class=HTMLResponse)
def verify_page(request: Request):
    user = _pending_user(request, "2fa", PENDING_SECONDS)
    if not user:
        return RedirectResponse("/login?error=Please+sign+in+again.", status_code=303)
    return _page(request, "verify.html", email=user.email)


@router.post("/2fa", response_class=HTMLResponse)
def verify(request: Request, code: str = Form(""), csrf: str = Form("")):
    _check_csrf(request, csrf)
    user = _pending_user(request, "2fa", PENDING_SECONDS)
    if not user:
        return RedirectResponse("/login?error=Please+sign+in+again.", status_code=303)
    ip = _ip(request)
    wait = auth.locked_for(user.email, ip)
    if wait:
        return _page(request, "verify.html", 429, email=user.email, error=_wait_text(wait))
    digits = "".join(ch for ch in code if ch.isdigit())
    used_recovery = False
    if len(digits) == 6 and len(code.strip().replace(" ", "")) == 6:
        ok = auth.verify_totp(user, digits)
    else:
        ok = used_recovery = auth.use_recovery_code(user, code)
    if not ok:
        auth.record_failure(user.email, ip)
        return _page(request, "verify.html", 400, email=user.email,
                     error="That code didn't work. Codes change every 30 seconds and each one works once.")
    auth.clear_failures(user.email)
    nxt = auth.safe_next(request.session.get("next", "/"))
    _sign_in(request, user)
    if used_recovery:
        left = auth.recovery_codes_left(user)
        return _page(request, "recovery_used.html", left=left, next=nxt)
    return RedirectResponse(nxt, status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    _check_csrf(request, csrf)
    user = current_user(request)
    if user:
        auth.bump_session_version(user.id)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- Sign in with Google / Apple ----------

def _binding_cookie_args() -> dict:
    """Apple posts the callback cross-site, which drops SameSite=Lax cookies, so the browser binding
    cookie is SameSite=None; that requires Secure (fine on https and on localhost)."""
    base = auth.public_base_url()
    secure = base.startswith("https://") or "://localhost" in base or "://127.0.0.1" in base
    return {"httponly": True, "secure": secure, "samesite": "none" if secure else "lax", "path": "/auth/",
            "max_age": auth.PENDING_SECONDS}


@router.get("/auth/{provider}/start")
def oidc_start(provider: str, next: str = "/"):
    if provider not in auth.PROVIDERS:
        raise HTTPException(404)
    if not auth.configured(provider):
        name = auth.PROVIDERS[provider]["name"]
        return RedirectResponse(f"/login?error=Sign+in+with+{name}+isn't+set+up+on+this+server.", status_code=303)
    url, binding = auth.start_oidc(provider, next)
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(OIDC_COOKIE, binding, **_binding_cookie_args())
    return r


def _finish_oidc(request: Request, provider: str, code: str, state: str, error: str) -> Response:
    try:
        if error:
            raise AuthError(f"{auth.PROVIDERS[provider]['name']} sign-in was cancelled.")
        pending = auth.take_pending(provider, state, request.cookies.get(OIDC_COOKIE))
        if not code:
            raise AuthError("That sign-in didn't finish. Please try again.")
        tokens = auth.exchange_code(provider, code, pending["verifier"])
        claims = auth.verify_id_token(provider, tokens.get("id_token", ""), pending["nonce"])
        user = auth.user_for_identity(provider, claims)
    except AuthError as e:
        r = _page(request, "login.html", 400, next="/", error=str(e))
        r.delete_cookie(OIDC_COOKIE, path="/auth/")
        return r
    _sign_in(request, user)
    r = RedirectResponse(pending["next"], status_code=303)
    r.delete_cookie(OIDC_COOKIE, path="/auth/")
    return r


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    return _finish_oidc(request, "google", code, state, error)


@router.post("/auth/apple/callback")
def apple_callback(request: Request, code: str = Form(""), state: str = Form(""), error: str = Form("")):
    return _finish_oidc(request, "apple", code, state, error)
