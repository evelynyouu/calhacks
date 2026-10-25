"""Onboarding: start as a new customer, then link a (demo) bank before the intake.  OWNER: Lance.

/start                      the new-customer form (makes a mock bank customer, see core/onboarding.py)
/p/{cid}/link?step=...      the Plaid-style link: choose bank -> "sign in" (one click, no password) ->
                            pick accounts + consent; POST links and opens the intake chat at /p/{cid}
Everything here is a demo with a mock bank (Capital One's Nessie sandbox); no real credentials, ever.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.routes.pages import _session, templates
from core import nessie, onboarding
from core.profile import get_profile

router = APIRouter()

STEPS = ("bank", "signin", "accounts")
# Shown greyed out on the bank step: the demo can't link them, and says so.
NOT_IN_DEMO = ["Chase", "Bank of America", "Wells Fargo"]


def _mock_source() -> str:
    if nessie.mode() == nessie.LIVE:
        return "Capital One's Nessie sandbox (a test bank with made-up money)"
    return "in-memory demo data shaped like Capital One's Nessie sandbox (made-up money)"


# ---------- new customer ----------

def _start_page(request: Request, form: dict, errors: dict, status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "start.html", {
        "form": form, "errors": errors, "frequencies": onboarding.FREQUENCIES,
        "default_frequency": onboarding.DEFAULT_FREQUENCY, "mock_source": _mock_source()}, status_code=status)


@router.get("/start", response_class=HTMLResponse)
def start_form(request: Request):
    return _start_page(request, {}, {})


@router.post("/start")
def start(request: Request, name: str = Form(""), paycheck: str = Form(""), frequency: str = Form(""),
          checking: str = Form(""), savings: str = Form(""), bills: str = Form("")):
    form = {"name": name, "paycheck": paycheck, "frequency": frequency, "checking": checking,
            "savings": savings, "bills": bills}
    try:
        cid = onboarding.create(onboarding.parse_form(**form))
    except onboarding.SignupError as e:
        return _start_page(request, form, e.errors, 400)
    except onboarding.BankUnavailable as e:
        return _start_page(request, form, {"bank": f"The demo bank (Nessie sandbox) didn't answer: {e}. "
                                                   "Try again in a moment."}, 503)
    return RedirectResponse(f"/p/{cid}/link", status_code=303)


# ---------- the bank link ----------

@router.get("/p/{cid}/link", response_class=HTMLResponse)
def link_page(request: Request, cid: str, step: str = "bank"):
    s = _session(cid)   # 404 for an unknown customer
    if s.bank_linked:   # already linked, or a fund already running (Alex): nothing to link
        return RedirectResponse(f"/p/{cid}", status_code=303)
    step = step if step in STEPS else "bank"
    profile = get_profile(cid)
    ctx = {"cid": cid, "profile": profile, "step": step, "step_no": STEPS.index(step) + 1,
           "steps": len(STEPS), "bank": onboarding.BANK, "bank_note": onboarding.BANK_NOTE,
           "not_in_demo": NOT_IN_DEMO, "mock_source": _mock_source(),
           "frequency": onboarding.frequency_label(cid)}
    if step == "accounts":
        ctx["accounts"] = onboarding.accounts(cid)
    return templates.TemplateResponse(request, "link.html", ctx)


@router.post("/p/{cid}/link")
def link(cid: str, checking: str = Form("checking"), investing: str = Form("investing")):
    s = _session(cid)
    if s.done:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    if (checking, investing) != ("checking", "investing"):
        raise HTTPException(400, "Pick your checking account and FirstFund Investing")
    if not s.linked:
        s.linked = onboarding.link(cid)
    return RedirectResponse(f"/p/{cid}", status_code=303)
