"""Target proposals: Accept / Not now on the Portfolio card, "Check my targets", and changed risk answers from the
Account tab.  OWNER: Lance.  Logic: core/targets.py.

Accept changes the targets only (the mix or the paycheck percent). New paychecks steer toward them; nothing is
bought or sold here, and a rebalance still needs the customer's Confirm.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.routes.pages import _ctx, _need_fund, _session, templates
from core import intake, targets

router = APIRouter()

CARD = "partials/target_proposal.html"
ANSWER_CHOICES = {k: [v for v, _ in intake.BY_KEY[k].options] for k in ("risk_priority", "risk_range", "horizon")}


def card(acct) -> dict:
    """What the proposal card renders (a Jinja global, so pages that include the card need no route changes)."""
    return {"proposal": acct.proposal, "status": targets.status(acct) if acct.portfolio else None}


templates.env.globals["target_card"] = card


def _respond(request: Request, cid: str, say: str | None) -> HTMLResponse:
    """The card itself, plus the fund card and paycheck panel out of band (their targets may have changed) and a
    line in the chat."""
    s = _session(cid)
    msg = s.say(say) if say else None
    ctx = _ctx(request, cid, oob={"fund", "account"}, chat_note=msg)
    html = templates.get_template(CARD).render(ctx) + templates.get_template("partials/panels.html").render(ctx)
    return HTMLResponse(html)


@router.post("/p/{cid}/targets/accept", response_class=HTMLResponse)
def accept(request: Request, cid: str):
    s = _need_fund(cid)
    prop = s.fund.proposal
    if not prop:
        return _respond(request, cid, None)
    targets.accept(s.fund)
    tail = ("New paychecks invest the new amount." if prop["kind"] in ("income", "spending") else
            "New paychecks steer toward the new mix; nothing is sold unless you confirm a rebalance.")
    return _respond(request, cid, f"Done: {prop['title'][0].lower()}{prop['title'][1:]}. {tail} "
                                  f"It's listed under “How your fund was built”.")


@router.post("/p/{cid}/targets/dismiss", response_class=HTMLResponse)
def dismiss(request: Request, cid: str):
    s = _need_fund(cid)
    had = s.fund.proposal
    targets.dismiss(s.fund)
    return _respond(request, cid, "Okay, not now. Your fund stays exactly as it is." if had else None)


@router.post("/p/{cid}/targets/check", response_class=HTMLResponse)
def check(request: Request, cid: str):
    """Re-evaluate the targets now (they're also checked at every paycheck)."""
    s = _need_fund(cid)
    prop = targets.review(s.fund)
    return _respond(request, cid, None if prop else "I checked: nothing about your situation calls for new targets.")


@router.post("/p/{cid}/targets/answers")
def answers(cid: str, risk_priority: str = Form(...), risk_range: str = Form(...), max_drop: float = Form(...),
            horizon: str = Form(...)):
    """Changed risk answers (Account tab): a proposal on the Portfolio tab, built by the fund builder."""
    s = _need_fund(cid)
    q = intake.BY_KEY["max_drop"]
    picked = {"risk_priority": risk_priority, "risk_range": risk_range, "horizon": horizon}
    if any(v not in ANSWER_CHOICES[k] for k, v in picked.items()) or not q.min <= max_drop <= q.max:
        raise HTTPException(400, "Pick one of the listed answers")
    prop = targets.propose_answers(s.fund, {**picked, "max_drop": max_drop})
    if not prop:
        return RedirectResponse(f"/p/{cid}/account?saved=risk#risk", status_code=303)
    s.say(f"{prop['text']} Accept or say not now on your Portfolio tab; nothing changes until you do.")
    return RedirectResponse(f"/p/{cid}#target-proposal", status_code=303)
