"""All page + HTMX routes.  OWNER: Ethan.

Full pages: /, /p/{cid}, /p/{cid}/report
HTMX endpoints return partials; extra panels refresh with hx-swap-oob.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent import claude_agent
from app import state
from core import nessie
from core.models import to_dict
from core.payday import run_payday
from core.policy import tier_for
from core.portfolio import build_portfolio
from core.profile import get_profile
from core.readiness import check_readiness
from core.sim import crash_test, simulate

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["money"] = lambda v: f"${v:,.0f}"
templates.env.filters["pct"] = lambda v, d=0: f"{v * 100:.{d}f}%"

# Personas that start with a fund so the demo can jump straight to them
DEMO_DEFAULT_FUNDS = {"alex": (3, ["healthcare"])}


def _ctx(request: Request, cid: str, **extra) -> dict:
    try:
        profile = get_profile(cid)
    except KeyError:
        raise HTTPException(404, "Unknown persona")
    s = state.get(cid)
    if s.portfolio is None and cid in DEMO_DEFAULT_FUNDS:
        s.portfolio = build_portfolio(*DEMO_DEFAULT_FUNDS[cid])
    readiness = check_readiness(profile)
    sim = None
    if s.portfolio:
        sim = simulate(s.portfolio, profile.invested_balance, readiness.safe_monthly_amount)
    return {
        "request": request, "cid": cid, "profile": profile, "readiness": readiness,
        "tier": tier_for(profile.invested_balance), "s": s, "portfolio": s.portfolio, "sim": sim,
        "pie_data": _pie(s.portfolio), "fan_data": to_dict(sim) if sim else None,
        "activity": nessie.recent_activity(cid), "nessie_mode": nessie.mode(),
        **extra,
    }


def _pie(p):
    if not p:
        return None
    return {"labels": [h.ticker for h in p.holdings], "values": [round(h.weight * 100, 1) for h in p.holdings]}


def _render(name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(ctx["request"], name, ctx)


# ---------- Pages ----------

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"personas": nessie.list_customers()})


@router.get("/p/{cid}", response_class=HTMLResponse)
def dashboard(request: Request, cid: str):
    return _render("dashboard.html", _ctx(request, cid))


@router.get("/p/{cid}/report", response_class=HTMLResponse)
def report(request: Request, cid: str):
    ctx = _ctx(request, cid)
    p, r, t = ctx["profile"], ctx["readiness"], ctx["tier"]
    facts = [
        f"Invested balance: ${p.invested_balance:,.0f}. Tier {t.tier}, checked {t.check_frequency.lower()}.",
        f"Readiness: {r.headline}.",
        *r.reasons,
        *([f"Fund: {ctx['portfolio'].risk_label}, yearly fee {ctx['portfolio'].annual_fee_pct:.2f}%."]
          if ctx["portfolio"] else []),
        *(ctx["s"].last_payday.reasons if ctx["s"].last_payday else []),
    ]
    ctx["report_text"] = claude_agent.explain(
        facts, f"Write {p.name}'s monthly FirstFund report: a friendly 2-paragraph email, then 3 short recommendations.")
    ctx["report_facts"] = facts
    return _render("report.html", ctx)


@router.post("/p/{cid}/reset")
def reset(cid: str):
    nessie.reset(cid)
    state.reset(cid)
    return RedirectResponse(f"/p/{cid}", status_code=303)


# ---------- Chat ----------

@router.post("/p/{cid}/chat", response_class=HTMLResponse)
def chat(request: Request, cid: str, message: str = Form("")):
    s = state.get(cid)
    text = message.strip() or "Hi! I just connected my bank."
    if message.strip():
        s.transcript.append({"role": "user", "text": text})
    reply, events = claude_agent.chat_turn(s, text)
    s.transcript.append({"role": "assistant", "text": reply})
    ctx = _ctx(request, cid, new_messages=s.transcript[-2:] if message.strip() else s.transcript[-1:],
               oob=events)
    return _render("partials/chat_turn.html", ctx)


# ---------- Crash test ----------

@router.post("/p/{cid}/crash/{choice}", response_class=HTMLResponse)
def crash(request: Request, cid: str, choice: str):
    if choice not in ("sell", "hold", "buy"):
        raise HTTPException(400)
    s = state.get(cid)
    if not s.portfolio:
        raise HTTPException(400, "Build a fund first")
    balance = max(get_profile(cid).invested_balance, 1000.0)
    s.crash = crash_test(s.portfolio, balance, choice, s.intake.get("interests"))
    if s.crash.new_portfolio:
        s.portfolio = s.crash.new_portfolio
    explanation = claude_agent.explain(
        s.crash.explanation_facts, "The user just took a crash test. Explain in 2 sentences what happens and why.")
    return _render("partials/crash_result.html", _ctx(request, cid, explanation=explanation, oob={"fund"}))


# ---------- Life events (Evelyn's functions behind the buttons) ----------

EVENTS = {
    "payday": "Fast-forward to payday",
    "car_repair": "Unexpected $600 car repair",
    "raise": "Got a 10% raise",
    "market_drop": "Market drops 30%",
}


@router.post("/p/{cid}/event/{name}", response_class=HTMLResponse)
def life_event(request: Request, cid: str, name: str):
    if name not in EVENTS:
        raise HTTPException(400)
    s = state.get(cid)
    if name == "payday":
        days = get_profile(cid).days_to_payday
        nessie.advance_days(cid, days)
        s.last_payday = run_payday(cid, s.portfolio)
        s.last_payday_explanation = claude_agent.explain(
            s.last_payday.reasons, "Explain what FirstFund just did on payday in 2 short sentences.")
    elif name == "car_repair":
        nessie.record_purchase(cid, "Car repair", 600)
    elif name == "raise":
        nessie.apply_raise(cid, 10)
    elif name == "market_drop":
        nessie.apply_market_move(cid, -30)
    s.events.insert(0, EVENTS[name])
    return _render("partials/money.html", _ctx(request, cid, oob={"readiness", "fund", "tier"}))
