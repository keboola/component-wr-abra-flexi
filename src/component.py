"""ABRA Flexi (FlexiBee) writer component."""

from collections.abc import Iterable, Iterator


def chunked(rows: Iterable[dict], size: int) -> Iterator[list[dict]]:
    """Yield successive lists of at most `size` items from `rows`."""
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_winstrom_record(row: dict, id_column: str, id_type: str) -> dict:
    """Turn one input row into a FlexiBee record dict.

    The `id_column` value becomes the record's `id`:
    - id_type="ext"      → "id": "ext:<value>" (upsert on external id)
    - id_type="internal" → "id": "<value>" (update existing numeric id);
      an empty value omits `id` entirely so FlexiBee creates a new record.
    The id column itself is removed from the data fields.
    """
    record = {k: v for k, v in row.items() if k != id_column}
    raw_id = row.get(id_column, "")
    if id_type == "ext":
        record["id"] = f"ext:{raw_id}"
    elif str(raw_id).strip():
        record["id"] = raw_id
    return record
