from market_intel.extract import extract_first, extract_id, extract_values


def test_extract_top_level_scalar():
    record = {"applicant": "Acme Corp"}
    assert extract_values(record, "applicant") == ["Acme Corp"]
    assert extract_first(record, "applicant") == "Acme Corp"


def test_extract_nested_object():
    record = {"openfda": {"product_code": "LRK"}}
    assert extract_values(record, "openfda.product_code") == ["LRK"]


def test_extract_through_array_of_objects():
    record = {
        "device": [
            {"openfda": {"product_code": "LRK"}},
            {"openfda": {"product_code": "LQZ"}},
        ]
    }
    assert extract_values(record, "device.openfda.product_code") == ["LRK", "LQZ"]


def test_extract_list_valued_leaf():
    record = {"product_code": ["LRK", "LQZ"]}
    assert extract_values(record, "product_code") == ["LRK", "LQZ"]


def test_extract_missing_path_returns_empty():
    record = {"foo": "bar"}
    assert extract_values(record, "openfda.product_code") == []
    assert extract_first(record, "openfda.product_code") is None


def test_extract_id_joins_multiple_fields():
    record = {"pma_number": "P123", "supplement_number": "S001"}
    assert extract_id(record, ["pma_number", "supplement_number"]) == "P123-S001"


def test_extract_id_skips_missing_fields():
    record = {"pma_number": "P123"}
    assert extract_id(record, ["pma_number", "supplement_number"]) == "P123"


def test_extract_id_empty_when_nothing_found():
    assert extract_id({}, ["k_number"]) == ""
