import textwrap

from market_intel.segments import Segment, list_segments


def test_query_for_510k_combines_product_codes_and_dates():
    seg = Segment(
        name="test",
        product_codes=["LRK", "LQZ"],
        date_start="2018-01-01",
        date_end="2020-12-31",
    )
    q = seg.query_for_endpoint("510k")
    assert 'product_code:("LRK"+OR+"LQZ")' in q
    assert "decision_date:[2018-01-01+TO+2020-12-31]" in q
    assert "+AND+" in q


def test_query_for_classification_ignores_applicant_filter():
    # classification has no company_field, so an applicants filter should
    # not appear in its query even if the segment defines one.
    seg = Segment(name="test", product_codes=["LRK"], applicants=["Acme Corp"])
    q = seg.query_for_endpoint("classification")
    assert "Acme" not in q
    assert 'product_code:"LRK"' in q


def test_query_returns_none_when_nothing_applies():
    seg = Segment(name="test", device_classes=["2"])
    # udi has no device_class_field and no product codes/applicants set
    assert seg.query_for_endpoint("udi") is None
    assert seg.is_empty_for_endpoint("udi") is True


def test_free_text_included():
    seg = Segment(name="test", free_text='device_name:"ambulatory"')
    q = seg.query_for_endpoint("classification")
    assert q == 'device_name:"ambulatory"'


def test_load_and_save_roundtrip(tmp_path):
    seg = Segment(
        name="my_segment",
        description="Test segment",
        product_codes=["LRK"],
        endpoints=["510k", "classification"],
    )
    seg.save(segments_dir=tmp_path)
    loaded = Segment.load("my_segment", segments_dir=tmp_path)
    assert loaded.product_codes == ["LRK"]
    assert loaded.endpoints == ["510k", "classification"]
    assert loaded.description == "Test segment"


def test_list_segments(tmp_path):
    (tmp_path / "a.yaml").write_text(textwrap.dedent("""
        description: Segment A
        product_codes: ["LRK"]
    """))
    (tmp_path / "b.yaml").write_text(textwrap.dedent("""
        description: Segment B
        product_codes: ["LQZ"]
    """))
    assert list_segments(segments_dir=tmp_path) == ["a", "b"]


def test_list_segments_empty_dir(tmp_path):
    assert list_segments(segments_dir=tmp_path / "does_not_exist") == []
