"""Seed the demo personas into Nessie.  OWNER: Evelyn.  Logic lives in core/seed.py.

    python -m scripts.seed_nessie                 # seed every persona not in data/nessie_ids.json
    python -m scripts.seed_nessie sam             # just Sam
    python -m scripts.seed_nessie --reset sam     # delete Sam's accounts (by stored id) and recreate
    python -m scripts.seed_nessie --dry-run sam   # print every request, send nothing, write nothing

Everyone shares one NESSIE_API_KEY. Never call DELETE /data: it wipes every team's data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import nessie, seed   # noqa: E402


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("personas", nargs="*", help="default: all")
    ap.add_argument("--reset", metavar="PERSONA", help="delete and recreate this persona's accounts")
    ap.add_argument("--dry-run", action="store_true", help="print requests instead of sending them")
    args = ap.parse_args(argv)

    unknown = [p for p in args.personas + ([args.reset] if args.reset else []) if p not in nessie._SEED]
    if unknown:
        ap.error(f"unknown persona(s): {', '.join(unknown)}; choose from {', '.join(nessie._SEED)}")
    personas = args.personas or ([args.reset] if args.reset else None)

    if args.dry_run:
        with seed.dry_run():
            seed.seed(personas, reset=args.reset, save=False)
    else:
        seed.seed(personas, reset=args.reset)


if __name__ == "__main__":
    main()
