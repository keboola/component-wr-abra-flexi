# wr-abra-flexi Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Keboola writer that pushes rows from a Storage input table into an ABRA Flexi (FlexiBee) evidence type via its REST API, with configurable upsert IDs and a per-record error output table.

**Architecture:** One config row per evidence type. Root config holds connection/auth; row config holds evidence name, ID column/type, batch size, and fail-on-error flag. A `FlexiBeeWriterClient` (mirroring the sibling `ex-flexibee` extractor's client) owns all HTTP; `component.py` `run()` reads the input CSV in batches, builds `{"winstrom": {evidence: [...]}}` payloads, POSTs them, and routes failed records to a `write_errors` output table. Two sync actions (`testConnection`, `listEvidences`) validate credentials and populate the evidence dropdown.

**Tech Stack:** Python 3.14, `keboola.component`, `keboola.http_client`, `pydantic` v2, `pytest`, `keboola.datadirtest` (VCR).

---

## Reference: the sibling extractor

The component at `../component-ex-flexibee` is the canonical reference for conventions. In particular:
- `src/client/flexibee_client.py` — `HttpClient` setup (Basic auth, retries, timeouts), `list_evidences()`, `test_connection()`.
- `src/component.py` — `@sync_action` usage, `VCR_SANITIZERS` with `DefaultSanitizer`, exit-code `__main__` block.
- `tests/test_functional.py` + `tests/functional/<case>/` — VCR datadir test layout.

Read those files when a task references "the extractor pattern". The writer **reuses** these patterns rather than inventing new ones.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/configuration.py` | Single merged Pydantic `Configuration` model (root + row fields). Validates early, raises `UserException`. |
| `src/client/__init__.py` | Package marker. |
| `src/client/flexibee_writer_client.py` | `FlexiBeeWriterClient`, `WriteResult`, `FlexiBeeClientError`. All HTTP; uses `post_raw()` to capture 4xx bodies. |
| `src/component.py` | `Component.run()` orchestrator, `chunked()` + `build_winstrom_record()` helpers, `_write_error_table()`, two `@sync_action`s, `VCR_SANITIZERS`. |
| `tests/test_configuration.py` | Unit tests for the config model. |
| `tests/test_writer_helpers.py` | Unit tests for `chunked()` and `build_winstrom_record()`. |
| `tests/test_flexibee_writer_client.py` | Unit tests for the client with a mocked `HttpClient`. |
| `tests/test_functional.py` | VCR datadir test runner (parametrized). |
| `tests/functional/<case>/...` | Per-case VCR fixtures. |
| `component_config/configSchema.json` | Root schema. **Built by `component-build-ui` (Task 9).** |
| `component_config/configRowSchema.json` | Row schema with `listEvidences` dropdown. **Built by `component-build-ui` (Task 9).** |
| `README.md`, `component_config/*.md` | Docs (Task 10). |

---

## Task 1: Add pytest configuration to pyproject.toml

The scaffolded `pyproject.toml` has no pytest config, so `from configuration import ...` won't resolve in tests. Add `pythonpath`/`testpaths` exactly like the extractor.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Append the pytest config block**

Add this section to the end of `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Verify pytest collects from src**

Run: `uv run pytest --collect-only -q`
Expected: command exits 0 (no collection errors about missing modules). It's fine if "no tests ran" — we only care that import paths resolve.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: configure pytest pythonpath and testpaths"
```

---

## Task 2: Configuration model

A single Pydantic model receives the platform-merged config (root + row fields together — the component never sees the split). Mirrors the extractor's `Configuration` style: `__init__` converts `ValidationError` to `UserException`; `#password` via alias.

**Files:**
- Create: `tests/test_configuration.py`
- Create: `src/configuration.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_configuration.py`:

```python
import pytest

from configuration import Configuration

BASE = {
    "base_url": "https://demo.flexibee.eu",
    "company": "demo",
    "username": "winstrom",
    "#password": "winstrom",
    "evidence": "adresar",
    "id_column": "kod",
}


def test_minimal_config_valid():
    cfg = Configuration(**BASE)
    assert cfg.base_url == "https://demo.flexibee.eu"
    assert cfg.company == "demo"
    assert cfg.password == "winstrom"
    assert cfg.evidence == "adresar"
    assert cfg.id_column == "kod"
    assert cfg.id_type == "ext"
    assert cfg.batch_size == 100
    assert cfg.fail_on_error is False
    assert cfg.ssl_verify is True


def test_password_alias_mapping():
    cfg = Configuration(**BASE)
    assert cfg.password == "winstrom"


def test_missing_required_field_raises_user_exception():
    from keboola.component import UserException

    data = dict(BASE)
    del data["company"]
    with pytest.raises(UserException):
        Configuration(**data)


def test_invalid_id_type_raises_user_exception():
    from keboola.component import UserException

    data = dict(BASE, id_type="bogus")
    with pytest.raises(UserException):
        Configuration(**data)


def test_batch_size_out_of_range_raises_user_exception():
    from keboola.component import UserException

    with pytest.raises(UserException):
        Configuration(**dict(BASE, batch_size=0))
    with pytest.raises(UserException):
        Configuration(**dict(BASE, batch_size=1000))


def test_id_type_internal_accepted():
    cfg = Configuration(**dict(BASE, id_type="internal"))
    assert cfg.id_type == "internal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_configuration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'configuration'` (or import error), because `src/configuration.py` still holds the cookiecutter template.

- [ ] **Step 3: Write the Configuration model**

Replace the entire contents of `src/configuration.py` with:

```python
"""Configuration model for the ABRA Flexi writer.

The platform merges root config (connection) and row config (evidence/write
options) before the component runs, so this single model receives both.
"""

import logging

from keboola.component.exceptions import UserException
from pydantic import BaseModel, Field, ValidationError, field_validator


class Configuration(BaseModel):
    # --- connection (root config) ---
    base_url: str
    company: str
    username: str
    password: str = Field(alias="#password")
    ssl_verify: bool = True
    debug: bool = False

    # --- write options (row config) ---
    evidence: str = ""
    id_column: str = ""
    id_type: str = "ext"
    batch_size: int = 100
    fail_on_error: bool = False

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            error_messages = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(error_messages)}")

        if self.debug:
            logging.debug("Component will run in Debug mode")

    @field_validator("id_type")
    @classmethod
    def _validate_id_type(cls, v: str) -> str:
        if v not in ("ext", "internal"):
            raise ValueError("id_type must be 'ext' or 'internal'")
        return v

    @field_validator("batch_size")
    @classmethod
    def _validate_batch_size(cls, v: int) -> int:
        if v < 1 or v > 500:
            raise ValueError("batch_size must be between 1 and 500")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_configuration.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_configuration.py src/configuration.py
git commit -m "feat: add writer configuration model"
```

---

## Task 3: Writer helper functions (batching + record building)

Two pure functions live in `component.py`: `chunked()` splits an iterable into fixed-size batches; `build_winstrom_record()` turns one input row into a FlexiBee record dict, moving the ID column into the `id` field. Pure functions = easy TDD before any HTTP exists.

**Files:**
- Create: `tests/test_writer_helpers.py`
- Create: `src/component.py` (minimal — just the two helpers for now; full `run()` comes in Task 5)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_writer_helpers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_writer_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_winstrom_record' from 'component'` (the file is still the cookiecutter template).

- [ ] **Step 3: Write minimal component.py with the two helpers**

Replace the entire contents of `src/component.py` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_writer_helpers.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_writer_helpers.py src/component.py
git commit -m "feat: add batching and record-building helpers"
```

---

## Task 4: FlexiBeeWriterClient

The client owns all HTTP. It reuses the extractor's `HttpClient` construction (Basic auth, retries, timeouts) and adds `write_records()`. Because `HttpClient.post()` always calls `raise_for_status()` (losing FlexiBee's 400 error body), `write_records()` uses **`post_raw()`** to inspect the status and parse the body itself.

**Files:**
- Create: `src/client/__init__.py`
- Create: `src/client/flexibee_writer_client.py`
- Create: `tests/test_flexibee_writer_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flexibee_writer_client.py`:

```python
from unittest.mock import MagicMock

import pytest
import requests

from client.flexibee_writer_client import (
    FlexiBeeClientError,
    FlexiBeeWriterClient,
    WriteResult,
)


def _make_client():
    client = FlexiBeeWriterClient(
        base_url="https://demo.flexibee.eu",
        company="demo",
        username="winstrom",
        password="winstrom",
    )
    client._http = MagicMock()
    return client


def _raw_response(status_code: int, json_body=None, text: str = ""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text
    return resp


def test_write_records_all_success():
    client = _make_client()
    client._http.post_raw.return_value = _raw_response(
        201,
        {
            "winstrom": {
                "success": True,
                "stats": {"created": 2, "updated": 0, "failed": 0},
                "results": [{"id": 1, "success": True}, {"id": 2, "success": True}],
            }
        },
    )
    result = client.write_records("adresar", [{"id": "ext:A"}, {"id": "ext:B"}])
    assert isinstance(result, WriteResult)
    assert result.created == 2
    assert result.failed == 0
    assert result.failed_records == []


def test_write_records_partial_failure_400_is_parsed():
    client = _make_client()
    client._http.post_raw.return_value = _raw_response(
        400,
        {
            "winstrom": {
                "success": False,
                "stats": {"created": 1, "updated": 0, "failed": 1},
                "results": [
                    {"id": 1, "success": True},
                    {"id": "ext:B", "error": "ic already exists", "for": "ic", "code": "UNIQUE"},
                ],
            }
        },
    )
    result = client.write_records("adresar", [{"id": "ext:A"}, {"id": "ext:B"}])
    assert result.created == 1
    assert result.failed == 1
    assert result.failed_records == [
        {"id": "ext:B", "error": "ic already exists", "field": "ic", "code": "UNIQUE"}
    ]


def test_write_records_auth_error_raises():
    client = _make_client()
    client._http.post_raw.return_value = _raw_response(401, {"winstrom": {}})
    with pytest.raises(FlexiBeeClientError, match="Authentication"):
        client.write_records("adresar", [{"id": "ext:A"}])


def test_write_records_network_error_raises():
    client = _make_client()
    client._http.post_raw.side_effect = requests.ConnectionError("boom")
    with pytest.raises(FlexiBeeClientError, match="failed"):
        client.write_records("adresar", [{"id": "ext:A"}])


def test_write_records_non_json_response_raises():
    client = _make_client()
    client._http.post_raw.return_value = _raw_response(500, json_body=None, text="<html>err</html>")
    with pytest.raises(FlexiBeeClientError, match="non-JSON"):
        client.write_records("adresar", [{"id": "ext:A"}])


def test_list_evidences_parses_pairs():
    client = _make_client()
    client._http.get.return_value = {
        "evidences": {"evidence": [{"evidencePath": "adresar", "evidenceName": "Adresar"}]}
    }
    assert client.list_evidences() == [("adresar", "Adresar")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_flexibee_writer_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'client.flexibee_writer_client'`.

- [ ] **Step 3: Create the package marker**

Create `src/client/__init__.py` (empty file):

```python
```

- [ ] **Step 4: Write the client**

Create `src/client/flexibee_writer_client.py`:

```python
"""Client for writing records to the ABRA Flexi (FlexiBee) REST API."""

from __future__ import annotations

from dataclasses import dataclass, field

import requests
from keboola.http_client import HttpClient


class FlexiBeeClientError(Exception):
    """Raised for FlexiBee API errors that should surface to the user."""


@dataclass
class WriteResult:
    """Outcome of one batch write."""

    created: int
    updated: int
    failed: int
    failed_records: list[dict] = field(default_factory=list)


class FlexiBeeWriterClient:
    """Writes records to one ABRA Flexi company over HTTP Basic auth.

    Writes POST to the evidence *list* URL with a `{"winstrom": {evidence: [...]}}`
    body. FlexiBee upserts each record based on its `id` (numeric, `code:`, or
    `ext:`). Per-record validation failures come back as HTTP 400 with a
    `winstrom.results[]` body, so we use `post_raw()` (which does not call
    `raise_for_status`) to read that body.
    """

    # (connect, read) timeout in seconds — bounds each HTTP attempt so an
    # unreachable host fails fast instead of hanging the job.
    _HTTP_TIMEOUT = (10, 60)

    def __init__(
        self,
        base_url: str,
        company: str,
        username: str,
        password: str,
        ssl_verify: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.company = company
        self.username = username
        self.password = password
        self.ssl_verify = ssl_verify
        self._http = HttpClient(
            base_url=f"{self.base_url}/",
            auth=(self.username, self.password),
            max_retries=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
        )

    def write_records(self, evidence: str, records: list[dict]) -> WriteResult:
        """POST one batch of records to an evidence; return the parsed outcome."""
        endpoint = f"c/{self.company}/{evidence}.json"
        body = {"winstrom": {evidence: records}}
        try:
            response = self._http.post_raw(
                endpoint_path=endpoint,
                json=body,
                verify=self.ssl_verify,
                timeout=self._HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FlexiBeeClientError(f"Write to evidence '{evidence}' failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise FlexiBeeClientError(
                f"Authentication or permission error (HTTP {response.status_code}) writing to "
                f"'{evidence}'. Check the credentials and that the user has REST API write access."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise FlexiBeeClientError(
                f"Unexpected non-JSON response (HTTP {response.status_code}) from '{evidence}': "
                f"{response.text[:500]}"
            ) from exc

        win = payload.get("winstrom", {})
        stats = win.get("stats", {})
        results = win.get("results", [])
        failed_records = [
            {
                "id": str(r.get("id", "")),
                "error": r.get("error", ""),
                "field": r.get("for", ""),
                "code": r.get("code", ""),
            }
            for r in results
            if r.get("error") or r.get("success") is False
        ]
        return WriteResult(
            created=int(stats.get("created", 0)),
            updated=int(stats.get("updated", 0)),
            failed=int(stats.get("failed", len(failed_records))),
            failed_records=failed_records,
        )

    def list_evidences(self) -> list[tuple[str, str]]:
        """Return (evidencePath, evidenceName) pairs for the connected company."""
        endpoint = f"c/{self.company}/evidence-list.json"
        try:
            data = self._http.get(
                endpoint_path=endpoint, verify=self.ssl_verify, timeout=self._HTTP_TIMEOUT
            )
        except requests.RequestException as exc:
            raise FlexiBeeClientError(f"Could not list evidences: {exc}") from exc
        evidences = data.get("evidences", {}).get("evidence", [])
        return [(e.get("evidencePath", ""), e.get("evidenceName", "")) for e in evidences]

    def test_connection(self) -> None:
        """Hit evidence-list to confirm auth/host. Raises FlexiBeeClientError on failure."""
        try:
            self.list_evidences()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a connection failure
            raise FlexiBeeClientError(f"Could not connect to ABRA Flexi: {exc}") from exc
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_flexibee_writer_client.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 6: Commit**

```bash
git add src/client/__init__.py src/client/flexibee_writer_client.py tests/test_flexibee_writer_client.py
git commit -m "feat: add FlexiBee writer client with batch write and error parsing"
```

---

## Task 5: Component run() orchestration + error table

Wire the helpers and client into `run()`: read the input CSV in batches, build records, write each batch, accumulate failures, and emit the `write_errors` table. `run()` is a thin orchestrator; the heavy lifting is already in the helpers and client.

**Files:**
- Modify: `src/component.py` (add imports, `Component` class, `_write_error_table`, `__main__`)

- [ ] **Step 1: Replace component.py with the full orchestrator**

Replace the entire contents of `src/component.py` with (keeps the helpers from Task 3, adds the class):

```python
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
                    f"Input table has no column '{cfg.id_column}'. "
                    f"Available columns: {reader.fieldnames}"
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
```

- [ ] **Step 2: Re-run the helper unit tests (regression check)**

Run: `uv run pytest tests/test_writer_helpers.py -v`
Expected: PASS — the helpers are unchanged, so all 6 stay green. (This confirms the rewrite didn't break their import or behavior.)

- [ ] **Step 3: Run the full unit suite**

Run: `uv run pytest tests/test_configuration.py tests/test_writer_helpers.py tests/test_flexibee_writer_client.py -v`
Expected: PASS — all unit tests green.

- [ ] **Step 4: Commit**

```bash
git add src/component.py
git commit -m "feat: implement writer run() orchestration and error table"
```

---

## Task 6: Lint clean-up

Run Ruff and fix anything it flags. The project config is `line-length=120`, `target-version=py314`, lint extends `["I", "UP", "G"]`.

**Files:**
- Modify: any of the above as Ruff dictates.

- [ ] **Step 1: Run the linter**

Run: `uv run ruff check src tests`
Expected: ideally "All checks passed!". If not, note each finding.

- [ ] **Step 2: Auto-fix and format**

Run:
```bash
uv run ruff check --fix src tests
uv run ruff format src tests
```

- [ ] **Step 3: Re-run the unit suite to confirm fixes didn't break anything**

Run: `uv run pytest tests/test_configuration.py tests/test_writer_helpers.py tests/test_flexibee_writer_client.py -v`
Expected: PASS.

- [ ] **Step 4: Commit (only if Ruff changed files)**

```bash
git add -A
git commit -m "style: ruff lint and format"
```

---

## Task 7: VCR functional test runner + first happy-path case

Set up the datadir VCR harness (same as the extractor) and record the first case against the sandbox. Sandbox credentials are in `secrets.json` at the repo root in Keboola `{"parameters": {...}}` format — **do not read or echo the secret values**; copy the file into place where the recorder expects it per the `generate-vcr-tests` skill, or let that skill drive recording.

**Files:**
- Create: `tests/test_functional.py`
- Create: `tests/functional/01_happy_path_adresar/source/data/config.json`
- Create: `tests/functional/01_happy_path_adresar/source/data/in/tables/adresar.csv`
- Recorded: `tests/functional/01_happy_path_adresar/source/data/cassettes/*` (produced by the recorder)

- [ ] **Step 1: Create the VCR test runner**

Create `tests/test_functional.py` (identical pattern to the extractor):

```python
"""Functional tests for the component using VCR cassettes."""

from pathlib import Path

import pytest
from keboola.datadirtest.vcr import VCRDataDirTester, get_test_cases

FUNCTIONAL_DIR = str(Path(__file__).parent / "functional")
COMPONENT_SCRIPT = str(Path(__file__).parent.parent / "src" / "component.py")


@pytest.mark.parametrize("test_name", get_test_cases(FUNCTIONAL_DIR))
def test_functional(test_name):
    """Run a single VCR functional test case."""
    tester = VCRDataDirTester(
        data_dir=FUNCTIONAL_DIR,
        component_script=COMPONENT_SCRIPT,
        selected_tests=[test_name],
    )
    tester.run()
```

- [ ] **Step 2: Create the happy-path input table and config**

Create `tests/functional/01_happy_path_adresar/source/data/in/tables/adresar.csv`:

```csv
kod,nazev,ulice,mesto,psc
EXT001,Acme s.r.o.,Hlavni 1,Praha,11000
EXT002,Beta a.s.,Vedlejsi 2,Brno,60200
```

Create `tests/functional/01_happy_path_adresar/source/data/config.json`:

```json
{
  "action": "run",
  "parameters": {
    "base_url": "https://demo.flexibee.eu",
    "company": "demo",
    "username": "winstrom",
    "#password": "winstrom",
    "ssl_verify": true,
    "evidence": "adresar",
    "id_column": "kod",
    "id_type": "ext",
    "batch_size": 100,
    "fail_on_error": false
  }
}
```

(The input table must also be declared in the storage input mapping inside `config.json` if the datadir runner requires it — follow the extractor's existing cases for the exact `storage.input.tables` shape. Match `destination` to `adresar.csv`.)

- [ ] **Step 3: Record the cassette via the generate-vcr-tests skill**

This step is interactive and credential-bound — hand it to the dedicated skill rather than scripting it here:

Invoke `component-developer:generate-vcr-tests` (or `/generate-vcr-tests`) for case `01_happy_path_adresar`, pointing it at `secrets.json` for the live credentials. It records `cassettes/requests.json` (sanitized — Authorization header stripped by `VCR_SANITIZERS`) and the expected output snapshot.

After recording, manually confirm no secret leaked:

Run: `grep -ri "winstrom:winstrom\|Basic " tests/functional/01_happy_path_adresar/source/data/cassettes/ || echo "CLEAN"`
Expected: `CLEAN` (the sanitizer should have stripped the Authorization header).

- [ ] **Step 4: Run the functional test**

Run: `uv run pytest tests/test_functional.py -v -k 01_happy_path`
Expected: PASS — the component replays the cassette, writes `adresar` records, and produces an empty `write_errors` table.

- [ ] **Step 5: Commit**

```bash
git add tests/test_functional.py tests/functional/01_happy_path_adresar
git commit -m "test: add VCR functional runner and happy-path adresar case"
```

---

## Task 8: Remaining functional test cases

Add the error and sync-action cases from the spec. Cases that don't need live HTTP (config-validation failures) need no cassette; cases that do (partial failure, sync actions) record against the sandbox via `generate-vcr-tests`.

**Files (one directory per case under `tests/functional/`):**
- `02_partial_failure/` — input with one invalid row; expect exit 0, `write_errors` has 1 row. (Record cassette.)
- `03_fail_on_error_true/` — same bad input + `fail_on_error: true`; expect exit 1. (Record cassette.)
- `04_missing_password/` — config without `#password`; expect exit 1. (No cassette.)
- `05_missing_id_column/` — `id_column` set to a column absent from the input; expect exit 1. (No cassette.)
- `06_test_connection/` — `action: testConnection`; expect sync-action success, exit 0. (Record cassette.)
- `07_list_evidences/` — `action: listEvidences`; expect a JSON list of options. (Record cassette.)

- [ ] **Step 1: Create the no-cassette validation cases**

Create `tests/functional/04_missing_password/source/data/config.json`:

```json
{
  "action": "run",
  "parameters": {
    "base_url": "https://demo.flexibee.eu",
    "company": "demo",
    "username": "winstrom",
    "ssl_verify": true,
    "evidence": "adresar",
    "id_column": "kod"
  }
}
```

Create `tests/functional/04_missing_password/source/data/cassettes/expected_status.json`:

```json
{
  "exit_code": 1
}
```

Create `tests/functional/05_missing_id_column/source/data/in/tables/adresar.csv`:

```csv
kod,nazev
EXT001,Acme s.r.o.
```

Create `tests/functional/05_missing_id_column/source/data/config.json`:

```json
{
  "action": "run",
  "parameters": {
    "base_url": "https://demo.flexibee.eu",
    "company": "demo",
    "username": "winstrom",
    "#password": "winstrom",
    "ssl_verify": true,
    "evidence": "adresar",
    "id_column": "nonexistent_column",
    "id_type": "ext"
  }
}
```

Create `tests/functional/05_missing_id_column/source/data/cassettes/expected_status.json`:

```json
{
  "exit_code": 1
}
```

(Add the matching `storage.input.tables` mapping to case 05's `config.json` exactly as in case 01.)

- [ ] **Step 2: Run the no-cassette cases to verify they fail with exit 1**

Run: `uv run pytest tests/test_functional.py -v -k "04_missing_password or 05_missing_id_column"`
Expected: PASS — both cases assert exit code 1 and the component raises `UserException` before any HTTP call.

- [ ] **Step 3: Record the live cases via generate-vcr-tests**

For `02_partial_failure`, `03_fail_on_error_true`, `06_test_connection`, `07_list_evidences`: create each `config.json` (mirror case 01 for run cases; set `action` to `testConnection`/`listEvidences` for the sync cases) and the input CSVs, then invoke `component-developer:generate-vcr-tests` to record cassettes against `secrets.json`.

For `02`/`03`, the input CSV must contain a row FlexiBee will reject (e.g. a duplicate `ic` or an invalid `stat` reference) so the API returns a per-record error.

Create `tests/functional/03_fail_on_error_true/source/data/config.json` with `"fail_on_error": true` and `tests/functional/03_fail_on_error_true/source/data/cassettes/expected_status.json` = `{"exit_code": 1}`.

- [ ] **Step 4: Verify no secrets leaked into any cassette**

Run: `grep -ri "winstrom:winstrom\|Authorization\|Basic " tests/functional/*/source/data/cassettes/ || echo "CLEAN"`
Expected: `CLEAN`. If anything prints, the sanitizer config is wrong — fix `VCR_SANITIZERS` before committing.

- [ ] **Step 5: Run the full functional suite**

Run: `uv run pytest tests/test_functional.py -v`
Expected: PASS — all 7 cases green.

- [ ] **Step 6: Commit**

```bash
git add tests/functional
git commit -m "test: add error, fail-on-error, and sync-action functional cases"
```

---

## Task 9: Configuration schemas (UI) — hand off to component-build-ui

The actual `configSchema.json` and `configRowSchema.json` are built by the UI specialist, not hand-written here. This task is a structured hand-off; do **not** write the JSON yourself.

**Files (built by the skill):**
- Modify: `component_config/configSchema.json`
- Modify: `component_config/configRowSchema.json`

- [ ] **Step 1: Invoke component-build-ui with these field specs**

Invoke `component-developer:component-build-ui`. Root schema (`configSchema.json`) fields:

| Field | Type | Required | Secret | Default | UI notes |
|-------|------|----------|--------|---------|----------|
| `base_url` | string | ✓ | | | "ABRA Flexi instance URL, e.g. https://demo.flexibee.eu" |
| `company` | string | ✓ | | | "Company identifier (firma) from the URL path" |
| `username` | string | ✓ | | | Basic-auth username |
| `#password` | string | ✓ | ✓ | | Basic-auth password (encrypted) |
| `ssl_verify` | boolean | | | `true` | "Verify TLS certificate (disable only for self-signed on-prem certs)" |
| `debug` | boolean | | | `false` | Verbose logging |

Add a **`testConnection`** sync-action button bound to the root schema.

Row schema (`configRowSchema.json`) fields:

| Field | Type | Required | Default | UI notes |
|-------|------|----------|---------|----------|
| `evidence` | string (enum via sync action) | ✓ | | Dropdown populated by the **`listEvidences`** sync action |
| `id_column` | string | ✓ | | "Input column whose value identifies the record" |
| `id_type` | enum (`ext`,`internal`) | | `ext` | "ext = upsert on external ID; internal = update by FlexiBee numeric ID" |
| `batch_size` | integer | | `100` | min 1, max 500 |
| `fail_on_error` | boolean | | `false` | "Abort the job on the first record error instead of routing to write_errors" |

The `evidence` dropdown must declare its dependency on the `listEvidences` sync action (which needs the root connection params).

- [ ] **Step 2: Validate the schemas with the schema-tester**

Per the `component-build-ui` skill, run its schema test/Playwright validation. Expected: both schemas parse, the `evidence` dropdown wires to `listEvidences`, and `testConnection` renders.

- [ ] **Step 3: Commit**

```bash
git add component_config/configSchema.json component_config/configRowSchema.json
git commit -m "feat: add root and row configuration schemas"
```

---

## Task 10: Documentation

Fill in the component descriptions and README so the component is self-documenting and portal-ready.

**Files:**
- Modify: `README.md`
- Modify: `component_config/component_short_description.md`
- Modify: `component_config/component_long_description.md`
- Modify: `component_config/configuration_description.md`

- [ ] **Step 1: Write component_config/component_short_description.md**

```markdown
Writes data from Keboola Storage tables into the ABRA Flexi (FlexiBee) ERP system via its REST API.
```

- [ ] **Step 2: Write component_config/component_long_description.md**

```markdown
# ABRA Flexi Writer

Writes rows from a Keboola Storage input table into an ABRA Flexi (formerly FlexiBee) evidence
type (e.g. `adresar`, `faktura-vydana`) via the FlexiBee REST API.

Each configuration row targets one evidence type. Records are upserted in batches: the value of a
chosen ID column becomes the FlexiBee record identifier — either an external ID (`ext:`) for
idempotent upserts, or an internal numeric ID for updating existing records. Records that the API
rejects are written to a `write_errors` output table (id, error, field, code) so you can review and
re-run them; set **Fail on error** to abort the whole job on the first failure instead.

## Authentication

HTTP Basic authentication. The configured user must have REST API write access in ABRA Flexi.

## Configuration

- **Connection** (per configuration): instance URL, company identifier, username, password.
- **Per evidence row**: evidence type, ID column, ID type, batch size, fail-on-error.
```

- [ ] **Step 3: Write component_config/configuration_description.md**

```markdown
## Connection

- **Instance URL** – base URL of the ABRA Flexi instance (e.g. `https://demo.flexibee.eu`).
- **Company** – company identifier (firma) as it appears in the API path.
- **Username / Password** – HTTP Basic credentials with REST API write access.
- **Verify TLS** – disable only for self-signed certificates on on-premises installs.

Use **Test Connection** to validate the credentials.

## Evidence row

- **Evidence** – the ABRA Flexi evidence type to write into (pick from the dropdown).
- **ID column** – the input-table column whose value identifies each record.
- **ID type** – `ext` upserts on an external ID; `internal` updates by FlexiBee numeric ID.
- **Batch size** – records sent per API request (1–500).
- **Fail on error** – abort on the first rejected record instead of collecting failures in the
  `write_errors` table.
```

- [ ] **Step 4: Update README.md**

Replace the cookiecutter boilerplate README body with a short description (reuse the long description above), a configuration section, a "Development" section with the test commands (`uv run pytest`, `uv run ruff check src tests`), and a link to the FlexiBee API docs (https://www.flexibee.eu/api/). Keep any existing license/footer the template provided.

- [ ] **Step 5: Commit**

```bash
git add README.md component_config/*.md
git commit -m "docs: write component descriptions and README"
```

---

## Task 11: End-to-end validation in the CF test project — hand off to kbagent

Deploy and run the component against the CF test project to confirm a real end-to-end write. This is operational and credential-bound; drive it through the kbagent skill.

- [ ] **Step 1: Invoke kbagent to register and run**

Invoke `kbagent:kbagent` (or `/kbagent:keboola`) to:
1. Register `keboola.wr-abra-flexi` in the CF test project (writer type).
2. Create a root config with the sandbox connection (from `secrets.json` — do not echo values).
3. Add a row for the `adresar` evidence (`id_column=kod`, `id_type=ext`, small `batch_size`).
4. Upload a small input CSV and run the job.

- [ ] **Step 2: Verify the run**

Confirm via kbagent / Keboola MCP:
- Job exit code 0.
- `write_errors` table exists in Storage (0 rows for clean input).
- Logs show `created=N, updated=M, failed=0`.

- [ ] **Step 3: Record the outcome**

Note the config ID and job ID in the PR description when the work is opened for review. (Per the user's standing instruction, keep customer names out of public artifacts — this is the CF test project, so that's fine.)

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-06-01-wr-abra-flexi-design.md`):
- §2 Keboola mapping (config rows, write_errors, secrets) → Tasks 2, 5, 9. ✅
- §3 Auth (HTTP Basic) → Task 4 client + Task 9 schema. ✅
- §4 Data model (winstrom wrapper, batch, ID handling, error body via post_raw) → Tasks 3, 4. ✅
- §5 Config + sync actions (testConnection, listEvidences) → Tasks 2, 5, 9. ✅
- §6 Code architecture (client separate, thin run(), exit codes) → Tasks 4, 5. ✅
- §7 Testing (8 datadir cases, VCR, sanitizers, sync-action tests) → Tasks 7, 8. ✅
- §8 Deployment (kbagent CF test project) → Task 11. ✅
- §9 Risks (license/write-access 403, self-signed TLS, rate limits) → handled in client error message (Task 4), `ssl_verify` (Tasks 2/9), retry list (Task 4). ✅

**Note on §7 test case numbering:** the spec listed `06_internal_id_mode` and separate sync-action cases; this plan folds internal-ID behaviour into the helper unit tests (Task 3, `test_build_record_internal_*`) which is cheaper and more precise than a full datadir run, and keeps two dedicated sync-action functional cases (06, 07). This is a deliberate, stated simplification — not a gap.

**Placeholder scan:** No TBD/TODO. The only deliberately-delegated steps are credential-bound recording (Task 7/8 → `generate-vcr-tests`), UI JSON (Task 9 → `component-build-ui`), and deployment (Task 11 → `kbagent`) — each names the exact skill, fields, and expected outcome rather than leaving it vague.

**Type consistency:** `WriteResult(created, updated, failed, failed_records)` is defined in Task 4 and consumed identically in Task 5. `build_winstrom_record(row, id_column, id_type)` and `chunked(rows, size)` signatures match between Task 3 (definition + tests) and Task 5 (the final `component.py` repeats them verbatim). Failed-record dict keys (`id`, `error`, `field`, `code`) match `_ERROR_COLUMNS` in Task 5. `FlexiBeeClientError` / `FlexiBeeWriterClient` names consistent across Tasks 4, 5.
