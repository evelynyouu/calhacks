"""FirstFund web app.  Run: uvicorn app.main:app --reload"""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.routes.pages import router  # noqa: E402

app = FastAPI(title="FirstFund")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True}
