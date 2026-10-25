"""Auto-trade backtest: replay paychecks + rebalancing on historical/simulated prices.  OWNER: Saahil.

Runs four parallel strategies on the same start date, starting amount, and deposits so the only
variable is what happens between paychecks:

  auto   – invest each paycheck into the most underweight fund, then rebalance drift every
           review_every_days (the tier's normal schedule).
  hold   – same paycheck deposits, directed to the most underweight fund, but no rebalancing.
  bench  – same deposits into VTI / VXUS / BND at this fund's own stock/bond split, no rebalancing.
  spy    – same deposits, 100% into SPY.

All four start with the same dollar amount and receive identical paycheck deposits on the same days,
so the return difference isolates the effect of rebalancing.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import market
from core.broker import SimBroker
from core.models import Holding, Portfolio
from core.rebalance import NEW_MONEY_PICKS, allocate_new_money, rebalance_trades

PAY_PERIOD_DAYS = 14    # bi-weekly paychecks
RECORD_EVERY_DAYS = 7   # weekly chart points


def run_backtest(
    portfolio: Portfolio,
    starting_amount: float,
    monthly_invest: float,
    review_every_days: int,
    today: date,
    days: int = 91,
) -> dict:
    """Replay `days` of history ending on `today`.

    Args:
        portfolio:          the customer's current fund
        starting_amount:    dollars invested on the first day
        monthly_invest:     dollars per month going in via paychecks
        review_every_days:  how often the auto strategy rebalances (from tier)
        today:              the last day of the backtest (demo clock)
        days:               how many days to look back (default 91 = ~3 months)

    Returns a dict with:
        labels, auto, hold, benchmark, spy, contributed  (parallel lists, one entry per week)
        trades      (list of rebalancing trades the auto strategy placed)
        summary     (return pcts, alpha, trade count, dates)
    """
    if not portfolio.holdings or starting_amount <= 0:
        return _empty()

    start = today - timedelta(days=days)
    bench_port = _benchmark_portfolio(portfolio)

    # Isolated broker per strategy – unique CIDs so they never share state.
    auto_br, auto_cid = SimBroker(), "__bt_auto__"
    hold_br, hold_cid = SimBroker(), "__bt_hold__"
    bench_br, bench_cid = SimBroker(), "__bt_bench__"

    _buy_portfolio(auto_br,  auto_cid,  portfolio,  starting_amount, start)
    _buy_portfolio(hold_br,  hold_cid,  portfolio,  starting_amount, start)
    _buy_portfolio(bench_br, bench_cid, bench_port, starting_amount, start)
    spy_shares = starting_amount / market.raw_price("SPY", start)

    labels:      list[str]   = [start.isoformat()]
    auto_vals:   list[float] = [round(starting_amount, 2)]
    hold_vals:   list[float] = [round(starting_amount, 2)]
    bench_vals:  list[float] = [round(starting_amount, 2)]
    spy_vals:    list[float] = [round(starting_amount, 2)]
    contrib_vals:list[float] = [round(starting_amount, 2)]
    trades_log:  list[dict]  = []

    total_contrib = starting_amount
    last_pay    = start
    last_review = start
    last_record = start

    current = start + timedelta(days=1)
    while current <= today:
        # --- bi-weekly paycheck ---
        if (current - last_pay).days >= PAY_PERIOD_DAYS:
            last_pay   = current
            deposit    = monthly_invest / 2   # bi-weekly
            total_contrib += deposit
            spy_shares += deposit / market.raw_price("SPY", current)

            for br, cid, port in [
                (auto_br,  auto_cid,  portfolio),
                (hold_br,  hold_cid,  portfolio),
                (bench_br, bench_cid, bench_port),
            ]:
                vals = _vals(br, cid, current)
                plan = allocate_new_money(port, vals, deposit, NEW_MONEY_PICKS)   # as a real paycheck buys
                for t, d in plan.items():
                    if d >= 1.0:
                        br.submit(cid, t, "buy", d, current)

        # --- periodic rebalance (auto strategy only) ---
        if (current - last_review).days >= review_every_days:
            last_review = current
            vals = _vals(auto_br, auto_cid, current)
            rb   = rebalance_trades(portfolio, vals)
            # sells first so proceeds cover buys
            for t, delta in sorted(rb.items(), key=lambda x: x[1]):
                if abs(delta) >= 1.0:
                    side = "buy" if delta > 0 else "sell"
                    auto_br.submit(auto_cid, t, side, abs(delta), current)
                    trades_log.append({
                        "on": current.isoformat(),
                        "symbol": t,
                        "side": side,
                        "notional": round(abs(delta), 2),
                    })

        # --- weekly snapshot ---
        if (current - last_record).days >= RECORD_EVERY_DAYS or current == today:
            last_record = current
            labels.append(current.isoformat())
            auto_vals.append( round(sum(_vals(auto_br,  auto_cid,  current).values()), 2))
            hold_vals.append( round(sum(_vals(hold_br,  hold_cid,  current).values()), 2))
            bench_vals.append(round(sum(_vals(bench_br, bench_cid, current).values()), 2))
            spy_vals.append(  round(spy_shares * market.raw_price("SPY", current), 2))
            contrib_vals.append(round(total_contrib, 2))

        current += timedelta(days=1)

    def _ret(final: float) -> float:
        return round((final / total_contrib - 1) * 100, 2) if total_contrib else 0.0

    auto_end  = auto_vals[-1]
    hold_end  = hold_vals[-1]
    bench_end = bench_vals[-1]
    spy_end   = spy_vals[-1]

    return {
        "labels":     labels,
        "auto":       auto_vals,
        "hold":       hold_vals,
        "benchmark":  bench_vals,
        "spy":        spy_vals,
        "contributed":contrib_vals,
        "trades":     trades_log,
        "summary": {
            "auto_return_pct":     _ret(auto_end),
            "hold_return_pct":     _ret(hold_end),
            "bench_return_pct":    _ret(bench_end),
            "spy_return_pct":      _ret(spy_end),
            "alpha_vs_hold_pct":   round(_ret(auto_end) - _ret(hold_end),  2),
            "alpha_vs_bench_pct":  round(_ret(auto_end) - _ret(bench_end), 2),
            "auto_end":            auto_end,
            "hold_end":            hold_end,
            "bench_end":           bench_end,
            "spy_end":             spy_end,
            "total_rebalance_trades": len(trades_log),
            "total_contributed":   round(total_contrib, 2),
            "start":               (today - timedelta(days=days)).isoformat(),
            "end":                 today.isoformat(),
            "days":                days,
            "review_every_days":   review_every_days,
        },
    }


# ---------- helpers ----------

def _vals(broker: SimBroker, cid: str, on: date) -> dict[str, float]:
    return {s: sh * market.raw_price(s, on) for s, sh in broker.positions(cid).items()}


def _buy_portfolio(broker: SimBroker, cid: str, portfolio: Portfolio,
                   amount: float, on: date) -> None:
    """Buy the initial allocation proportional to target weights."""
    for h in portfolio.holdings:
        dollars = round(amount * h.weight, 2)
        if dollars >= 1.0:
            broker.submit(cid, h.ticker, "buy", dollars, on)


def _benchmark_portfolio(portfolio: Portfolio) -> Portfolio:
    """VTI / VXUS / BND at the same stock/bond split as `portfolio`."""
    bond = sum(h.weight for h in portfolio.holdings if h.ticker == "BND")
    stock = 1.0 - bond
    us   = round(stock * 0.70, 4)
    intl = round(stock * 0.30, 4)
    bond = round(1.0 - us - intl, 4)   # absorb rounding
    return Portfolio(
        risk_level=portfolio.risk_level,
        risk_label=portfolio.risk_label,
        holdings=[
            Holding("VTI",  "US Stocks",    us,   "core", 0.03),
            Holding("VXUS", "Intl Stocks",  intl, "core", 0.05),
            Holding("BND",  "Bonds",        bond, "core", 0.03),
        ],
        annual_fee_pct=round(us * 0.03 + intl * 0.05 + bond * 0.03, 3),
        worst_year=2022, worst_year_return=-15.0,
    )


def _empty() -> dict:
    return {
        "labels": [], "auto": [], "hold": [], "benchmark": [], "spy": [],
        "contributed": [], "trades": [],
        "summary": {"error": "No portfolio or zero starting amount."},
    }
