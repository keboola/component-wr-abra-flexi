"""ABRA Flexi (FlexiBee) writer component."""

import csv
import logging
import os
from collections.abc import Iterable, Iterator

import requests
from keboola.component import ComponentBase, UserException
from keboola.component.base import sync_action
from keboola.component.sync_actions import SelectElement, ValidationResult
from keboola.vcr import DefaultSanitizer

from client.flexibee_writer_client import FlexiBeeClientError, FlexiBeeWriterClient
from configuration import ColumnMapping, Configuration

# Keboola Storage API host, derived from the stack the component runs on. Used by the
# column-mapping sync action to read an input table's columns at config time (when no
# data is staged yet). Mirrors the convention used across CF writer components.
_STACK_SUFFIX = os.environ.get("KBC_STACKID", "connection.keboola.com").replace("connection.", "")

# Picked up automatically by the datadirtest VCR recorder. Only the credential needs
# sanitizing: DefaultSanitizer strips the HTTP Basic Authorization header and redacts
# password fields. The ABRA Flexi base_url is not secret — it lives in configs.json and
# is recorded as-is, so no URL rewriting is needed for replay to match.
VCR_SANITIZERS = [
    DefaultSanitizer(additional_sensitive_fields=["#password", "password"]),
]

_ERROR_COLUMNS = ["id", "error", "field", "code"]


def chunked[T](rows: Iterable[T], size: int) -> Iterator[list[T]]:
    """Yield successive lists of at most `size` items from `rows`."""
    batch: list[T] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def apply_column_mapping(row: dict, id_column: str, mapping: list[ColumnMapping]) -> dict:
    """Rename and filter input CSV columns per mapping before building the FlexiBee payload.

    - Empty mapping → passthrough: all columns returned unchanged.
    - With mapping: only mapped columns are kept (unmapped columns are dropped).
    - The `id_column` is always preserved under its original source name so that
      `build_winstrom_record` can extract it regardless of the mapping.
    - If a mapping entry's source column is not in the row, it is silently skipped.
    - If the id_column appears in the mapping list, the rename is ignored — the column
      stays under its original source name (the id is handled separately).
    """
    if not mapping:
        return row

    result: dict = {}

    # Always carry the id_column through unchanged
    if id_column in row:
        result[id_column] = row[id_column]

    for cm in mapping:
        if cm.source == id_column:
            continue  # id_column is handled above — do not rename it
        if cm.source in row:
            result[cm.destination] = row[cm.source]

    return result


def build_column_mapping_prefill(columns: list[str], id_column: str, field_names: set[str]) -> list[dict]:
    """Build prefilled `column_mapping` rows for the loadColumnMapping sync action.

    One row per input column except the `id_column` (handled separately at write time).
    A destination is auto-filled only when an identically named FlexiBee field exists;
    otherwise it is left blank for the user to pick.
    """
    return [{"source": col, "destination": col if col in field_names else ""} for col in columns if col != id_column]


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


class Component(ComponentBase):
    def __init__(self):
        super().__init__()

    def _build_client(self, cfg: Configuration) -> FlexiBeeWriterClient:
        return FlexiBeeWriterClient(
            base_url=cfg.base_url,
            company=cfg.company,
            username=cfg.username,
            password=cfg.password,
            ssl_verify=cfg.ssl_verify,
        )

    def run(self):
        cfg = Configuration(**self.configuration.parameters)
        if not cfg.evidence:
            raise UserException("No evidence type selected. Choose an evidence type for this row.")
        if not cfg.id_column:
            raise UserException(
                "No ID column configured. Set 'id_column' to the input column holding the record identifier."
            )

        input_tables = self.get_input_tables_definitions()
        if not input_tables:
            raise UserException("No input table found. Map exactly one input table to this row.")
        input_table = input_tables[0]

        client = self._build_client(cfg)

        failed_records: list[dict] = []
        total_created = total_updated = total_failed = 0

        with open(input_table.full_path, encoding="utf-8", newline="") as in_file:
            reader = csv.DictReader(in_file)
            if reader.fieldnames is None or cfg.id_column not in reader.fieldnames:
                raise UserException(
                    f"Input table has no column '{cfg.id_column}'. Available columns: {reader.fieldnames}"
                )
            for batch in chunked(reader, cfg.batch_size):
                mapped = [apply_column_mapping(row, cfg.id_column, cfg.column_mapping) for row in batch]
                records = [build_winstrom_record(row, cfg.id_column, cfg.id_type) for row in mapped]
                try:
                    result = client.write_records(cfg.evidence, records)
                except FlexiBeeClientError as exc:
                    raise UserException(str(exc))
                total_created += result.created
                total_updated += result.updated
                total_failed += result.failed
                failed_records.extend(result.failed_records)
                if cfg.fail_on_error and result.failed_records:
                    first = result.failed_records[0]
                    raise UserException(
                        f"Write failed for record id='{first['id']}': {first['error']} "
                        f"(field={first['field']}, code={first['code']}). "
                        f"{len(failed_records)} record(s) failed before aborting."
                    )

        self._write_error_table(failed_records)
        logging.info(
            "ABRA Flexi write complete for evidence '%s': created=%d, updated=%d, failed=%d",
            cfg.evidence,
            total_created,
            total_updated,
            total_failed,
        )
        if total_failed:
            logging.warning("%d record(s) failed; see the write_errors table.", total_failed)

    def _write_error_table(self, failed_records: list[dict]) -> None:
        """Write failed records to the write_errors table (always, even on 0 failures)."""
        table = self.create_out_table_definition(
            "write_errors.csv",
            primary_key=[],
            incremental=False,
            write_always=True,
            has_header=True,
        )
        with open(table.full_path, "w", encoding="utf-8", newline="") as out_file:
            writer = csv.DictWriter(out_file, fieldnames=_ERROR_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for record in failed_records:
                writer.writerow(record)
        self.write_manifest(table)

    @sync_action("testConnection")
    def test_connection(self) -> ValidationResult:
        cfg = Configuration(**self.configuration.parameters)
        client = self._build_client(cfg)
        try:
            client.test_connection()
        except FlexiBeeClientError as exc:
            raise UserException(str(exc))
        return ValidationResult("Connection successful.")

    @sync_action("listEvidences")
    def list_evidences(self) -> list[SelectElement]:
        cfg = Configuration(**self.configuration.parameters)
        client = self._build_client(cfg)
        try:
            evidences = client.list_evidences()
        except Exception as exc:  # noqa: BLE001
            raise UserException(f"Could not list evidences: {exc}")
        return [SelectElement(value=path, label=f"{name} ({path})") for path, name in evidences]

    def _get_input_table_columns(self, table_id: str) -> list[str]:
        """Read a Storage table's column names via the Storage API.

        Sync actions run at config time with no staged data, so the input columns
        cannot be read from disk — they are fetched from Storage by table id.
        """
        url = f"https://connection.{_STACK_SUFFIX}/v2/storage/tables/{table_id}"
        headers = {"X-StorageApi-Token": self.environment_variables.token}
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise UserException(f"Could not read columns of input table '{table_id}' from Keboola Storage: {exc}")
        return response.json().get("columns", [])

    def _flexibee_fields_metadata(self, cfg: Configuration) -> list[dict]:
        """FlexiBee writable fields for `cfg.evidence`, shaped for the destination dropdown."""
        client = self._build_client(cfg)
        try:
            fields = client.list_evidence_fields(cfg.evidence)
        except FlexiBeeClientError as exc:
            raise UserException(str(exc))
        return [
            {"field_name": f["name"], "label": f"{f['name']} (required)" if f["mandatory"] else f["name"]}
            for f in fields
        ]

    @sync_action("loadEvidenceFields")
    def load_evidence_fields(self) -> dict:
        """Populate the destination-field dropdown with the evidence's writable FlexiBee fields."""
        cfg = Configuration(**self.configuration.parameters)
        if not cfg.evidence:
            raise UserException("Select an evidence type before loading its fields.")
        return {"type": "data", "data": {"_metadata_": {"flexibee_fields": self._flexibee_fields_metadata(cfg)}}}

    @sync_action("loadColumnMapping")
    def load_column_mapping(self) -> dict:
        """Prefill `column_mapping`: one row per input column, FlexiBee fields offered as destinations.

        Reads the input table's columns from Storage and the evidence's writable fields
        from FlexiBee, builds a mapping row for each non-id input column (auto-matching a
        destination when the names are identical), and stores both lists in `_metadata_`
        so the source/destination dropdowns can render.
        """
        cfg = Configuration(**self.configuration.parameters)
        if not cfg.evidence:
            raise UserException("Select an evidence type before loading the column mapping.")
        input_mappings = self.configuration.tables_input_mapping
        if len(input_mappings) != 1:
            raise UserException(f"Map exactly one input table to this row first (found {len(input_mappings)}).")

        columns = self._get_input_table_columns(input_mappings[0].source)
        flexibee_fields = self._flexibee_fields_metadata(cfg)
        field_names = {f["field_name"] for f in flexibee_fields}
        mapping = build_column_mapping_prefill(columns, cfg.id_column, field_names)

        data = dict(self.configuration.parameters)
        data["column_mapping"] = mapping
        data["_metadata_"] = {"table": {"columns": columns}, "flexibee_fields": flexibee_fields}
        return {"type": "data", "data": data}


if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
