# Data sources

| File | Columns | Source | As of |
|---|---|---|---|
| `data/companies.csv` | `market_cap_b`, and `price` for rows added for the custom index | companiesmarketcap.com (US ranking pages 1-3, company pages for NVO, NTDOY, RBLX, ALNY, FSLR, ENPH, TER; the global page for TSM, ASML, TM, BABA) | 2026-10-24 |
| `data/companies.csv` | `sector` | GICS sector in plain words; Materials companies have home fund XLI (there's no materials fund in etfs.csv) | 2026-10-24 |
| `core/portfolio.py` `RETURNS` | 2008 and 2022 total returns for VTI, VXUS (VGTSX for 2008), BND | totalrealreturns.com/n/VTI,VGTSX,BND,VXUS | 2026-10-24 |
