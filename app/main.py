"""FirstFund web app.  Run: uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.routes.pages import router  # noqa: E402
from core import feed, market  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    feed.prefetch(list(market.ETFS) + list(market.COMPANIES))   # real prices load in the background
    yield


app = FastAPI(title="FirstFund", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True}
