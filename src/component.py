"""ABRA Flexi (FlexiBee) writer component."""

import csv
import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import asdict

import requests
from keboola.component import ComponentBase, UserException
from keboola.component.base import sync_action
from keboola.component.sync_actions import SelectElement, ValidationResult
from keboola.vcr import DefaultSanitizer

from client.flexibee_writer_client import FlexiBeeClientError, FlexiBeeWriterClient
from configuration import ColumnMapping, Configuration

LOGGER = logging.getLogger(__name__)

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
    - If a mapping entry has an empty destination (an input column the user left
      unmapped in loadColumnMapping), it is skipped — otherwise it would create a
      blank-named field in the winstrom payload.
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
        if not cm.destination:
            continue  # unmapped input column — skip rather than emit a blank field
        if cm.source in row:
            result[cm.destination] = row[cm.source]

    return result


def build_column_mapping_prefill(
    columns: list[str],
    id_column: str,
    field_names: set[str],
    existing: list[dict] | None = None,
) -> list[dict]:
    """Merge prefilled `column_mapping` rows for the loadColumnMapping sync action.

    Existing rows are preserved verbatim — user-defined mappings are never overwritten.
    A new row is appended only for an input column that is not the `id_column` and is not
    already mapped; its destination is auto-filled when an identically named FlexiBee field
    exists, otherwise left blank for the user to pick.
    """
    existing = existing or []
    mapped_sources = {row.get("source") for row in existing}
    result = list(existing)
    for col in columns:
        if col == id_column or col in mapped_sources:
            continue
        result.append({"source": col, "destination": col if col in field_names else ""})
    return result


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
        # Single construction path: parse the config and build the client once, so
        # run() and every sync action share the same instances instead of re-parsing.
        self.cfg = Configuration(**self.configuration.parameters)
        self.client = FlexiBeeWriterClient(
            base_url=self.cfg.base_url,
            company=self.cfg.company,
            username=self.cfg.username,
            password=self.cfg.password,
            ssl_verify=self.cfg.ssl_verify,
        )

    def run(self):
        cfg = self.cfg
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

        total_created = total_updated = total_failed = 0

        error_table = self.create_out_table_definition(
            "write_errors.csv",
            primary_key=[],
            incremental=False,
            write_always=True,
            has_header=True,
        )
        with open(input_table.full_path, encoding="utf-8", newline="") as in_file:
            reader = csv.DictReader(in_file)
            if reader.fieldnames is None or cfg.id_column not in reader.fieldnames:
                # Validate before producing any output so an invalid input aborts cleanly.
                raise UserException(
                    f"Input table has no column '{cfg.id_column}'. Available columns: {reader.fieldnames}"
                )
            # Stream failures to disk per batch instead of holding them all in memory.
            with open(error_table.full_path, "w", encoding="utf-8", newline="") as err_file:
                error_writer = csv.DictWriter(err_file, fieldnames=_ERROR_COLUMNS, extrasaction="ignore")
                error_writer.writeheader()
                for batch in chunked(reader, cfg.batch_size):
                    mapped = [apply_column_mapping(row, cfg.id_column, cfg.column_mapping) for row in batch]
                    records = [build_winstrom_record(row, cfg.id_column, cfg.id_type) for row in mapped]
                    try:
                        result = self.client.write_records(cfg.evidence, records)
                    except FlexiBeeClientError as exc:
                        raise UserException(str(exc))
                    total_created += result.created
                    total_updated += result.updated
                    total_failed += result.failed
                    for failure in result.failed_records:
                        error_writer.writerow(asdict(failure))
                    if cfg.fail_on_error and result.failed_records:
                        first = result.failed_records[0]
                        # Abort without emitting a partial output table.
                        err_file.close()
                        os.remove(error_table.full_path)
                        raise UserException(
                            f"Write failed for record id='{first.id}': {first.error} "
                            f"(field={first.field}, code={first.code}). "
                            f"{total_failed} record(s) failed before aborting."
                        )

        self.write_manifest(error_table)
        LOGGER.info(
            "ABRA Flexi write complete for evidence '%s': created=%d, updated=%d, failed=%d",
            cfg.evidence,
            total_created,
            total_updated,
            total_failed,
        )
        if total_failed:
            LOGGER.warning("%d record(s) failed; see the write_errors table.", total_failed)

    @sync_action("testConnection")
    def test_connection(self) -> ValidationResult:
        try:
            self.client.test_connection()
        except FlexiBeeClientError as exc:
            raise UserException(str(exc))
        return ValidationResult("Connection successful.")

    @sync_action("listEvidences")
    def list_evidences(self) -> list[SelectElement]:
        try:
            evidences = self.client.list_evidences()
        except FlexiBeeClientError as exc:
            raise UserException(f"Could not list evidences: {exc}")
        return [SelectElement(value=e.path, label=f"{e.name} ({e.path})") for e in evidences]

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
            return response.json().get("columns", [])
        except (requests.RequestException, ValueError) as exc:
            raise UserException(f"Could not read columns of input table '{table_id}' from Keboola Storage: {exc}")

    def _flexibee_fields_metadata(self, cfg: Configuration) -> list[dict]:
        """FlexiBee writable fields for `cfg.evidence`, shaped for the destination dropdown."""
        try:
            fields = self.client.list_evidence_fields(cfg.evidence)
        except FlexiBeeClientError as exc:
            raise UserException(str(exc))
        return [
            {"field_name": f["name"], "label": f"{f['name']} (required)" if f["mandatory"] else f["name"]}
            for f in fields
        ]

    @sync_action("loadColumnMapping")
    def load_column_mapping(self) -> dict:
        """Add `column_mapping` rows for unmapped input columns; load FlexiBee fields as destinations.

        Reads the input table's columns from Storage and the evidence's writable fields
        from FlexiBee. Existing mapping rows are preserved — a row is appended only for an
        input column not already mapped (auto-matching a destination when the names are
        identical). Both lists are stored in `_metadata_` so the source/destination
        dropdowns can render.
        """
        cfg = self.cfg
        if not cfg.evidence:
            raise UserException("Select an evidence type before loading the column mapping.")
        input_mappings = self.configuration.tables_input_mapping
        if len(input_mappings) != 1:
            raise UserException(f"Map exactly one input table to this row first (found {len(input_mappings)}).")

        columns = self._get_input_table_columns(input_mappings[0].source)
        flexibee_fields = self._flexibee_fields_metadata(cfg)
        field_names = {f["field_name"] for f in flexibee_fields}
        existing = self.configuration.parameters.get("column_mapping", [])
        mapping = build_column_mapping_prefill(columns, cfg.id_column, field_names, existing)

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
