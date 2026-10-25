"""Interest slices: sized per fund, scaled for how much each swings, capped in total, dropped by a rule;
and look-through overlap across the whole fund.  OWNER: Evelyn."""
import pytest

from core.portfolio import MIN_TILT, PER_INTEREST_CAP, TILT_CAP_BY_RISK, build_portfolio, lookthrough


def budget(p, theme):
    """One interest's share of the fund. Interests are companies now (or the theme fund when we hold none), so
    add up its part of each (p.parts holds the exact interest part; the companies are also in the US index)."""
    from core.market import COMPANIES, ETFS
    def theme_of(t):
        return ETFS[t]["theme"] if t in ETFS else ETFS[COMPANIES[t]["etf"]]["theme"]
    return round(sum(w for t, w in p.parts.get("interest", {}).items() if theme_of(t) == theme), 4)


def reasons(p, kind, ticker=None):
    return [r for r in p.reasons if r.kind == kind and (ticker is None or r.ticker == ticker)]


def test_one_interest_no_longer_takes_the_whole_cap():
    p = build_portfolio(4, ["healthcare"])
    assert budget(p, "healthcare") == pytest.approx(PER_INTEREST_CAP)
    assert PER_INTEREST_CAP < TILT_CAP_BY_RISK[4]


def test_funds_that_swing_more_get_less_with_a_reason():
    p = build_portfolio(4, ["clean_energy", "staples"])
    assert MIN_TILT <= budget(p, "clean_energy") < budget(p, "staples")
    assert reasons(p, "swing", "ICLN") and not reasons(p, "swing", "XLP")   # sized by the theme's fund


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_interests_stay_within_the_total_cap_and_shrinks_are_explained(level):
    themes = ["healthcare", "staples", "financials", "utilities"]
    p = build_portfolio(level, themes)
    assert p.tilt_share <= TILT_CAP_BY_RISK[level] + 1e-4
    assert all(budget(p, th) >= MIN_TILT - 1e-4 for th in themes)
    if sum(PER_INTEREST_CAP for _ in range(4)) > TILT_CAP_BY_RISK[level]:
        assert reasons(p, "cap") or reasons(p, "dropped")


def test_extras_are_dropped_by_overlap_not_pick_order():
    themes = ["healthcare", "tech", "biotech", "financials", "real_estate", "gold", "gaming", "utilities"]
    p = build_portfolio(1, themes)
    dropped = reasons(p, "dropped")
    assert budget(p, "tech") == 0                                # tech overlaps the market most, though picked 2nd
    assert all("overlap" in r.text for r in dropped)
    assert min(r.facts["overlap"] for r in dropped) >= max(r.facts["overlap"] for r in reasons(p, "tilt"))


def test_an_interest_company_already_in_the_index_is_one_position_with_both_reasons():
    # was: "XLK's top companies are VTI's"; interests are companies now, so the overlap is the same company
    p = build_portfolio(3, ["tech"])
    for t in ("AAPL", "MSFT"):
        assert sum(h.ticker == t for h in p.holdings) == 1
        assert reasons(p, "combined", t) and reasons(p, "core", t) and reasons(p, "tilt", t)


def test_no_company_reaches_8_percent_even_held_for_three_reasons():
    # was: NVDA flagged at 8%+ through XLK, SMH and directly; now no position reaches the report's 8% line
    p = build_portfolio(4, ["tech", "ai_chips"], ["NVDA"])
    assert lookthrough(p.holdings)["companies"]["NVDA"] < 0.08
    assert reasons(p, "cap", "NVDA") and not reasons(p, "concentration", "NVDA")


def test_a_sector_far_above_the_market_is_flagged():
    p = build_portfolio(4, ["tech", "ai_chips", "software", "cyber"])
    assert any("Technology" in r.text for r in reasons(p, "sector"))
    assert not reasons(build_portfolio(4, ["healthcare"]), "sector")
