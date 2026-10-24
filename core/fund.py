"""A customer's fund: build it from the intake, invest each paycheck, confirm trades.  OWNER: Lance.

Flow: the intake answers set the risk level, interests and paycheck percent. The starting amount
is invested on day one. Each paycheck, the chosen percent moves from checking to the fund
(through Nessie) and is spread across the funds furthest below target. When the customer's tier
allows it and they've turned it on, each review lines up trades they must confirm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from core import intake, market, nessie
from core.broker import get_broker, values
from core.models import Portfolio, Trade
from core.policy import tier_for
from core.portfolio import build_portfolio
from core.profile import get_profile
from core.rebalance import allocate_new_money


@dataclass
class FundAccount:
    customer_id: str
    today: date = field(default_factory=market.today)   # the demo clock for prices; paydays move it
    answers: dict = field(default_factory=dict)
    portfolio: Portfolio | None = None
    risk_score: int | None = None
    invest_pct: float = 0.0
    contributed: float = 0.0
    deposits: list[dict] = field(default_factory=list)   # newest first
    auto_trade: bool = False
    pending: list[dict] = field(default_factory=list)    # trades waiting for the customer's OK
    last_review: date | None = None
    log: list[str] = field(default_factory=list)          # plain-words history, newest first


def build(acct: FundAccount) -> Portfolio:
    """(Re)build the fund from the intake answers."""
    acct.risk_score = intake.risk_score(acct.answers)
    acct.portfolio = build_portfolio(intake.risk_level(acct.risk_score), intake.interests(acct.answers),
                                     intake.stocks(acct.answers))
    acct.invest_pct = float(acct.answers.get("paycheck_pct", acct.invest_pct))
    return acct.portfolio


def start(acct: FundAccount, on: date | None = None) -> list[Trade]:
    """Build the fund and invest the starting amount."""
    build(acct)
    amount = float(acct.answers.get("starting_amount", 0))
    if amount <= 0:
        return []
    trades = invest(acct, amount, on or acct.today)
    acct.contributed += amount
    acct.log.insert(0, f"Invested your starting {_money(amount)}.")
    return trades


def invest(acct: FundAccount, amount: float, on: date | None = None) -> list[Trade]:
    """Buy `amount` dollars, sending it to the funds furthest below target."""
    on = on or acct.today
    plan = allocate_new_money(acct.portfolio, values(acct.customer_id, on), amount)
    return [get_broker().submit(acct.customer_id, t, "buy", d, on) for t, d in plan.items() if d > 0]


def value(acct: FundAccount, on: date | None = None) -> float:
    return round(sum(values(acct.customer_id, on or acct.today).values()), 2)


def paycheck(acct: FundAccount) -> dict:
    """Run the clock to payday, deposit the paycheck, and invest the chosen percent right away."""
    cid = acct.customer_id
    days = max(1, get_profile(cid).days_to_payday)
    nessie.advance_days(cid, days)
    acct.today += timedelta(days=days)
    pay = nessie.deposit_paycheck(cid)
    amount = math.floor(pay * acct.invest_pct / 100)   # Nessie moves whole dollars
    trades, transfer_id = [], None
    if amount > 0 and acct.portfolio:
        transfer_id = nessie.transfer_to_investing(cid, amount)
        trades = invest(acct, amount)
        acct.contributed += amount
    entry = {"on": acct.today, "paycheck": pay, "pct": acct.invest_pct, "invested": float(amount),
             "transfer_id": transfer_id, "trades": trades}
    acct.deposits.insert(0, entry)
    acct.log.insert(0, f"Paycheck of {_money(pay)}: invested {_money(amount)} ({acct.invest_pct:g}%).")
    review_if_due(acct)
    return entry


def review_if_due(acct: FundAccount) -> dict | None:
    """If auto-prepared trades are on and a review is due, build the report and queue its trades."""
    tier = tier_for(value(acct))
    if not (acct.auto_trade and tier.auto_trades and acct.portfolio):
        return None
    if acct.last_review and (acct.today - acct.last_review).days < tier.review_every_days:
        return None
    from core.report import build_report   # report reads this module
    rep = build_report(acct)
    acct.last_review = acct.today
    acct.pending = [dict(t, why=r["title"]) for r in rep["recommendations"] for t in r["trades"]]
    if acct.pending:
        acct.log.insert(0, f"Your {tier.check_frequency.lower()} review lined up {len(acct.pending)} "
                           f"trade{'s' if len(acct.pending) != 1 else ''} for you to confirm.")
    return rep


def queue(acct: FundAccount, trades: list[dict], why: str) -> None:
    acct.pending = [dict(t, why=why) for t in trades]


def confirm_pending(acct: FundAccount) -> list[Trade]:
    """Place every queued trade: sells first, so their cash covers the buys."""
    ordered = sorted(acct.pending, key=lambda t: t["side"] != "sell")
    done = [get_broker().submit(acct.customer_id, t["symbol"], t["side"], t["notional"], acct.today) for t in ordered]
    if done:
        acct.log.insert(0, f"You confirmed {len(done)} trade{'s' if len(done) != 1 else ''}.")
    acct.pending = []
    return done


def set_interests(acct: FundAccount, themes: list[str]) -> Portfolio:
    """New interests -> new targets. Nothing is sold; new money and the next rebalance move toward them."""
    acct.answers["interests"] = {"themes": list(dict.fromkeys(themes)),
                                 "unmatched": acct.answers.get("interests", {}).get("unmatched", [])}
    return build(acct)


def set_stocks(acct: FundAccount, tickers: list[str]) -> Portfolio:
    """Single companies to hold. Like interests: targets change now, trades follow new money or a rebalance."""
    acct.answers["stocks"] = list(dict.fromkeys(t.upper() for t in tickers))
    return build(acct)


def set_invest_pct(acct: FundAccount, pct: float) -> None:
    acct.invest_pct = acct.answers["paycheck_pct"] = max(0.0, min(100.0, float(pct)))


def market_drop(acct: FundAccount, pct: float) -> None:
    market.add_shock(acct.today, pct)
    acct.log.insert(0, f"The market moved {pct:+g}%.")


def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"
