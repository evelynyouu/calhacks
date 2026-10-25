"""Every decision the fund builder makes comes with a plain-English reason.  OWNER: Evelyn."""
import pytest

from core.models import Reason
from core.portfolio import build_portfolio


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_every_holding_has_a_reason(level):
    p = build_portfolio(level, ["tech", "healthcare", "clean_energy"], ["AAPL", "TSLA"])
    explained = {r.ticker for r in p.reasons if r.ticker}
    assert {h.ticker for h in p.holdings} <= explained
    assert all(isinstance(r, Reason) and r.text and r.kind for r in p.reasons)


def test_bonds_reason_names_the_share():
    p = build_portfolio(2, [])
    bonds = [r for r in p.reasons if r.kind == "bonds"]
    assert len(bonds) == 1 and bonds[0].ticker == "BND" and "45%" in bonds[0].text
    assert bonds[0].facts["bond_share"] == pytest.approx(0.45)


def test_dropped_interests_and_companies_get_reasons_and_still_warn():
    themes = ["tech", "healthcare", "biotech", "financials", "real_estate", "gold", "gaming", "utilities"]
    p = build_portfolio(1, themes, ["AAPL", "NVDA", "MSFT"])
    dropped = [r for r in p.reasons if r.kind == "dropped"]
    kept = {h.ticker for h in p.holdings}
    assert dropped and all(r.ticker not in kept for r in dropped)
    assert any("left out" in w for w in p.overlap_warnings)     # warnings come from the reasons
