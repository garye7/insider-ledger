"""
Parses a single Form 4 / Form 4-A ownershipDocument XML into a list of
qualifying non-derivative transaction records (transaction code P or S).

Schema reference: SEC's Form 3/4/5 XML technical spec
(https://www.sec.gov/info/edgar/specifications/ownershipxmltechspec.htm).
This targets the standard <ownershipDocument> schema used since the mid-2000s.
A dedicated Rule 10b5-1 checkbox was added to the form in 2023; the tag name
for it has moved around SEC's schema revisions, so this parser checks a few
known locations and *also* falls back to scanning footnote text for
"10b5-1" as a safety net. Validate this against a handful of real filings
after your first live run — this is the one part of the schema most likely
to need a small patch (see README > Known follow-ups).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

@dataclass
class Transaction:
    ticker: str
    company: str
    issuer_cik: str
    insider: str
    insider_cik: str
    role: str
    is_officer: bool
    is_director: bool
    is_ten_pct_owner: bool
    type: str  # "P" or "S"
    tx_date: str
    filed_date: str
    form_type: str  # "4" or "4/A"
    amended: bool
    shares: float
    price: float
    value: float
    ownership: str  # "Direct" / "Indirect"
    shares_after: float | None
    rule_10b5_1: bool
    footnote_text: str = ""
    accession: str = ""
    filing_url: str = ""

def _text(el, path, default=None):
    if el is None:
        return default
    node = el.find(path)
    if node is None or node.text is None:
        return default
    return node.text.strip()

def _float(el, path, default=None):
    val = _text(el, path)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default

def _derive_role(reporting_owner) -> tuple[str, bool, bool, bool]:
    rel = reporting_owner.find("reportingOwnerRelationship")
    is_director = _text(rel, "isDirector") in ("1", "true", "True")
    is_officer = _text(rel, "isOfficer") in ("1", "true", "True")
    is_ten_pct = _text(rel, "isTenPercentOwner") in ("1", "true", "True")
    is_other = _text(rel, "isOther") in ("1", "true", "True")
    officer_title = _text(rel, "officerTitle", "") or ""

    if is_officer and officer_title:
        role = officer_title
    elif is_officer:
        role = "Officer"
    elif is_director:
        role = "Director"
    elif is_ten_pct:
        role = "10% Owner"
    elif is_other:
        role = _text(rel, "otherText", "Other") or "Other"
    else:
        role = "Reporting person"
    return role, is_officer, is_director, is_ten_pct

def _find_rule_10b5_1(root) -> bool:
    # Known candidate tag names across schema revisions. SEC has not been fully
    # consistent here across versions; check a few plausible spots.
    candidates = [
        ".//aff10b5One",
        ".//rule10b5-1",
        ".//isRule10b51",
    ]
    for path in candidates:
        node = root.find(path)
        if node is not None and _text(node, ".") in ("1", "true", "True"):
            return True
    # Fallback: footnotes very often say so explicitly even when the schema
    # field is absent or the filer didn't populate it.
    for fn in root.findall(".//footnote"):
        if fn.text and "10b5-1" in fn.text:
            return True
    return False

def parse_form4_xml(
    xml_text: str,
    *,
    ticker: str,
    filed_date: str,
    form_type: str,
    accession: str,
    filing_url: str,
) -> list[Transaction]:
    root = ET.fromstring(xml_text)

    issuer = root.find("issuer")
    company = _text(issuer, "issuerName", ticker)
    issuer_cik = _text(issuer, "issuerCik", "")

    footnote_text = " ".join(
        (fn.text or "").strip() for fn in root.findall(".//footnote")
    )
    amended = form_type.upper() == "4/A"
    rule_10b5_1 = _find_rule_10b5_1(root)

    transactions: list[Transaction] = []

    for owner in root.findall("reportingOwner"):
        owner_id = owner.find("reportingOwnerId")
        insider_name = _text(owner_id, "rptOwnerName", "Unknown")
        insider_cik = _text(owner_id, "rptOwnerCik", "")
        role, is_officer, is_director, is_ten_pct = _derive_role(owner)

        for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
            code = _text(txn, "transactionCoding/transactionCode")
            if code not in ("P", "S"):
                continue

            tx_date = _text(txn, "transactionDate/value", "")
            shares = _float(txn, "transactionAmounts/transactionShares/value", 0.0) or 0.0
            price = _float(txn, "transactionAmounts/transactionPricePerShare/value", 0.0) or 0.0
            value = round(shares * price, 2)
            if value < 10_000:
                continue

            ownership_code = _text(
                txn, "ownershipNature/directOrIndirectOwnership/value", "D"
            )
            ownership = "Direct" if ownership_code == "D" else "Indirect"
            shares_after = _float(
                txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
            )

            transactions.append(
                Transaction(
                    ticker=ticker,
                    company=company,
                    issuer_cik=issuer_cik,
                    insider=insider_name,
                    insider_cik=insider_cik,
                    role=role,
                    is_officer=is_officer,
                    is_director=is_director,
                    is_ten_pct_owner=is_ten_pct,
                    type=code,
                    tx_date=tx_date,
                    filed_date=filed_date,
                    form_type=form_type,
                    amended=amended,
                    shares=shares,
                    price=price,
                    value=value,
                    ownership=ownership,
                    shares_after=shares_after,
                    rule_10b5_1=rule_10b5_1,
                    footnote_text=footnote_text,
                    accession=accession,
                    filing_url=filing_url,
                )
            )

    return transactions
