from component import build_winstrom_record, chunked


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
