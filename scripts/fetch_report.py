"""
Entry point. Run as:  python scripts/fetch_report.py

Pipeline:
  1. Load config/sp500_constituents.csv (ticker, cik, company, sector, industry).
  2. For each issuer CIK, pull its Form 4 / 4-A filing history from EDGAR's
     company browse feed (this is indexed by issuer even though insiders are
     the actual filers — this is the standard, documented way EDGAR itself
     surfaces "insider transactions for company X").
  3. Keep only filings dated the current or previous calendar day (the report
     window). "Current day" is evaluated in America/New_York.
  4. For each matching filing, fetch its index.json to find the primary XML
     document, fetch that XML, and parse P/S non-derivative transactions
     >= $10,000 out of it.
  5. Aggregate everything into report.json via build_report.build_report().
  6. Always write the file, even if there were zero qualifying purchases —
     never skip publishing.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from edgar_client import EdgarClient
from parse_form4 import parse_form4_xml
from build_report import build_report

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sp500_constituents.csv"
DOCS_DATA_DIR = ROOT / "docs" / "data"
HISTORY_DIR = DOCS_DATA_DIR / "history"
NY = ZoneInfo("America/New_York")


def load_constituents(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError(f"{path} is empty — run scripts/build_constituents.py first.")
    return rows


def report_window(now_ny: dt.datetime) -> tuple[dt.date, dt.date, str]:
    """Current day + previous *calendar* day, per spec. If today is Monday,
    'previous calendar day' is Sunday (no filings expected, harmless) rather
    than Friday — change to `- dt.timedelta(days=3)` on Mondays if you'd
    rather always look back to the last business day instead."""
    today = now_ny.date()
    prev = today - dt.timedelta(days=1)
    label = f"Form 4 transactions filed on {prev.strftime('%b %-d')} and {today.strftime('%b %-d')}."
    return prev, today, label


def list_form4_filings(client: EdgarClient, cik: str) -> list[dict]:
    """Returns recent Form 4 / 4-A filings for an issuer via EDGAR's browse feed."""
    cik_padded = str(int(cik)).zfill(10)
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik_padded}&type=4&dateb=&owner=include&count=100&output=atom"
    )
    xml_text = client.get_text(url)
    root = _parse_atom(xml_text)
    filings = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = entry.findtext("a:title", default="", namespaces=ns)
        updated = entry.findtext("a:updated", default="", namespaces=ns)
        link_el = entry.find("a:link", ns)
        href = link_el.get("href") if link_el is not None else ""
        form_type = "4/A" if "4/A" in title else "4"
        accession = _accession_from_href(href)
        if accession:
            filings.append(
                {
                    "form_type": form_type,
                    "filed_date": updated[:10],
                    "accession": accession,
                    "index_href": href,
                    "cik": cik_padded,
                }
            )
    return filings


def _parse_atom(xml_text: str):
    from xml.etree import ElementTree as ET

    return ET.fromstring(xml_text)


def _accession_from_href(href: str) -> str | None:
    # href looks like .../Archives/edgar/data/{cik}/{accession-no-dashes}-index.htm
    if "-index" not in href:
        return None
    tail = href.rsplit("/", 1)[-1]
    return tail.split("-index")[0]


def fetch_primary_xml_url(client: EdgarClient, cik: str, accession: str) -> str | None:
    # SEC's actual folder names never contain dashes, even though the
    # display accession number (e.g. in filenames) does — strip them here
    # or every lookup 404s.
    accession_nodash = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/index.json"
    payload = client.get_json(index_url)
    items = payload.get("directory", {}).get("item", [])
    xml_candidates = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
    # Prefer a primary_doc.xml or the one that isn't an exhibit/POA.
    for name in xml_candidates:
        if "primary_doc" in name.lower():
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{name}"
    for name in xml_candidates:
        if "ex-24" not in name.lower() and "poa" not in name.lower():
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{name}"
    return None


def main() -> int:
    now_ny = dt.datetime.now(NY)
    prev_day, today_day, window_label = report_window(now_ny)
    data_cutoff_iso = now_ny.isoformat(timespec="minutes")

    constituents = load_constituents(CONFIG_PATH)
    sector_lookup = {row["ticker"]: row.get("sector", "Unknown") for row in constituents}

    client = EdgarClient()

    all_transactions: list[dict] = []
    filings_found = 0
    filings_parsed = 0
    errors: list[str] = []

    for row in constituents:
        ticker = row["ticker"]
        cik = row["cik"]
        try:
            filings = list_form4_filings(client, cik)
        except Exception as exc:  # noqa: BLE001 — one bad company shouldn't kill the run
            errors.append(f"{ticker}: browse-edgar lookup failed ({exc})")
            continue

        in_window = [
            f for f in filings
            if f["filed_date"] and dt.date.fromisoformat(f["filed_date"]) in (prev_day, today_day)
        ]
        filings_found += len(in_window)

        for f in in_window:
            try:
                xml_url = fetch_primary_xml_url(client, f["cik"], f["accession"])
                if not xml_url:
                    errors.append(f"{ticker}: no primary XML found for accession {f['accession']}")
                    continue
                xml_text = client.get_text(xml_url)
                txns = parse_form4_xml(
                    xml_text,
                    ticker=ticker,
                    filed_date=f["filed_date"],
                    form_type=f["form_type"],
                    accession=f["accession"],
                    filing_url=xml_url.rsplit("/", 1)[0] + "/",
                )
                for t in txns:
                    d = t.__dict__.copy()
                    d["ticker"] = ticker  # ensure config ticker, not issuer name variant
                    all_transactions.append(d)
                filings_parsed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ticker}: failed to parse accession {f['accession']} ({exc})")

    report = build_report(
        transactions=all_transactions,
        data_cutoff_iso=data_cutoff_iso,
        window_label=window_label,
        companies_checked=len(constituents),
        filings_found=filings_found,
        filings_parsed=filings_parsed,
        errors=errors,
        history_dir=HISTORY_DIR,
        sector_lookup=sector_lookup,
    )

    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DATA_DIR / "report.json").write_text(json.dumps(report, indent=2))
    print(
        f"Wrote report.json — {report['stats']['purchases']['count']} purchases, "
        f"{report['stats']['sales']['count']} sales, {len(errors)} errors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
