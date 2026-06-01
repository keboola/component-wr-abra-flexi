"""ABRA Flexi (FlexiBee) writer component."""

import csv
import logging
from collections.abc import Iterable, Iterator

from keboola.component import ComponentBase, UserException
from keboola.component.base import sync_action
from keboola.component.sync_actions import SelectElement, ValidationResult
from keboola.vcr import DefaultSanitizer

from client.flexibee_writer_client import FlexiBeeClientError, FlexiBeeWriterClient
from configuration import Configuration

# Picked up automatically by the datadirtest VCR recorder. Strips the HTTP Basic
# Authorization header and redacts password fields so no credentials are written
# to committed cassettes.
VCR_SANITIZERS = [
    DefaultSanitizer(additional_sensitive_fields=["#password", "password"]),
]

_ERROR_COLUMNS = ["id", "error", "field", "code"]


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
                records = [build_winstrom_record(row, cfg.id_column, cfg.id_type) for row in batch]
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
