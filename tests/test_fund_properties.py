"""Properties of the fund builder over every risk answer x horizon x 0/1/3/6 interests x 0/2 companies,
and golden funds for the four personas.  OWNER: Evelyn."""
import itertools

import pytest

from core import builder, intake
from core.portfolio import (BOND_MAX, BOND_MIN, MIN_TILT, PER_INTEREST_CAP, STOCK_CAP_BY_RISK, STOCK_EACH, THEMES,
                            TILT_CAP_BY_RISK)
from core import universe
from core.portfolio import COMPANY_CAP, INTL_SHARE, SPEND_CAP_BY_RISK
from core.spending import Spend
from tests.fund_helpers import PERSONA_ANSWERS

PRIORITY = ["protect", "steady", "growth", "max"]
RANGE = ["narrow", "medium", "wide", "widest"]
DROPS = list(range(0, 55, 5))
HORIZONS = [v for v, _ in intake.BY_KEY["horizon"].options]
INTERESTS = [[], ["clean_energy"], ["tech", "healthcare", "biotech"],
             ["tech", "ai_chips", "software", "staples", "gold", "gaming"]]
STOCKS = [[], ["NVDA", "AAPL"]]


def answers(p, r, d, h, themes, stocks, **kw):
    return {"risk_priority": p, "risk_range": r, "max_drop": d, "horizon": h,
            "interests": {"themes": themes, "unmatched": []}, "stocks": stocks, **kw}


def grid():
    for d, h, themes, stocks in itertools.product(DROPS, HORIZONS, INTERESTS, STOCKS):
        funds = {(p, r): builder.build(answers(p, r, d, h, themes, stocks)) for p, r in itertools.product(PRIORITY, RANGE)}
        yield (d, h, themes, stocks), funds


def bonds(f):
    return next(x.weight for x in f.holdings if x.ticker == "BND")


@pytest.fixture(scope="module")
def all_funds():
    return list(grid())


def test_weights_add_up_with_no_negatives_and_bonds_in_range(all_funds):
    for key, funds in all_funds:
        for pr, f in funds.items():
            assert sum(x.weight for x in f.holdings) == pytest.approx(1.0, abs=1e-4), (key, pr)
            assert all(x.weight >= 0 for x in f.holdings), (key, pr)
            assert BOND_MIN - 1e-9 <= bonds(f) <= BOND_MAX + 1e-9, (key, pr)


def test_a_lower_score_never_means_fewer_bonds(all_funds):
    for key, funds in all_funds:
        for (p, r), f in funds.items():
            for (p2, r2), g in funds.items():
                if PRIORITY.index(p2) <= PRIORITY.index(p) and RANGE.index(r2) <= RANGE.index(r):
                    assert bonds(g) >= bonds(f) - 1e-9, (key, (p, r), (p2, r2))


def test_worst_year_stays_within_max_drop_unless_no_mix_can(all_funds):
    for (d, h, themes, stocks), funds in all_funds:
        for pr, f in funds.items():
            if f.worst_year_return < -d:   # only allowed as the steadiest mix, and it says so
                assert bonds(f) == pytest.approx(BOND_MAX) and f.score == 0, (d, h, themes, stocks, pr)
                note = next(x for x in f.reasons if x.kind == "drop")
                assert note.facts["warn"] and note.text in f.overlap_warnings


def test_overriding_can_go_past_the_limits():
    f = builder.build(answers("max", "widest", 15, "lt3", [], [], override=True))
    assert f.risk_level == 4 and f.worst_year_return < -15
    assert any(x.kind == "override" for x in f.reasons)


def test_under_3_years_never_goes_above_conservative(all_funds):
    for (d, h, themes, stocks), funds in all_funds:
        if h == "lt3":
            assert all(f.risk_level == 1 for f in funds.values()), (d, themes, stocks)


def test_interests_and_companies_stay_within_their_caps(all_funds):
    # interests and added companies are measured by their exact parts (a company can also be in the US index)
    for key, funds in all_funds:
        for pr, f in funds.items():
            interest, added = f.parts.get("interest", {}), f.parts.get("added", {})
            assert sum(interest.values()) <= TILT_CAP_BY_RISK[f.risk_level] + 1e-4, (key, pr)
            assert all(w <= PER_INTEREST_CAP + 1e-4 for w in interest.values()), (key, pr)
            assert all(w <= STOCK_EACH + 1e-4 for w in added.values()), (key, pr)
            assert sum(added.values()) <= STOCK_CAP_BY_RISK[f.risk_level] + 1e-4, (key, pr)


def test_every_holding_and_every_drop_or_shrink_has_a_reason(all_funds):
    for (d, h, themes, stocks), funds in all_funds:
        wanted_themes = {THEMES[t] for t in themes}
        for pr, f in funds.items():
            explained = {(x.kind, x.ticker) for x in f.reasons}
            for x in f.holdings:
                assert any(t == x.ticker for _, t in explained), (x.ticker, pr)
            sized = {r.ticker for r in f.reasons if r.kind in ("swing", "cap")}
            kept_funds = {r.ticker for r in f.reasons if r.kind == "dropped"}
            for fund in wanted_themes:                     # each interest: kept at full size, or a reason why not
                assert fund in kept_funds or fund in sized or _theme_share(f, fund) >= PER_INTEREST_CAP - 1e-3, (fund, pr)
            for t in set(stocks) - set(f.parts.get("added", {})):
                assert ("dropped", t) in explained, (t, pr)


def _theme_share(f, fund):
    from core.market import COMPANIES, ETFS
    theme = ETFS[fund]["theme"]
    def theme_of(t):
        return ETFS[t]["theme"] if t in ETFS else ETFS[COMPANIES[t]["etf"]]["theme"]
    return sum(w for t, w in f.parts.get("interest", {}).items() if theme_of(t) == theme)


# ---------- the custom index, with spending ----------

SPENDS = {
    "none": [],
    "one brand": [Spend("Target", "Shopping", 400.0, "TGT", "brand")],
    "eight brands, one a core company": [Spend(f"Shop {t}", "Shopping", d, t, "brand") for t, d in
                                         [("COST", 900), ("TGT", 800), ("SBUX", 700), ("MCD", 600), ("UBER", 500),
                                          ("NKE", 400), ("DIS", 300), ("EBAY", 200)]],
    "private shops only": [Spend("Trader Joe's", "Groceries", 500.0, None, "category"),
                           Spend("Corner Cafe", "Restaurants", 120.0, None, "category")],
}


@pytest.fixture(scope="module")
def spending_funds():
    out = []
    for d, h, themes, stocks, (name, spend) in itertools.product((0, 20, 50), ("lt3", "15plus", "unsure"), INTERESTS,
                                                                   STOCKS, SPENDS.items()):
        for p, r in itertools.product(PRIORITY, RANGE):
            out.append(((d, h, tuple(themes), tuple(stocks), name, p, r), spend,
                        builder.build(answers(p, r, d, h, themes, stocks), spending=spend)))
    return out


def test_custom_index_properties_hold_with_any_spending(spending_funds):
    from core.market import COMPANIES
    from core.spending import company_spend
    for key, spend, f in spending_funds:
        w = {x.ticker: x.weight for x in f.holdings}
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4) and min(w.values()) > 0, key
        assert len(w) == len(f.holdings), key                                            # one position per company
        assert max(v for t, v in w.items() if t in COMPANIES) < COMPANY_CAP, key
        slices = f.slice_weights()
        assert slices["spend"] <= SPEND_CAP_BY_RISK[f.risk_level] + 1e-4, key
        assert set(f.parts.get("spend", {})) <= set(company_spend(spend)), key          # every pick traces to a purchase
        assert slices["intl"] == pytest.approx((1 - slices["bonds"]) * INTL_SHARE, abs=1e-3), key
        assert {x.sector for x in f.by_slice()["us_core"]} | {
            universe.sector(t) for t in f.parts.get("us_core", {})} == set(universe.SECTORS), key
        if spend:
            assert slices["spend"] > 0, key
        else:
            assert any(r.kind == "spend" and r.facts.get("empty") for r in f.reasons), key


def test_pr5_limits_still_hold_with_spending(spending_funds):
    for (d, h, *_), spend, f in spending_funds:
        if h == "lt3" and not f.reasons[-1:] == []:
            assert f.risk_level == 1, (d, h)
        if f.worst_year_return < -d:
            assert f.score == 0 and f.parts["bonds"]["BND"] == pytest.approx(BOND_MAX), (d, h)


# ---------- golden funds for the four personas (with their spending from the bank) ----------

def _golden(cid):
    from core.spending import spending_profile
    f = builder.build(PERSONA_ANSWERS[cid], spending=spending_profile(cid))
    return (f.risk_label, f.score, f.slice_weights(), sorted(f.parts.get("spend", {})),
            sorted(f.parts.get("interest", {})), sorted({r.kind for r in f.reasons}))


# (label, score, slice weights, spend picks, interest picks, reason kinds). Reviewed 2026-10-24:
#   Maria shops at Walmart, Starbucks, Uber; Walmart is also an "everyday essentials" pick, so it's capped.
#   Jordan's Corner Cafe is local, so it counts toward the biggest restaurant companies (MCD, SBUX, YUM).
#   Sam's Trader Joe's is private: its grocery spending goes to WMT, COST, KR.
#   Alex is at 67 with 30% bonds, as before: the max-drop check counts the US index as the market (VTI's real
#   returns), since it follows the market by design.
GOLDEN = {
    "maria": ("Conservative", 0, {"bonds": 0.65, "intl": 0.105, "us_core": 0.1151, "spend": 0.05, "interest": 0.0795,
                                  "added": 0}, ["SBUX", "UBER", "WMT"], ["COST", "WMT"],
              ["bonds", "cap", "combined", "core", "drop", "horizon", "score", "spend", "tilt"]),
    "jordan": ("Moderate", 33, {"bonds": 0.45, "intl": 0.165, "us_core": 0.1684, "spend": 0.0798, "interest": 0.1373,
                                "added": 0}, ["MCD", "SBUX", "TGT", "YUM"], ["GOOGL", "META", "NTDOY", "RBLX"],
               ["bonds", "combined", "core", "drop", "horizon", "score", "sector", "spend", "swing", "tilt"]),
    "sam": ("Growth", 50, {"bonds": 0.4, "intl": 0.18, "us_core": 0.1666, "spend": 0.0998, "interest": 0.1539,
                           "added": 0}, ["COST", "KR", "NFLX", "TGT", "WMT"], ["AAPL", "JNJ", "LLY", "MSFT"],
            ["bonds", "combined", "core", "drop", "horizon", "score", "spend", "swing", "tilt"]),
    "alex": ("Growth", 67, {"bonds": 0.3, "intl": 0.21, "us_core": 0.2368, "spend": 0.1, "interest": 0.1539,
                            "added": 0}, ["AMZN", "COST", "TSLA"], ["AAPL", "JNJ", "LLY", "MSFT"],
             ["bonds", "combined", "core", "drop", "horizon", "score", "spend", "swing", "tilt"]),
}


@pytest.mark.parametrize("cid", ["maria", "jordan", "sam", "alex"])
def test_golden_persona_funds(cid):
    assert _golden(cid) == GOLDEN[cid]
