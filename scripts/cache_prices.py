"""Download ETF price history once and cache to data/prices/*.csv.  OWNER: Saahil.

Run tonight on good wifi:  python scripts/cache_prices.py
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKERS = [r["ticker"] for r in csv.DictReader((ROOT / "data" / "etfs.csv").open())]

if __name__ == "__main__":
    import yfinance as yf  # pip install yfinance
    out = ROOT / "data" / "prices"
    out.mkdir(exist_ok=True)
    for t in TICKERS:
        df = yf.download(t, start="2005-01-01", auto_adjust=True, progress=False)
        df.to_csv(out / f"{t}.csv")
        print(t, len(df))
