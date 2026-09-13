import pytest

from market_intel.sources.openfda.client import (
    MAX_LIMIT,
    OpenFDAClient,
    OpenFDAError,
    build_query,
    date_range,
    field_in,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Records requests and serves canned paginated responses."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            status, payload = self.pages[idx]
            return FakeResponse(status, payload)
        return FakeResponse(200, {"meta": {"results": {"total": 0}}, "results": []})


def _page(total, results):
    return 200, {"meta": {"results": {"total": total}}, "results": results}


def test_search_paginates_until_exhausted():
    records = [{"k_number": f"K{i}"} for i in range(5)]
    session = FakeSession([
        _page(5, records[:2]),
        _page(5, records[2:4]),
        _page(5, records[4:]),
    ])
    client = OpenFDAClient(session=session, api_key=None)

    out = list(client.search("510k", search='product_code:"LRK"', limit=2))
    assert [r["k_number"] for r in out] == [f"K{i}" for i in range(5)]
    assert len(session.calls) == 3
    # skip advances correctly across pages
    assert [c[1]["skip"] for c in session.calls] == [0, 2, 4]


def test_search_respects_max_records():
    records = [{"k_number": f"K{i}"} for i in range(10)]
    session = FakeSession([_page(10, records)])
    client = OpenFDAClient(session=session, api_key=None)

    out = list(client.search("510k", search=None, limit=MAX_LIMIT, max_records=3))
    assert len(out) == 3


def test_api_key_added_to_params():
    session = FakeSession([_page(0, [])])
    client = OpenFDAClient(session=session, api_key="TESTKEY")
    list(client.search("classification"))
    assert session.calls[0][1]["api_key"] == "TESTKEY"


def test_404_genuine_no_matches_treated_as_empty():
    session = FakeSession([
        (404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}),
    ])
    client = OpenFDAClient(session=session)
    out = list(client.search("510k", search='product_code:"ZZZZ"'))
    assert out == []


def test_404_malformed_query_raises_instead_of_looking_empty():
    # A malformed query (bad syntax, unknown field, etc.) also comes back
    # as a 404 with code NOT_FOUND -- openFDA doesn't give it a distinct
    # error code. If this were swallowed the same way as a genuine empty
    # result, a broken query would silently look like "zero competitors
    # found" instead of surfacing as a bug.
    session = FakeSession([
        (404, {"error": {"code": "NOT_FOUND",
                          "message": "malformed query, unknown field 'bogus_field'"}}),
    ])
    client = OpenFDAClient(session=session)
    with pytest.raises(OpenFDAError):
        list(client.search("510k", search='bogus_field:"LRK"'))


def test_field_in_single_and_multi_value():
    assert field_in("product_code", ["LRK"]) == 'product_code:"LRK"'
    q = field_in("product_code", ["LRK", "LQZ"])
    assert q == 'product_code:("LRK" OR "LQZ")'
    assert field_in("product_code", []) == ""


def test_date_range_open_ended():
    assert date_range("decision_date", "2020-01-01", None) == "decision_date:[2020-01-01 TO *]"
    assert date_range("decision_date", None, None) == ""


def test_build_query_joins_with_and_and_drops_empty():
    q = build_query(['product_code:"LRK"', "", 'applicant:"Acme"'])
    assert q == 'product_code:"LRK" AND applicant:"Acme"'


def test_field_in_three_values_matches_expected_lucene_syntax():
    # Regression test: verifies the exact query openFDA expects for a
    # product-code basket -- plain spaces around OR, not literal '+' signs
    # (requests URL-encodes the query string itself, so pre-encoding it
    # here would send openFDA a literal '+' character instead of a boolean
    # operator).
    q = field_in("product_code", ["LRK", "LQZ", "PLC"])
    assert q == 'product_code:("LRK" OR "LQZ" OR "PLC")'
    assert build_query(["", None]) is None
