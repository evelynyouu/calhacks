"""A fund as the six slices the pages show.  OWNER: Ethan.

A custom fund has about 60 holdings, too many to list flat or draw in a pie. Every page that shows the fund
(the fund card and its pie, the Portfolio list, the report) reads it through here: one slice per row, a
slice's holdings under it, and the US index grouped by sector. Pure Python on a Portfolio, so the numbers
the templates print are the ones the engine made. An older ETF fund reads the same way (see
Portfolio.slice_of), so nothing here needs a second code path.
"""
from __future__ import annotations

from core import market
from core.models import SLICES, Portfolio

# One plain line per slice, shown when the slice is opened. No numbers: the page prints those from the fund.
BLURBS = {
    "bonds": "The steady part. Bonds usually fall less than stocks do, so they cushion the drops.",
    "intl": "Companies outside the US, held as one fund so you don't have to pick countries.",
    "us_core": "Your own index: large US companies, with bigger ones getting more. Grouped by sector below.",
    "spend": "Companies you buy from, read from your bank purchases. This personalizes your fund; it isn't a prediction.",
    "interest": "The biggest companies in the industries you picked, or the industry fund when there are none.",
    "added": "Companies you added yourself.",
}
TOP_POSITIONS = 10        # the Portfolio tab lists this many before "Show all"


def view(p: Portfolio) -> list[dict]:
    """The slices that have holdings, in SLICES order. A slice has its weight, count, blurb, rows (biggest first)
    and, for the US index when companies carry a sector, `sectors`: the same rows grouped, biggest group first."""
    by, weights = p.by_slice(), p.slice_weights()
    out = []
    for key, label in SLICES:
        if not by[key]:
            continue
        rows = [row(h) for h in by[key]]
        out.append({"key": key, "label": label, "weight": weights[key], "count": len(rows), "blurb": BLURBS[key],
                    "rows": rows, "sectors": sector_groups(rows) if key == "us_core" else None,
                    "personalized": key == "spend"})
    return out


def row(h) -> dict:
    """One holding as a page shows it. A company is shown by its name; a fund by its short label."""
    i = market.info(h.ticker)
    return {"ticker": h.ticker, "name": h.name, "label": h.name if h.sector else i["label"], "about": i["about"],
            "weight": h.weight, "role": h.role, "sector": h.sector, "kind": "stock" if h.sector else i["kind"]}


def sector_groups(rows: list[dict]) -> list[dict] | None:
    """Rows grouped by sector with each group's total, biggest first. None when no row has a sector (a fund)."""
    if not any(r["sector"] for r in rows):
        return None
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["sector"] or "Other", []).append(r)
    return sorted(({"sector": s, "weight": round(sum(r["weight"] for r in rs), 6), "count": len(rs), "rows": rs}
                   for s, rs in groups.items()), key=lambda g: -g["weight"])


def pie(p: Portfolio | None) -> dict | None:
    """Chart.js payload: one wedge per slice that has holdings (six at most), in the order the card lists them."""
    if not p:
        return None
    s = view(p)
    return {"labels": [x["label"] for x in s], "values": [round(x["weight"] * 100, 1) for x in s]}


def top_positions(rows: list[dict], n: int = TOP_POSITIONS) -> tuple[list[dict], list[dict]]:
    """The n biggest rows (by value) and the rest, for a list that opens with its top n."""
    ranked = sorted(rows, key=lambda r: -r["value"])
    return ranked[:n], ranked[n:]
