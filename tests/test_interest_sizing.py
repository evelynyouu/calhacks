"""Interest slices: sized per fund, scaled for how much each swings, capped in total, dropped by a rule;
and look-through overlap across the whole fund.  OWNER: Evelyn."""
import pytest

from core.portfolio import MIN_TILT, PER_INTEREST_CAP, TILT_CAP_BY_RISK, build_portfolio, lookthrough


def weight(p, t):
    return next((h.weight for h in p.holdings if h.ticker == t), 0.0)


def reasons(p, kind, ticker=None):
    return [r for r in p.reasons if r.kind == kind and (ticker is None or r.ticker == ticker)]


def test_one_interest_no_longer_takes_the_whole_cap():
    p = build_portfolio(4, ["healthcare"])
    assert weight(p, "XLV") == pytest.approx(PER_INTEREST_CAP)
    assert PER_INTEREST_CAP < TILT_CAP_BY_RISK[4]


def test_funds_that_swing_more_get_less_with_a_reason():
    p = build_portfolio(4, ["clean_energy", "staples"])
    assert MIN_TILT <= weight(p, "ICLN") < weight(p, "XLP")
    assert reasons(p, "swing", "ICLN") and not reasons(p, "swing", "XLP")


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_interests_stay_within_the_total_cap_and_shrinks_are_explained(level):
    p = build_portfolio(level, ["healthcare", "staples", "financials", "utilities"])
    tilts = [h for h in p.holdings if h.role == "tilt"]
    assert sum(h.weight for h in tilts) <= TILT_CAP_BY_RISK[level] + 1e-4
    assert all(h.weight >= MIN_TILT - 1e-4 for h in tilts)
    if sum(PER_INTEREST_CAP for _ in range(4)) > TILT_CAP_BY_RISK[level]:
        assert reasons(p, "cap") or reasons(p, "dropped")


def test_extras_are_dropped_by_overlap_not_pick_order():
    themes = ["healthcare", "tech", "biotech", "financials", "real_estate", "gold", "gaming", "utilities"]
    p = build_portfolio(1, themes)
    dropped = reasons(p, "dropped")
    kept = [h.ticker for h in p.holdings if h.role == "tilt"]
    assert "XLK" not in kept                                     # tech overlaps the core most, though picked 2nd
    assert all("overlap" in r.text for r in dropped)
    assert min(r.facts["overlap"] for r in dropped) >= max(r.facts["overlap"] for r in reasons(p, "tilt"))


def test_overlap_with_the_core_is_flagged():
    p = build_portfolio(3, ["tech"])
    note = next(r for r in reasons(p, "overlap") if r.ticker == "XLK")
    assert "VTI" in note.text and "approximate" in note.text


def test_one_company_over_8_percent_of_the_fund_is_flagged():
    p = build_portfolio(4, ["tech", "ai_chips"], ["NVDA"])
    assert lookthrough(p.holdings)["companies"]["NVDA"] >= 0.08
    note = next(r for r in reasons(p, "concentration") if r.ticker == "NVDA")
    assert "approximate" in note.text and note.text in p.overlap_warnings


def test_a_sector_far_above_the_market_is_flagged():
    p = build_portfolio(4, ["tech", "ai_chips", "software", "cyber"])
    assert any("Technology" in r.text for r in reasons(p, "sector"))
    assert not reasons(build_portfolio(4, ["healthcare"]), "sector")
