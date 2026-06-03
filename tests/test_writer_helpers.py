from component import (
    apply_column_mapping,
    build_column_mapping_prefill,
    build_winstrom_record,
    chunked,
)
from configuration import ColumnMapping


def test_prefill_auto_matches_identical_field_names_and_skips_id():
    rows = build_column_mapping_prefill(
        columns=["kod", "nazev", "company_name", "psc"],
        id_column="kod",
        field_names={"nazev", "ulice", "psc"},
    )
    assert rows == [
        {"source": "nazev", "destination": "nazev"},
        {"source": "company_name", "destination": ""},
        {"source": "psc", "destination": "psc"},
    ]


def test_prefill_empty_columns():
    assert build_column_mapping_prefill([], "kod", {"nazev"}) == []


def test_chunked_splits_evenly():
    assert list(chunked([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_chunked_handles_remainder():
    assert list(chunked([1, 2, 3], 2)) == [[1, 2], [3]]


def test_chunked_empty():
    assert list(chunked([], 2)) == []


def test_build_record_ext_id_prefixes_and_drops_id_column():
    row = {"kod": "ABC", "nazev": "Acme"}
    record = build_winstrom_record(row, id_column="kod", id_type="ext")
    assert record == {"nazev": "Acme", "id": "ext:ABC"}


def test_build_record_internal_id_passes_value():
    row = {"flexi_id": "15", "nazev": "Acme"}
    record = build_winstrom_record(row, id_column="flexi_id", id_type="internal")
    assert record == {"nazev": "Acme", "id": "15"}


def test_build_record_internal_empty_id_is_omitted():
    # An empty internal id means "create a new record" — omit the id key entirely.
    row = {"flexi_id": "", "nazev": "Acme"}
    record = build_winstrom_record(row, id_column="flexi_id", id_type="internal")
    assert record == {"nazev": "Acme"}


# --- apply_column_mapping tests ---


def test_apply_column_mapping_empty_is_passthrough():
    row = {"kod": "ABC", "company_name": "Acme", "street": "Main St"}
    result = apply_column_mapping(row, id_column="kod", mapping=[])
    assert result == row


def test_apply_column_mapping_renames_columns():
    mapping = [
        ColumnMapping(source="company_name", destination="nazev"),
        ColumnMapping(source="street", destination="ulice"),
    ]
    row = {"kod": "ABC", "company_name": "Acme", "street": "Main St"}
    result = apply_column_mapping(row, id_column="kod", mapping=mapping)
    assert result == {"kod": "ABC", "nazev": "Acme", "ulice": "Main St"}


def test_apply_column_mapping_drops_unmapped_columns():
    mapping = [ColumnMapping(source="company_name", destination="nazev")]
    row = {"kod": "ABC", "company_name": "Acme", "extra_col": "ignored"}
    result = apply_column_mapping(row, id_column="kod", mapping=mapping)
    assert "extra_col" not in result
    assert result == {"kod": "ABC", "nazev": "Acme"}


def test_apply_column_mapping_preserves_id_column_not_in_mapping():
    # id_column is always preserved even if not in the mapping list
    mapping = [ColumnMapping(source="company_name", destination="nazev")]
    row = {"kod": "ABC", "company_name": "Acme"}
    result = apply_column_mapping(row, id_column="kod", mapping=mapping)
    assert result["kod"] == "ABC"


def test_apply_column_mapping_id_column_in_mapping_is_ignored():
    # If the user accidentally includes the id_column in mapping, it's still preserved
    # under its original source name (not renamed), because id handling is separate.
    mapping = [
        ColumnMapping(source="kod", destination="some_dest"),
        ColumnMapping(source="company_name", destination="nazev"),
    ]
    row = {"kod": "ABC", "company_name": "Acme"}
    result = apply_column_mapping(row, id_column="kod", mapping=mapping)
    assert result["kod"] == "ABC"
    assert "some_dest" not in result


def test_apply_column_mapping_missing_source_column_skipped():
    # If a mapped source column is missing from the CSV row, skip it silently
    mapping = [
        ColumnMapping(source="company_name", destination="nazev"),
        ColumnMapping(source="missing_col", destination="mesto"),
    ]
    row = {"kod": "ABC", "company_name": "Acme"}
    result = apply_column_mapping(row, id_column="kod", mapping=mapping)
    assert "mesto" not in result
    assert result == {"kod": "ABC", "nazev": "Acme"}


def test_apply_column_mapping_integrates_with_build_winstrom_record():
    # Full pipeline: map columns → build record → FlexiBee payload
    mapping = [
        ColumnMapping(source="company_name", destination="nazev"),
        ColumnMapping(source="street", destination="ulice"),
    ]
    row = {"kod": "ABC", "company_name": "Acme", "street": "Main St", "extra": "dropped"}
    mapped = apply_column_mapping(row, id_column="kod", mapping=mapping)
    record = build_winstrom_record(mapped, id_column="kod", id_type="ext")
    assert record == {"id": "ext:ABC", "nazev": "Acme", "ulice": "Main St"}
