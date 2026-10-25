"""The company universe: every company has a price, a size and a sector; the core covers the US market.  OWNER: Evelyn."""
from core import market, universe


def test_core_is_about_50_us_companies_covering_every_sector():
    core = universe.core_companies()
    assert 45 <= len(core) <= 60
    assert all(universe.country(t) == "US" for t in core)
    assert {universe.sector(t) for t in core} == set(universe.SECTORS)
    assert all(sum(universe.sector(t) == s for t in core) >= 3 for s in universe.SECTORS)
    assert core == sorted(core, key=universe.market_cap, reverse=True)


def test_every_company_has_a_price_cap_sector_and_a_known_home_fund():
    for t, row in market.COMPANIES.items():
        assert float(row["price"]) > 0 and universe.market_cap(t) > 0, t
        assert universe.sector(t) in universe.SECTORS and row["etf"] in market.ETFS, t


def test_theme_companies_are_the_themes_biggest():
    two = universe.theme_companies("tech", 2)
    assert set(two) == {"AAPL", "MSFT"} and universe.market_cap(two[0]) >= universe.market_cap(two[1])
    assert universe.theme_companies("gold", 2) == []        # no gold companies: the theme keeps its fund
