import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from parse_form4 import parse_form4_xml  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "sample_form4.xml").read_text()


def test_filters_below_10k_and_keeps_qualifying_purchase():
    txns = parse_form4_xml(
        FIXTURE,
        ticker="AMT",
        filed_date="2026-08-25",
        form_type="4",
        accession="0001053507-26-000139",
        filing_url="https://www.sec.gov/Archives/edgar/data/1053507/000105350726000139/",
    )
    # The $8,850 sale should be dropped by the $10,000 floor; only the
    # $500,733 purchase should survive.
    assert len(txns) == 1
    t = txns[0]
    assert t.type == "P"
    assert t.value == 500733.0
    assert t.shares == 2829.0
    assert t.price == 177.0


def test_role_and_ownership_and_10b5_1_detection():
    txns = parse_form4_xml(
        FIXTURE, ticker="AMT", filed_date="2026-08-25", form_type="4",
        accession="acc", filing_url="https://example.com/",
    )
    t = txns[0]
    assert t.role == "Director"
    assert t.is_director is True
    assert t.is_officer is False
    assert t.ownership == "Direct"
    assert t.shares_after == 18420.0
    # Detected via footnote-text fallback since the fixture uses that path.
    assert t.rule_10b5_1 is True


def test_amended_flag_comes_from_form_type_not_xml():
    txns = parse_form4_xml(
        FIXTURE, ticker="AMT", filed_date="2026-08-26", form_type="4/A",
        accession="acc2", filing_url="https://example.com/",
    )
    assert txns[0].amended is True


def test_below_threshold_only_filing_yields_zero_transactions():
    # A filing where every transaction is under $10,000 should parse cleanly
    # to an empty list, not an error — this is what "no qualifying purchases
    # today" looks like at the parser level.
    tiny_xml = FIXTURE.replace("<value>2829</value>", "<value>10</value>")
    txns = parse_form4_xml(
        tiny_xml, ticker="AMT", filed_date="2026-08-25", form_type="4",
        accession="acc3", filing_url="https://example.com/",
    )
    assert txns == []
