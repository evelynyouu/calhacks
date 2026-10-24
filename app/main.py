"""FirstFund web app.  Run: uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402
import secrets  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.routes import auth as auth_routes  # noqa: E402
from app.routes.pages import router  # noqa: E402
from core import feed, market  # noqa: E402

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
    print("\n*** WARNING: SESSION_SECRET is not set. Using a random one, so every restart signs everyone out.\n"
          "*** Set SESSION_SECRET in .env (python -c 'import secrets; print(secrets.token_urlsafe(48))').\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    feed.prefetch(list(market.ETFS) + list(market.COMPANIES))   # real prices load in the background
    yield


app = FastAPI(title="FirstFund", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(auth_routes.router)
app.include_router(router)
# Middleware added last runs first: security headers -> session cookie -> sign-in gate -> routes.
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_routes.gate)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="ff_session",
                   max_age=auth_routes.ABSOLUTE_SECONDS, same_site="lax",
                   https_only=os.getenv("HTTPS_ONLY") == "1")
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_routes.security_headers)


@app.get("/health")
def health():
    return {"ok": True}
