import textwrap
from typing import Iterator, Optional

import pytest

from market_intel import analysis, db, pipeline
from market_intel.normalize import CompanyNormalizer
from market_intel.segments import Segment


class FakeOpenFDAClient:
    def __init__(self, records_by_endpoint):
        self.records_by_endpoint = records_by_endpoint

    def search(self, endpoint: str, search: Optional[str] = None,
               max_records: Optional[int] = None, **kwargs) -> Iterator[dict]:
        records = self.records_by_endpoint.get(endpoint, [])
        for r in records[:max_records] if max_records else records:
            yield r


@pytest.fixture
def normalizer(tmp_path):
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text(textwrap.dedent("""
        companies:
          - canonical: Philips
            aliases: [Respironics Inc]
    """))
    categories = tmp_path / "categories.yaml"
    categories.write_text(textwrap.dedent("""
        categories:
          Philips: Strategic/Incumbent Medtech
    """))
    return CompanyNormalizer(aliases_path=aliases, categories_path=categories)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.sqlite")
    yield connection
    connection.close()


def test_run_segment_persists_raw_records_and_events(conn, normalizer):
    records = {
        "510k": [
            {"k_number": "K001", "applicant": "Respironics Inc",
             "product_code": "LRK", "decision_date": "20190501"},
            {"k_number": "K002", "applicant": "Acme Sleep Devices, LLC",
             "product_code": "LRK", "decision_date": "20190801"},
            {"k_number": "K003", "applicant": "Respironics Inc",
             "product_code": "LRK", "decision_date": "20200101"},
        ]
    }
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])

    stats = pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    assert stats["510k"]["status"] == "ok"
    assert stats["510k"]["records"] == 3

    raw_count = conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0]
    assert raw_count == 3

    events = conn.execute("SELECT company_canonical, company_category, year FROM events "
                          "ORDER BY record_id").fetchall()
    assert len(events) == 3
    companies = {e["company_canonical"] for e in events}
    assert companies == {"Philips", "Acme Sleep Devices"}
    philips_rows = [e for e in events if e["company_canonical"] == "Philips"]
    assert all(e["company_category"] == "Strategic/Incumbent Medtech" for e in philips_rows)


def test_run_segment_commits_so_data_survives_reconnect(tmp_path, normalizer):
    # Regression test: run_segment must commit its writes. A prior version
    # only wrote through the connection without ever calling commit(), so
    # everything vanished as soon as the connection was closed (SQLite
    # implicitly rolls back an uncommitted transaction on close) -- this
    # was invisible to tests that read back through the same connection,
    # since a connection sees its own uncommitted writes.
    db_path = tmp_path / "durability.sqlite"
    records = {"510k": [
        {"k_number": "K001", "applicant": "Respironics Inc",
         "product_code": "LRK", "decision_date": "20190501"},
    ]}
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])

    conn1 = db.connect(db_path)
    pipeline.run_segment(segment, conn=conn1, client=client, normalizer=normalizer)
    conn1.close()

    conn2 = db.connect(db_path)
    assert conn2.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0] == 1
    assert conn2.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert conn2.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1
    assert conn2.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0] == 1
    conn2.close()


def test_run_segment_skips_endpoint_with_no_applicable_filter(conn, normalizer):
    client = FakeOpenFDAClient({})
    segment = Segment(name="test_segment", device_classes=["2"], endpoints=["udi"])
    stats = pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)
    assert stats["udi"]["status"] == "skipped"


def test_pivot_has_subtotals_and_grand_total(conn, normalizer):
    records = {
        "510k": [
            {"k_number": "K001", "applicant": "Respironics Inc",
             "product_code": "LRK", "decision_date": "20190501"},
            {"k_number": "K002", "applicant": "Acme Sleep Devices, LLC",
             "product_code": "LRK", "decision_date": "20190801"},
            {"k_number": "K003", "applicant": "Respironics Inc",
             "product_code": "LRK", "decision_date": "20200101"},
        ]
    }
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])
    pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    df = analysis.load_events(conn, segment="test_segment")
    pivot = analysis.pivot_product_company_year(df)

    assert pivot.loc[("LRK", "Philips"), 2019] == 1
    assert pivot.loc[("LRK", "Philips"), 2020] == 1
    assert pivot.loc[("LRK", "Acme Sleep Devices"), 2019] == 1
    assert pivot.loc[("LRK", analysis.SUBTOTAL_LABEL), 2019] == 2
    assert pivot.loc[("LRK", analysis.SUBTOTAL_LABEL), 2020] == 1
    assert pivot.loc[(analysis.GRAND_TOTAL_LABEL, ""), 2019] == 2


def test_new_entrants_by_year(conn, normalizer):
    records = {
        "510k": [
            {"k_number": "K001", "applicant": "Respironics Inc",
             "product_code": "LRK", "decision_date": "20190501"},
            {"k_number": "K002", "applicant": "Acme Sleep Devices, LLC",
             "product_code": "LRK", "decision_date": "20200801"},
        ]
    }
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])
    pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    df = analysis.load_events(conn, segment="test_segment")
    entrants = analysis.new_entrants_by_year(df)
    row = entrants[entrants["company_canonical"] == "Acme Sleep Devices"].iloc[0]
    assert row["first_year"] == 2020


def test_rebuild_events_picks_up_new_alias(conn, tmp_path):
    aliases = tmp_path / "aliases_v1.yaml"
    aliases.write_text("companies: []\n")
    categories = tmp_path / "categories.yaml"
    categories.write_text("categories: {}\n")
    normalizer = CompanyNormalizer(aliases_path=aliases, categories_path=categories)

    records = {"510k": [
        {"k_number": "K001", "applicant": "Respironics Inc",
         "product_code": "LRK", "decision_date": "20190501"},
    ]}
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])
    pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    before = conn.execute("SELECT company_canonical FROM events").fetchone()[0]
    # No alias entry yet: falls back to a cleaned, title-cased name (legal
    # suffixes like "Inc" are stripped even for unmapped companies, so
    # spelling variants of the *same* unmapped company still collide).
    assert before == "Respironics"

    aliases.write_text(textwrap.dedent("""
        companies:
          - canonical: Philips
            aliases: [Respironics Inc]
    """))
    normalizer.reload()
    pipeline.rebuild_events(conn=conn, normalizer=normalizer)

    after = conn.execute("SELECT company_canonical FROM events").fetchone()[0]
    assert after == "Philips"


def test_build_510k_summary_url():
    assert (analysis.build_510k_summary_url("K052737")
            == "https://www.accessdata.fda.gov/cdrh_docs/pdf5/K052737.pdf")
    assert (analysis.build_510k_summary_url("K193503")
            == "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K193503.pdf")
    assert (analysis.build_510k_summary_url("k123456")
            == "https://www.accessdata.fda.gov/cdrh_docs/pdf12/K123456.pdf")


def test_build_510k_summary_url_handles_bad_input():
    assert analysis.build_510k_summary_url(None) is None
    assert analysis.build_510k_summary_url("") is None
    assert analysis.build_510k_summary_url("XYZ") is None


def test_listing_510k_includes_sections_1_through_4(conn, normalizer):
    records = {
        "510k": [{
            "k_number": "K193503",
            "device_name": "Acme Sleep Appliance",
            "applicant": "Respironics Inc",
            "contact": "Jane Doe",
            "address_1": "123 Main St",
            "address_2": None,
            "city": "Pittsburgh",
            "state": "PA",
            "zip_code": "15238",
            "country_code": "US",
            "date_received": "20190301",
            "decision_date": "20190501",
            "decision_code": "SESE",
            "decision_description": "Substantially Equivalent",
            "clearance_type": "Traditional",
            "third_party_flag": "N",
            "expedited_review_flag": "N",
            "advisory_committee": "SU",
            "advisory_committee_description": "General & Plastic Surgery",
            "statement_or_summary": "Summary",
            "product_code": "LRK",
            "openfda": {
                "device_class": "2",
                "regulation_number": "872.5570",
                "medical_specialty_description": "Dental",
            },
        }],
        "classification": [{
            "product_code": "LRK",
            "device_class": "2",
            "regulation_number": "872.5570",
            "review_panel": "DE",
            "definition": "An intraoral device intended to reduce snoring.",
            "implant_flag": "N",
            "life_sustain_support_flag": "N",
            "gmp_exempt_flag": "N",
        }],
    }
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"],
                       endpoints=["510k", "classification"])
    pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    listing = analysis.listing_510k(conn, segment="test_segment", normalizer=normalizer)

    assert list(listing.columns) == analysis.LISTING_510K_COLUMNS
    assert len(listing) == 1
    row = listing.iloc[0]
    assert row["k_number"] == "K193503"
    assert row["summary_pdf_url"] == "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K193503.pdf"
    assert row["applicant"] == "Respironics Inc"
    assert row["company_canonical"] == "Philips"
    assert row["company_category"] == "Strategic/Incumbent Medtech"
    assert row["decision_description"] == "Substantially Equivalent"
    assert row["device_class"] == "2"
    assert row["review_panel"] == "DE"
    assert row["definition"] == "An intraoral device intended to reduce snoring."


def test_listing_510k_blank_classification_fields_when_not_fetched(conn, normalizer):
    records = {"510k": [{
        "k_number": "K052737",
        "device_name": "Acme Device",
        "applicant": "Acme Corp",
        "product_code": "LRK",
        "decision_date": "20050101",
    }]}
    client = FakeOpenFDAClient(records)
    segment = Segment(name="test_segment", product_codes=["LRK"], endpoints=["510k"])
    pipeline.run_segment(segment, conn=conn, client=client, normalizer=normalizer)

    listing = analysis.listing_510k(conn, segment="test_segment", normalizer=normalizer)
    row = listing.iloc[0]
    assert row["review_panel"] is None
    assert row["definition"] is None
