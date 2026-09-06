"""
Rebuilds config/sp500_constituents.csv from two public sources:
  - Wikipedia's "List of S&P 500 companies" table (ticker, company, GICS sector,
    GICS sub-industry) — the de facto standard machine-readable source most
    open-source tools use, since S&P doesn't publish the list for free.
  - SEC's own ticker->CIK mapping (https://www.sec.gov/files/company_tickers.json)
    to attach the correct CIK to each ticker.

Run this quarterly, or whenever you hear about an S&P 500 rebalance, and
commit the updated CSV.

Usage: python scripts/build_constituents.py
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "config" / "sp500_constituents.csv"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "InsiderLedgerConstituentBuilder/1.0 (set SEC_USER_AGENT env var for your own contact info)",
)


def fetch_sp500_table() -> list[dict]:
    import pandas as pd  # local import: only needed for this one-off script

    tables = pd.read_html(WIKI_URL)
    df = tables[0]
    df = df.rename(
        columns={
            "Symbol": "ticker",
            "Security": "company",
            "GICS Sector": "sector",
            "GICS Sub-Industry": "industry",
        }
    )
    return df[["ticker", "company", "sector", "industry"]].to_dict("records")


def fetch_cik_map() -> dict[str, str]:
    resp = requests.get(SEC_TICKERS_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return {row["ticker"].upper(): str(row["cik_str"]) for row in data.values()}


def main() -> int:
    sp500 = fetch_sp500_table()
    cik_map = fetch_cik_map()

    rows = []
    misses = []
    for r in sp500:
        ticker = str(r["ticker"]).upper().replace(".", "-")
        cik = cik_map.get(ticker)
        if not cik:
            misses.append(ticker)
            continue
        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company": r["company"],
                "sector": r["sector"],
                "industry": r["industry"],
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "cik", "company", "sector", "industry"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} constituents to {OUT_PATH}")
    if misses:
        print(f"Could not resolve CIK for: {', '.join(misses)} — check these manually.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
