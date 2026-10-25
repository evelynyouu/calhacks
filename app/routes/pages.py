"""All page + HTMX routes.  OWNER: Ethan.

Full pages: / and, per persona, the intake chat at /p/{cid} until the fund is built; after that, tabs:
/p/{cid} (portfolio), /markets, /news, /chat, /report, /account.
HTMX endpoints return partials; extra panels refresh with hx-swap-oob, and app events
(a paycheck, a confirmed trade) also post a line into the chat. The ticker tape polls /tape.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent import claude_agent
from agent.tools import INDEXES, news_tickers
from app.routes.auth import auth_disabled, csrf_token
from app import state
from core import backtest, builder, explain, feed, fund, intake, market, nessie, slices
from core.broker import get_broker, values
from core.intake import QUESTIONS, AnswerError, parse
from core.market import ETFS
from core.models import to_dict
from core.policy import tier_for
from core.portfolio import THEME_LABELS
from core.profile import get_profile
from core.report import build_report

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["money"] = lambda v: f"${v:,.0f}"
templates.env.filters["money2"] = lambda v: f"${v:,.2f}"
templates.env.filters["pct"] = lambda v, d=0: f"{v * 100:.{d}f}%"
templates.env.filters["signed"] = lambda v, d=1: f"{round(v, d) + 0.0:+.{d}f}%"
templates.env.filters["day"] = lambda d: d.strftime("%b %-d, %Y")
templates.env.filters["ago"] = lambda d: _ago(d)
templates.env.globals["info"] = market.info
templates.env.globals["csrf_token"] = csrf_token

MARKET_DROP_PCT = -10.0
RAISE_PCT = 10.0
RANGES = {"1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "2Y": 730}
EXAMPLE_BUY = 25.0   # "$25 buys 0.07 shares" on a stock's page

# Personas that start with a fund already running, so the demo can jump straight to the report
DEMO_PRESETS = {
    "alex": {"answers": {"goal": "Retire early", "starting_amount": 52_000, "paycheck_pct": 20,
                         "risk_priority": "growth", "risk_range": "wide", "max_drop": 25, "horizon": "15plus",
                         "interests": {"themes": ["healthcare", "tech"], "unmatched": []}},
             "started_days_ago": 365, "auto_trade": True},
}


def _session(cid: str) -> state.Session:
    try:
        profile = get_profile(cid)
    except KeyError:
        raise HTTPException(404, "Unknown persona")
    s = state.get(cid)
    if not s.transcript:
        preset = DEMO_PRESETS.get(cid)
        if preset:
            s.fund.answers = dict(preset["answers"])
            fund.start(s.fund, on=s.fund.today - timedelta(days=preset["started_days_ago"]))
            s.fund.auto_trade = preset["auto_trade"]
            s.step = len(QUESTIONS)
            s.say(f"Welcome back, {profile.name}. Your fund has been running for a year. Open the report to see "
                  f"how it's doing, or press “Paycheck arrives” to watch the next paycheck invest itself.")
        else:
            s.say(claude_agent.greeting(profile.name))
            s.say(claude_agent.question_text(s))
    return s


def _ctx(request: Request, cid: str, **extra) -> dict:
    s = _session(cid)
    a = s.fund
    value = fund.value(a) if a.portfolio else 0.0
    return {
        "request": request, "cid": cid, "profile": get_profile(cid), "s": s, "a": a, "portfolio": a.portfolio,
        "value": value, "tier": tier_for(value), "questions": QUESTIONS,
        "pie_data": _pie(a.portfolio), "slices": slices.view(a.portfolio) if a.portfolio else [], "broker_mode": get_broker().mode,
        "activity": nessie.recent_activity(cid), "nessie_mode": nessie.mode(), "nessie_offline": nessie.offline_reason(),
        "etfs": ETFS, "theme_labels": THEME_LABELS, "live_market": market.source("SPY") == "live",
        "fast_forwarded": a.today > market.today(), "claude_on": claude_agent._get_client() is not None,
        "login_on": not auth_disabled(),
        "override": builder.override_offer(a.portfolio, a.answers) if a.portfolio else None, **extra,
    }


def _pie(p):
    """One wedge per slice (six at most), not one per holding: a custom fund has about 60 of those."""
    return slices.pie(p)


def _render(name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(ctx["request"], name, ctx)


def _panels(request: Request, cid: str, say: str | None = None, oob=("fund", "account", "tier", "today")) -> HTMLResponse:
    """Re-render the side panels (and post `say` into the chat) after an action."""
    s = _session(cid)
    msg = s.say(say) if say else None
    extra = _today(s.fund) if "today" in oob and s.fund.portfolio else {}
    return _render("partials/panels.html", _ctx(request, cid, oob=set(oob), chat_note=msg, **extra))


# ---------- Pages ----------

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"personas": nessie.list_customers()})


@router.get("/p/{cid}", response_class=HTMLResponse)
def dashboard(request: Request, cid: str):
    """The intake chat until the fund is built, then the Portfolio tab."""
    s = _session(cid)
    if not s.done:
        return _render("dashboard.html", _ctx(request, cid))
    return _render("portfolio.html", _ctx(request, cid, tab="portfolio", prompts=claude_agent.suggested_prompts(s)[:3],
                                          **_today(s.fund)))


def _today(a) -> dict:
    """Each holding's value and move today, and the fund's dollar move (from each price's day change)."""
    vals = values(a.customer_id, a.today)
    q = market.quotes(list(vals), a.today)
    rows = [{"ticker": t, "info": market.info(t), "value": v, "quote": q.get(t)}
            for t, v in sorted(vals.items(), key=lambda kv: -kv[1])]
    day = sum(r["value"] * r["quote"]["change_pct"] / (100 + r["quote"]["change_pct"]) for r in rows if r["quote"])
    before = sum(vals.values()) - day
    return {"rows": rows, "day_change": day, "day_pct": day / before * 100 if before else 0.0}


@router.get("/p/{cid}/how", response_class=HTMLResponse)
def how_built(request: Request, cid: str, full: bool = False):
    """How your fund was built: every number and reason comes from core/explain.py. ?full=1 opens the full version."""
    s = _need_fund(cid)
    return _render("how_built.html", _ctx(request, cid, tab="portfolio", how=explain.build(s.fund),
                                          intro=claude_agent.fund_intro(s), full=full))


@router.get("/p/{cid}/chat", response_class=HTMLResponse)
def chat_page(request: Request, cid: str, ask: str = ""):
    s = _session(cid)
    if not s.done:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    return _render("chat_page.html", _ctx(request, cid, tab="chat", prompts=claude_agent.suggested_prompts(s),
                                          ask=ask.strip()[:300]))


@router.get("/p/{cid}/report", response_class=HTMLResponse)
def report(request: Request, cid: str):
    ctx = _ctx(request, cid)
    if not ctx["portfolio"]:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    rep = build_report(ctx["a"])
    s = rep["summary"]
    ctx["rep"] = rep
    ctx["narrative"] = claude_agent.explain(
        rep["facts"], f"Write {ctx['profile'].name}'s fund report intro: 2 short paragraphs, plain words, no bullet points. "
                      "First: how the fund did over the period against its benchmark, what moved it, and what the customer's "
                      "own choices added or cost. Second: how the fund fits their money (you must say how much goes in each "
                      "paycheck, how many months of expenses the cash cushion covers, and any card debt) and then the one "
                      "recommendation most worth reading. Use only the facts below: add no new numbers, "
                      "never suggest buying or selling a specific stock, and say that prices are simulated if the facts say so.",
        fallback=rep["narrative"])
    ctx["history"] = rep["history"]
    ctx["fan_data"] = to_dict(rep["projection"])
    ctx["gain_up"] = s["gain"] >= 0
    ctx["tab"] = "report"
    return _render("report.html", ctx)


@router.get("/p/{cid}/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, cid: str):
    ctx = _ctx(request, cid)
    if not ctx["portfolio"]:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    a = ctx["a"]
    profile = ctx["profile"]
    tier = ctx["tier"]
    monthly_invest = a.invest_pct / 100 * profile.paycheck_amount * 2
    bt = backtest.run_backtest(
        portfolio=a.portfolio,
        starting_amount=max(ctx["value"], 1000.0),
        monthly_invest=monthly_invest,
        review_every_days=tier.review_every_days,
        today=a.today,
    )
    ctx["bt"] = bt
    ctx["tab"] = "backtest"
    ctx["price_note"] = "real closes where available, simulated past that" if market.source("SPY") == "live" else "simulated"
    return _render("backtest.html", ctx)


@router.post("/p/{cid}/reset")
def reset(cid: str):
    nessie.reset(cid)
    get_broker().reset(cid)
    market.clear_shocks()
    state.reset(cid)
    return RedirectResponse(f"/p/{cid}", status_code=303)


# ---------- Chat: intake answers, then free chat ----------

@router.post("/p/{cid}/answer", response_class=HTMLResponse)
def answer(request: Request, cid: str, value: str = Form(""), values: list[str] = Form(default=[]),
           other: str = Form("")):
    s = _session(cid)
    q = s.question
    if q is None:
        raise HTTPException(400, "The intake is already done")
    try:
        stored, shown = parse(q, value, values, other)
    except AnswerError as e:
        return _render("partials/chat_turn.html", _ctx(request, cid, new_messages=[{"role": "assistant", "text": str(e)}]))
    s.fund.answers[q.key] = stored
    new = [s.say(shown, "user")]
    s.step += 1
    oob = {"composer"}
    if s.done:
        fund.start(s.fund)
        new.append(s.say(claude_agent.fund_ready(s), link={"href": f"/p/{cid}/how", "label": "How your fund was built →"}))
        oob |= {"fund", "account", "tier", "nav"}
    else:
        new.append(s.say(claude_agent.question_text(s)))
        oob.add("fund")   # the progress line
    return _render("partials/chat_turn.html", _ctx(request, cid, new_messages=new, oob=oob))


@router.post("/p/{cid}/chat", response_class=HTMLResponse)
def chat(request: Request, cid: str, message: str = Form(""), echo: str = Form("")):
    s = _session(cid)
    text = message.strip()
    if not text or not s.done:
        raise HTTPException(400)
    # Typed messages appear as soon as they're sent (app.js), so only the reply goes back.
    # Suggested-prompt buttons send echo=1 to get their message back too.
    sent = s.say(text, "user")
    reply, events = claude_agent.chat_turn(s, text)
    new = ([sent] if echo else []) + [s.say(reply)]
    return _render("partials/chat_turn.html", _ctx(request, cid, new_messages=new, oob=events))


# ---------- Paycheck autopilot and demo events ----------

def _need_fund(cid: str) -> state.Session:
    s = _session(cid)
    if not s.fund.portfolio:
        raise HTTPException(400, "Build a fund first")
    return s


@router.post("/p/{cid}/paycheck", response_class=HTMLResponse)
def paycheck(request: Request, cid: str):
    s = _need_fund(cid)
    pending_before = len(s.fund.pending)
    d = fund.paycheck(s.fund)
    buys = ", ".join(f"{t.symbol} ${t.notional:,.2f}" for t in d["trades"])
    say = (f"Paycheck landed: ${d['paycheck']:,.0f}. Invested ${d['invested']:,.0f} ({d['pct']:g}%) right away"
           + (f": {buys}." if buys else "."))
    if len(s.fund.pending) and len(s.fund.pending) != pending_before:
        say += f" Your scheduled review also lined up {len(s.fund.pending)} trades; they're waiting for your OK."
    return _panels(request, cid, say)


@router.post("/p/{cid}/market_drop", response_class=HTMLResponse)
def market_drop(request: Request, cid: str):
    s = _need_fund(cid)
    before = fund.value(s.fund)
    fund.market_drop(s.fund, MARKET_DROP_PCT)
    after = fund.value(s.fund)
    return _panels(request, cid, f"The market just fell {abs(MARKET_DROP_PCT):g}%. Your fund went from "
                                 f"${before:,.2f} to ${after:,.2f}. Your next report will say what, if anything, to do.")


@router.post("/p/{cid}/raise", response_class=HTMLResponse)
def got_raise(request: Request, cid: str):
    _need_fund(cid)
    nessie.apply_raise(cid, RAISE_PCT)
    return _panels(request, cid, f"Nice, a {RAISE_PCT:g}% raise! Your paychecks are now "
                                 f"${get_profile(cid).paycheck_amount:,.0f}, so the same percent invests more.")


@router.post("/p/{cid}/pct", response_class=HTMLResponse)
def set_pct(request: Request, cid: str, pct: float = Form(...)):
    s = _need_fund(cid)
    fund.set_invest_pct(s.fund, pct)
    return _panels(request, cid, oob=("account",))


@router.post("/p/{cid}/auto", response_class=HTMLResponse)
def set_auto(request: Request, cid: str, on: str = Form("")):
    s = _need_fund(cid)
    if not tier_for(fund.value(s.fund)).auto_trades:
        raise HTTPException(400, "Automatic trade prep unlocks at a higher balance")
    s.fund.auto_trade = on == "1"
    return _panels(request, cid, oob=("account",))


@router.post("/p/{cid}/trades/{what}", response_class=HTMLResponse)
def trades(request: Request, cid: str, what: str):
    s = _need_fund(cid)
    if what == "confirm":
        done = fund.confirm_pending(s.fund)
        say = "Done: " + ", ".join(f"{t.side} {t.symbol} ${t.notional:,.2f}" for t in done) + "." if done else None
    elif what == "dismiss":
        s.fund.pending = []
        say = "Okay, I dropped those trades. Nothing was bought or sold."
    else:
        raise HTTPException(400)
    return _panels(request, cid, say)


@router.post("/p/{cid}/rec")
def apply_recommendation(cid: str, action: str = Form(...), value: str = Form("")):
    """Buttons on the report's recommendations. Trades go to the confirm panel; nothing trades here."""
    s = _need_fund(cid)
    a = s.fund
    if action == "queue_trades":
        rec = next((r for r in build_report(a)["recommendations"] if r["trades"]), None)
        if rec:
            fund.queue(a, rec["trades"], rec["title"])
            s.say(f"I lined up {len(a.pending)} trades from your report. Review them in the trades panel and "
                  "press Confirm when you're ready.")
    elif action == "set_pct":
        fund.set_invest_pct(a, float(value))
        s.say(f"Your paycheck share is now {a.invest_pct:g}%.")
    elif action in ("add_interest", "remove_interest"):
        if value not in THEME_LABELS:
            raise HTTPException(400)
        themes = [t for t in a.answers.get("interests", {}).get("themes", []) if t != value]
        fund.set_interests(a, themes + [value] if action == "add_interest" else themes)
        verb = "Added" if action == "add_interest" else "Removed"
        s.say(f"{verb} {THEME_LABELS[value]}. Your targets changed; new paychecks steer toward them, and the next "
              "report can line up a rebalance.")
    else:
        raise HTTPException(400)
    return RedirectResponse(f"/p/{cid}" if action == "queue_trades" else f"/p/{cid}/report", status_code=303)


# ---------- Markets (watchlist and stock detail) ----------

@router.get("/p/{cid}/markets", response_class=HTMLResponse)
def markets(request: Request, cid: str, s: str = "", r: str = "3M"):
    sess = _session(cid)
    a = sess.fund
    held = [h.ticker for h in a.portfolio.holdings] if a.portfolio else []
    groups = [("Indexes", INDEXES), ("Your fund", held), ("Watchlist", sess.watchlist)]
    q = market.quotes([t for _, ts in groups for t in ts], a.today)
    sel = (s or (held[0] if held else "SPY")).upper()
    if not market.known(sel):
        raise HTTPException(404, f"No prices for {sel}")
    r = r if r in RANGES else "3M"
    end = a.today
    pts = market.series(sel, end - timedelta(days=RANGES[r]), end)
    year = [p for _, p in market.series(sel, end - timedelta(days=365), end)]
    quote = q.get(sel) or market.quote(sel, end)
    shares = get_broker().positions(cid).get(sel, 0.0)
    cost = get_broker().cost_basis(cid).get(sel, 0.0)
    detail = {
        "symbol": sel, "info": market.info(sel), "quote": quote, "range": r, "source": market.source(sel),
        "chart": {"labels": [d.isoformat() for d, _ in pts], "values": [round(p, 2) for _, p in pts]},
        "range_pct": (pts[-1][1] / pts[0][1] - 1) * 100 if len(pts) > 1 else 0.0,
        "high": max(year), "low": min(year),
        "shares": shares, "value": shares * market.raw_price(sel, end), "cost": cost,
        "example_shares": EXAMPLE_BUY / quote["price"],
        "in_fund": sel in held, "is_stock": sel in intake.stocks(a.answers),
        "can_add_stock": a.portfolio is not None and sel not in ETFS,
        "theme": ETFS[sel]["theme"] if sel in ETFS and ETFS[sel]["role"] == "tilt" else "",
        "watching": sel in sess.watchlist, "news": feed.news([sel], 6),
    }
    return _render("markets.html", _ctx(request, cid, tab="markets", groups=groups, quotes=q, d=detail,
                                        ranges=list(RANGES)))


@router.get("/p/{cid}/search", response_class=HTMLResponse)
def search(request: Request, cid: str, q: str = ""):
    text = q.strip()
    results = feed.search(text) if len(text) >= 1 else []
    if text and not results:   # offline: search the tickers we know
        t = text.lower()
        results = [{"symbol": k, "name": market.name(k), "asset": "etf" if k in ETFS else "stocks"}
                   for k in list(ETFS) + list(market.COMPANIES) if t in k.lower() or t in market.name(k).lower()][:8]
    return _render("partials/search_results.html", _ctx(request, cid, results=results[:8], q=text))


@router.post("/p/{cid}/watch")
def watch(cid: str, symbol: str = Form(...), op: str = Form("add")):
    s = _session(cid)
    symbol = symbol.upper().strip()
    if op == "add" and symbol not in s.watchlist and market.known(symbol):
        s.watchlist.append(symbol)
    elif op == "remove" and symbol in s.watchlist:
        s.watchlist.remove(symbol)
    return RedirectResponse(f"/p/{cid}/markets?s={symbol}", status_code=303)


@router.post("/p/{cid}/stocks")
def stocks(cid: str, symbol: str = Form(...), op: str = Form("add")):
    """Add or remove a single company. Targets change now; money follows on paydays or a rebalance."""
    s = _need_fund(cid)
    symbol = symbol.upper().strip()
    current = intake.stocks(s.fund.answers)
    if op == "add":
        if symbol in ETFS or not market.known(symbol):
            raise HTTPException(400, "Can't add that one")
        fund.set_stocks(s.fund, current + [symbol])
        h = next((h for h in s.fund.portfolio.holdings if h.ticker == symbol), None)
        s.say(f"Added {market.name(symbol)} ({symbol}) to your fund at {h.weight:.0%}. Your next paychecks buy it, "
              "including partial shares." if h else f"Your fund is full of single companies, so {symbol} wasn't added. "
              "Remove one to make room.")
    else:
        fund.set_stocks(s.fund, [t for t in current if t != symbol])
        s.say(f"Removed {symbol} from your targets. Shares you own stay until you confirm a rebalance.")
    return RedirectResponse(f"/p/{cid}/markets?s={symbol}", status_code=303)


# ---------- News and the AI rundown ----------

@router.get("/p/{cid}/news", response_class=HTMLResponse)
def news(request: Request, cid: str):
    s = _session(cid)
    if not s.done:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    which = "market" if request.query_params.get("feed") == "market" else "fund"
    items = feed.news(news_tickers(s) if which == "fund" else None, 30)
    return _render("news.html", _ctx(request, cid, tab="news", items=items, which=which,
                                     prompts=claude_agent.suggested_prompts(s)))


@router.get("/p/{cid}/rundown", response_class=HTMLResponse)
def rundown(request: Request, cid: str, refresh: int = 0):
    s = _need_fund(cid)
    if refresh or not s.rundown:
        s.rundown = {"text": claude_agent.rundown(s), "at": datetime.now()}
    return _render("partials/rundown.html", _ctx(request, cid))


# ---------- Account, settings and security ----------

@router.get("/p/{cid}/account", response_class=HTMLResponse)
def account(request: Request, cid: str, saved: str = ""):
    s = _session(cid)
    if not s.done:
        return RedirectResponse(f"/p/{cid}", status_code=303)
    hook = (Path(__file__).resolve().parents[2] / "scripts" / "git-hooks" / "pre-commit").exists()
    ids = nessie.load_ids().get(cid, {}) if nessie.mode() == nessie.LIVE else {}
    options = sorted(THEME_LABELS.items(), key=lambda kv: kv[1])
    return _render("account.html", _ctx(request, cid, tab="account", saved=saved, has_hook=hook,
                                        accounts=ids.get("accounts", {}), interest_options=options,
                                        picked=set(intake.interests(s.fund.answers))))


@router.post("/p/{cid}/settings")
async def settings(request: Request, cid: str):
    s = _session(cid)
    form = await request.form()
    for key in ("weekly_report", "trade_confirmations", "big_moves"):
        s.prefs[key] = form.get(key) == "on"
    try:
        s.prefs["big_move_pct"] = max(1, min(20, int(form.get("big_move_pct", s.prefs["big_move_pct"]))))
    except ValueError:
        pass
    return RedirectResponse(f"/p/{cid}/account?saved=notifications#notifications", status_code=303)


@router.post("/p/{cid}/override", response_class=HTMLResponse)
def override(request: Request, cid: str, use: str = Form(...)):
    """The customer picks the mix their answers pointed to, or goes back to our suggestion."""
    if use not in ("answers", "suggested"):
        raise HTTPException(400)
    s = _need_fund(cid)
    p = fund.set_override(s.fund, use == "answers")
    say = (f"Done: your fund is now {p.risk_label}, the mix your answers pointed to. New paychecks steer toward it; "
           f"nothing is sold unless you confirm a rebalance." if use == "answers" else
           f"Done: back to our suggestion, {p.risk_label}. New paychecks steer toward it.")
    return _panels(request, cid, say=say)


@router.post("/p/{cid}/interests")
def interests(cid: str, values: list[str] = Form(default=[])):
    s = _need_fund(cid)
    fund.set_interests(s.fund, [v for v in values if v in THEME_LABELS])
    s.say("Updated your industries. New paychecks steer toward the new targets.")
    return RedirectResponse(f"/p/{cid}/account?saved=investing#investing", status_code=303)


# ---------- Ticker tape ----------

TAPE_EXTRAS = ["BND", "GLD"]
TAPE_HOLDINGS = 8   # a custom index holds ~60 companies; the strip shows the biggest positions
TAPE_DEFAULT = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]   # before a persona is picked


@router.get("/tape", response_class=HTMLResponse)
def tape_any(request: Request, cid: str = "", part: str = ""):
    """The strip pinned to the top of every page. part=groups returns only the scrolling cards, so the
    60s refresh swaps values inside the running animation instead of restarting it."""
    s = state.get(cid) if cid and cid in {c["id"] for c in nessie.list_customers()} else None
    a = s.fund if s else None
    on = a.today if a else market.today()
    held = [h.ticker for h in sorted(a.portfolio.holdings, key=lambda h: -h.weight)][:TAPE_HOLDINGS] \
        if a and a.portfolio else []
    symbols = list(dict.fromkeys(INDEXES + held + (s.watchlist if s else TAPE_DEFAULT) + TAPE_EXTRAS))
    q = market.quotes(symbols, on)
    items = [{**q[t], "label": _tape_label(t), "spark": _spark(t, on, q[t])} for t in symbols if t in q]
    ctx = {"request": request, "cid": cid if s else "", "tape": items, "part": part,
           "live_market": market.source("SPY") == "live", "demo_date": on if on > market.today() else None}
    return templates.TemplateResponse(request, "partials/tape.html", ctx)


@router.get("/p/{cid}/tape", response_class=HTMLResponse)
def tape(request: Request, cid: str, part: str = ""):
    _session(cid)   # 404 for an unknown persona
    return tape_any(request, cid, part)


SPARK_W, SPARK_H, SPARK_DAYS = 60, 24, 30


def _tape_label(t: str) -> str:
    return ETFS[t]["label"] if t in ETFS and ETFS[t]["role"] == "benchmark" else t


def _spark(t: str, end, quote: dict) -> dict:
    """A month of closes as SVG polyline points. Like Yahoo's, it's colored by today's move, and the
    dashed line marks the previous close (kept inside the box)."""
    pts = [p for _, p in market.series(t, end - timedelta(days=SPARK_DAYS), end)]
    up = quote["change"] >= 0
    if len(pts) < 2:
        return {"points": "", "base": SPARK_H / 2, "up": up}
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    y = lambda v: round(min(SPARK_H - 1, max(1, SPARK_H - 2 - (v - lo) / span * (SPARK_H - 4))), 1)
    step = SPARK_W / (len(pts) - 1)
    return {"points": " ".join(f"{i * step:.1f},{y(v)}" for i, v in enumerate(pts)),
            "base": y(quote.get("prev_close") or pts[0]), "up": up}


def _ago(d) -> str:
    if not d:
        return ""
    secs = (datetime.now(d.tzinfo) - d).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return d.strftime("%b %-d")
