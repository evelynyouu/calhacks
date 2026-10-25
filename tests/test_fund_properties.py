"""Properties of the fund builder over every risk answer x horizon x 0/1/3/6 interests x 0/2 companies,
and golden funds for the four personas.  OWNER: Evelyn."""
import itertools

import pytest

from core import builder, intake
from core.portfolio import (BOND_MAX, BOND_MIN, MIN_TILT, PER_INTEREST_CAP, STOCK_CAP_BY_RISK, STOCK_EACH, THEMES,
                            TILT_CAP_BY_RISK)
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
    for key, funds in all_funds:
        for pr, f in funds.items():
            tilts = [x for x in f.holdings if x.role == "tilt"]
            picks = [x for x in f.holdings if x.role == "stock"]
            assert sum(x.weight for x in tilts) <= TILT_CAP_BY_RISK[f.risk_level] + 1e-4, (key, pr)
            assert all(MIN_TILT - 1e-4 <= x.weight <= PER_INTEREST_CAP + 1e-4 for x in tilts), (key, pr)
            assert all(x.weight <= STOCK_EACH + 1e-4 for x in picks), (key, pr)
            assert sum(x.weight for x in picks) <= STOCK_CAP_BY_RISK[f.risk_level] + 1e-4, (key, pr)


def test_every_holding_and_every_drop_or_shrink_has_a_reason(all_funds):
    for (d, h, themes, stocks), funds in all_funds:
        wanted = {THEMES[t] for t in themes} | set(stocks)
        for pr, f in funds.items():
            held = {x.ticker for x in f.holdings}
            explained = {(x.kind, x.ticker) for x in f.reasons}
            for x in f.holdings:
                assert any(t == x.ticker for _, t in explained), (x.ticker, pr)
                if x.role == "tilt" and x.weight < PER_INTEREST_CAP - 1e-4:
                    assert ("swing", x.ticker) in explained or ("cap", x.ticker) in explained, (x.ticker, pr)
            for t in wanted - held:
                assert ("dropped", t) in explained, (t, pr)


# ---------- golden funds for the four personas ----------

GOLDEN = {
    "maria": ("Conservative", 0, {"VTI": 0.189, "VXUS": 0.081, "BND": 0.65, "XLP": 0.08},
              ["score", "horizon", "drop", "core", "core", "bonds", "tilt"]),
    "jordan": ("Moderate", 33, {"VTI": 0.2889, "VXUS": 0.1238, "BND": 0.45, "ESPO": 0.0622, "XLC": 0.0751},
               ["score", "horizon", "drop", "core", "core", "bonds", "tilt", "tilt", "swing", "swing"]),
    "sam": ("Growth", 50, {"VTI": 0.3123, "VXUS": 0.1338, "BND": 0.4, "XLK": 0.0739, "XLV": 0.08},
            ["score", "horizon", "drop", "core", "core", "bonds", "tilt", "tilt", "swing", "overlap"]),
    "alex": ("Growth", 67, {"VTI": 0.3823, "VXUS": 0.1638, "BND": 0.3, "XLV": 0.08, "XLK": 0.0739},
             ["score", "horizon", "drop", "core", "core", "bonds", "tilt", "tilt", "swing", "overlap"]),
}


@pytest.mark.parametrize("cid", list(GOLDEN))
def test_golden_persona_funds(cid):
    label, score, weights, kinds = GOLDEN[cid]
    f = builder.build(PERSONA_ANSWERS[cid])
    assert (f.risk_label, f.score) == (label, score)
    assert {x.ticker: x.weight for x in f.holdings} == weights
    assert [x.kind for x in f.reasons] == kinds
