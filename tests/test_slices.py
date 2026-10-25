"""The slice contract (core/models.py) against the 58-holding fixture and an older ETF fund.  OWNER: Ethan."""
import pytest

from core.models import SLICES
from tests.fund_helpers import CAP_OF_US_CORE, make_legacy_etf_fund, make_portfolio_fixture


def test_fixture_is_a_fifty_eight_holding_fund_in_the_contracts_shape():
    p = make_portfolio_fixture()
    assert len(p.holdings) == 50 + 2 + 3 + 2 + 1 and sum(h.weight for h in p.holdings) == pytest.approx(1)
    by = p.by_slice()
    assert [k for k, _ in SLICES] == list(by) and [len(by[k]) for k in by] == [1, 1, 50, 3, 2, 1]
    assert all(h.sector for k in ("us_core", "spend", "interest", "added") for h in by[k])
    assert all(any(r.ticker == h.ticker for r in p.reasons) for h in p.holdings)
    assert max(h.weight for h in by["us_core"]) <= CAP_OF_US_CORE * p.slice_weights()["us_core"] + 1e-4


def test_slice_weights_add_up_and_every_key_is_always_there():
    w = make_portfolio_fixture().slice_weights()
    assert list(w) == [k for k, _ in SLICES] and sum(w.values()) == pytest.approx(1)
    assert w["bonds"] == pytest.approx(0.25) and w["spend"] == pytest.approx(0.08)


def test_the_pages_view_lists_each_slice_biggest_first():
    from core import slices
    for sl in slices.view(make_portfolio_fixture()):
        assert [r["weight"] for r in sl["rows"]] == sorted((r["weight"] for r in sl["rows"]), reverse=True)


def test_an_older_etf_fund_still_reads_into_the_six_slices():
    p = make_legacy_etf_fund()                               # VTI, VXUS, BND, XLK, AAPL: no slice set on any of them
    by = p.by_slice()
    assert [h.ticker for h in by["bonds"]] == ["BND"] and [h.ticker for h in by["intl"]] == ["VXUS"]
    assert [h.ticker for h in by["us_core"]] == ["VTI"] and [h.ticker for h in by["interest"]] == ["XLK"]
    assert [h.ticker for h in by["added"]] == ["AAPL"] and by["spend"] == []
    assert sum(p.slice_weights().values()) == pytest.approx(1, abs=1e-3)


def test_a_persona_can_hold_the_fixture_fund():
    from core import fund
    from tests.fund_helpers import make_custom_fund
    s = make_custom_fund("sam", 10_000)
    assert len(s.fund.portfolio.holdings) == 58 and fund.value(s.fund) == pytest.approx(10_000, abs=0.5)


def test_the_fixtures_us_index_is_in_size_order_and_ranks_are_true():
    us = make_portfolio_fixture().by_slice()["us_core"]
    top = {h.ticker for h in sorted(us, key=lambda h: -h.weight)[:6]}
    assert top == {"NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META"}      # the six biggest all hit the cap, so they tie
    p = make_portfolio_fixture()
    reason = next(r for r in p.reasons if r.ticker == "NVDA")
    assert "1st biggest" in reason.text and "2nd biggest" in next(r for r in p.reasons if r.ticker == "MSFT").text
