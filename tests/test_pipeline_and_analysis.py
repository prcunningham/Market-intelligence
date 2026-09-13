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
