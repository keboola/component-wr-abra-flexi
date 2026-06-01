# wr-abra-flexi — Design Spec

> Type: writer
> Component ID: keboola.wr-abra-flexi
> Status: draft
> Date: 2026-06-01

## 1. Overview & source system

A Keboola writer that pushes data from Keboola Storage input tables into the ABRA Flexi (formerly
FlexiBee) Czech ERP/accounting system via its REST API. Each config row writes one evidence type
(e.g. `adresar`, `faktura-vydana`) in configurable batches, with per-record error tracking in an
output table.

- **Target system:** ABRA Flexi (FlexiBee) — Czech ERP/accounting SaaS/on-premises.
- **API docs:** https://www.flexibee.eu/api/ and https://demo.flexibee.eu/devdoc/
- **Primary use case:** Synchronising contacts, invoices, and orders from Keboola transformations
  back into an ABRA Flexi installation.

## 2. Keboola mapping

### Input → API writes
Each config row maps one Keboola input table to one FlexiBee evidence type. The component reads
the input table column-by-column and sends each column name as a FlexiBee field name. No schema
translation is performed — the user is responsible for naming columns correctly (they can verify
field names via the ABRA Flexi UI or documentation). Columns named `id`, or the configured
`id_column`, are handled specially (see §5).

### Config rows
**Config rows are used** — one row per evidence type (e.g. one row for `adresar`, one for
`faktura-vydana`). This follows the Keboola convention for writers that handle multiple independent
objects: each row can be enabled/disabled, run, and retried independently. Auth lives at root level;
evidence-specific options live at row level (same split as sibling `ex-flexibee`).

### Incremental / state
This is a **writer** — there is no incremental cursor or `state.json` for reads. The writer posts
whatever rows are present in the input table on each run. Idempotency is guaranteed via the FlexiBee
external ID mechanism: the user designates an `id_column` (mapped to `ext:VALUE` or internal ID).
FlexiBee upserts on that ID.

### Secrets
`#password` at root config level, encrypted by the platform per Keboola convention.

### Sync actions
- **`test-connection`** — call `GET /c/{company}/evidence-list.json`; succeeds if HTTP 200 returned.
- **`listEvidences`** — return `[(evidencePath, evidenceName), ...]` list for the `evidence` dropdown
  in the row schema. Reuses the same call as `ex-flexibee`.

### Output bucket / table naming
The component produces **one output table per row**: `write_errors` (via default bucket, table name
`write_errors`). This table is written with `write_always: true` so it appears even on failure.
If there are no errors, the table is empty (but still written). Standard default-bucket naming applies.

## 3. Authentication & connection

**Chosen: HTTP Basic Auth** over HTTPS. The API also supports session-token auth (POST to
`/login-logout/login.json`), but HTTP Basic is stateless, simpler for headless M2M use, and is what
the sibling `ex-flexibee` extractor uses — consistent DX for customers who have both.

**Connection surface:** REST JSON API. All requests use `Content-Type: application/json` and
`Accept: application/json`.

**Base URL pattern:** `https://{base_url}/c/{company}/{evidence}.json`

**Provisioning (what the user/admin must do):**
1. Log into ABRA Flexi as admin.
2. Create a dedicated API user (or use an existing user with write permissions to the target evidence types).
3. Ensure the user's license tier includes "REST API write access" (required for PUT/POST).
4. Copy the username and password into the Keboola root config.
No OAuth app registration or token minting is required — Basic Auth is headless.

**Sandbox:** A public demo instance is available at `demo.flexibee.eu` (credentials `winstrom:winstrom`,
company `demo`). Sandbox credentials are in `secrets.json` (Keboola-format `parameters` block with
`#password`). The demo instance supports write operations.

**Blockers / access:** None. The sandbox is accessible immediately; no admin gate blocks development.

## 4. Data model & endpoints

### Endpoints in scope for v1

**Generic write (all evidence types):**
```
POST https://{base_url}/c/{company}/{evidence}.json
Content-Type: application/json

{
  "winstrom": {
    "{evidence}": [
      {"id": "ext:EXTERNAL_KEY", "field1": "value", "field2": "value", ...},
      ...
    ]
  }
}
```

Multiple records per request (batch). The API returns a `winstrom` response with per-record
`results[]` containing `id`, `success/error`, `for` (field name), and `code` fields.

**Test-connection / evidence list:**
```
GET https://{base_url}/c/{company}/evidence-list.json
```

### What is deferred
- DELETE operations (full-sync with removals of absent records)
- ABRA Flexi Actions (locking, reversing invoices) — these are not upsert writes
- Attachment/file upload

### Pagination
Not applicable for writes. For the `listEvidences` sync action, the evidence list is small
(< 200 entries) and returned in a single response — no pagination required.

### Rate limits
No documented rate limits. The component uses `batch_size` (configurable, default 100) to
control request volume. On HTTP 429 or 503 responses, the component will sleep 5 s and retry
up to 3 times (exponential backoff via the `HttpClient` retry wrapper from `ex-flexibee`).

### Response shape
```json
{
  "winstrom": {
    "success": true,
    "stats": {"created": 3, "updated": 2, "deleted": 0, "skipped": 0, "failed": 1},
    "results": [
      {"id": 105, "success": false, "error": "...", "for": "ic", "code": "UNIQUE_CONSTRAINT"},
      {"id": 106, "success": true}
    ]
  }
}
```
The component iterates `results[]` to segregate successes from failures.

### ID handling (configurable)
The row config contains:
- `id_column` — name of the input table column to use as the record identifier.
- `id_type` — `ext` (default) or `internal`.
  - `ext`: the column value is prefixed with `ext:` and sent as `"id": "ext:{value}"`. FlexiBee
    upserts on external ID.
  - `internal`: sent as `"id": {value}` (numeric). Only works for updating existing records;
    creating new records requires omitting the `id` field.

## 5. Configuration & schema

### Root config (configSchema.json) — connection / auth
| Field | Type | Required | Secret | Default | Notes |
|-------|------|----------|--------|---------|-------|
| `base_url` | string | ✓ | | | e.g. `https://demo.flexibee.eu` (no trailing slash, no port) |
| `company` | string | ✓ | | | Company identifier in the URL path, e.g. `demo` |
| `username` | string | ✓ | | | HTTP Basic username |
| `#password` | string | ✓ | ✓ | | HTTP Basic password |
| `ssl_verify` | boolean | | | `true` | Set false for self-signed certs on on-premises installs |
| `debug` | boolean | | | `false` | Enables verbose logging |

### Row config (configRowSchema.json) — per-evidence write settings
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `evidence` | string | ✓ | | FlexiBee evidence path (e.g. `adresar`). Populated via `listEvidences` sync-action dropdown |
| `id_column` | string | ✓ | | Name of the input table column to use as the record ID |
| `id_type` | enum | | `ext` | `ext` or `internal` |
| `batch_size` | integer | | `100` | Records per API request (1–500) |
| `fail_on_error` | boolean | | `false` | If true, raise UserException on first record failure |

### Sync actions
Both actions live at **root config level** (they need connection params, not row params):

**`test-connection`:**
- Makes `GET /c/{company}/evidence-list.json` with the configured credentials.
- Returns `{"status": "success"}` on HTTP 200; raises `UserException` on 401/403/404.

**`listEvidences`:**
- Same call as test-connection.
- Returns `[{"value": evidencePath, "label": evidenceName}, ...]` for the UI dropdown.
- This is used by the `evidence` field in the row schema.

**Handoff:** `component-build-ui` should build `configSchema.json` and `configRowSchema.json` from
the field tables above, wiring up the `listEvidences` dependency on the `evidence` dropdown in the
row schema.

## 6. Code architecture

### Module layout
```
src/
  component.py          # thin orchestrator — run() only
  configuration.py      # Pydantic models: RootConfig, RowConfig
  client/
    flexibee_client.py  # FlexiBeeClient: all HTTP logic
    __init__.py
```

The `FlexiBeeClient` from `ex-flexibee` is the reference implementation. The writer's client extends
or mirrors it — sharing the `HttpClient` setup (Basic auth, retries, timeouts) and the
winstrom-unwrap pattern.

### `FlexiBeeWriterClient` key methods
- `__init__(base_url, company, username, password, ssl_verify)` — mirrors extractor
- `test_connection()` → calls `list_evidences()`
- `list_evidences()` → `GET /c/{company}/evidence-list.json`, returns `[(path, name)]`
- `write_records(evidence, records: list[dict]) -> WriteResult` — POSTs one batch; returns
  `WriteResult(created, updated, failed, results)`
- `_build_url(evidence)` → `{base_url}/c/{company}/{evidence}.json`
- `_wrap_request(evidence, records)` → builds the `{"winstrom": {evidence: [...]}}` body
- `_unwrap_response(body)` → extracts `winstrom.results[]` and `winstrom.stats`

### `component.py` — `run()` flow
```
1. Parse RootConfig + RowConfig (Pydantic, raises UserException on bad config)
2. Instantiate FlexiBeeWriterClient
3. Get input table definition (self.get_input_tables_definitions()[0])
4. Open CSV reader; iterate rows in batches of batch_size
5. For each batch:
   a. Apply ID column transformation (prefix ext: or pass numeric)
   b. Remove the id_column from the field dict (it becomes the "id" key, not a field)
   c. Call client.write_records(evidence, batch)
   d. Collect failed results into error_rows list
   e. If fail_on_error and any failures → raise UserException with summary
6. Write error_rows to data/out/tables/write_errors.csv + manifest (write_always=True)
7. Log summary: created X, updated Y, failed Z
```

### Error handling
| Condition | Exception | Exit code |
|-----------|-----------|-----------|
| Missing/invalid config params | `UserException` | 1 |
| Auth failure (401/403) | `UserException` | 1 |
| Host unreachable / DNS failure | `UserException` | 1 |
| Per-record API error (fail_on_error=true) | `UserException` | 1 |
| Per-record API error (fail_on_error=false) | Logged; written to error table | 0 |
| Unexpected exception | re-raise (exit 2) | 2 |

### Key dependencies
- `keboola.component` — base class, CommonInterface, UserException
- `keboola.http_client` — HttpClient with retry/timeout, same as `ex-flexibee`
- `pydantic` — config validation (already in cookiecutter template)
- `requests` — via HttpClient (no direct import needed)

## 7. Testing

### Datadir test cases
| Test | Input | Expected |
|------|-------|----------|
| `01_happy_path_adresar` | CSV with valid adresar columns, ext IDs | Exit 0, write_errors table empty |
| `02_partial_failure` | CSV with one invalid row (missing required field) | Exit 0, write_errors has 1 row |
| `03_fail_on_error_true` | CSV with one invalid row + fail_on_error=true | Exit 1 |
| `04_auth_failure` | Wrong password in config | Exit 1 |
| `05_missing_id_column` | id_column set to "nonexistent_col" | Exit 1 |
| `06_internal_id_mode` | id_type=internal with numeric id column | Exit 0, records updated |
| `07_sync_action_test_connection` | action=test-connection | Sync action response JSON, exit 0 |
| `08_sync_action_list_evidences` | action=listEvidences | JSON list of evidence options |

### VCR strategy
- Use `keboola.datadirtest` with pytest VCR cassettes.
- Record against `demo.flexibee.eu` (credentials from `secrets.json` — sanitize `#password` in cassette).
- Key cassettes to record:
  - `evidence-list.json` GET (for test-connection + listEvidences)
  - `adresar.json` POST with a 3-record batch (happy path)
  - `adresar.json` POST returning one failed record (partial failure)
  - `adresar.json` POST returning 401 (auth failure mock)
- Sanitize: replace `Authorization: Basic ...` header with a fixed safe value in cassette.
- Sandbox credentials available in `secrets.json` (Keboola-format `parameters` block).

### Sync action tests
- `test-connection` datadir tests (cases 07 above) validate that the correct JSON response shape is
  returned and that the exit code is correct on auth failure.

## 8. Deployment & validation (CF test project)

### kbagent steps
```bash
# 1. Register the component in the CF test project
kbagent create-component --component-id keboola.wr-abra-flexi --type writer

# 2. Create a root config with demo credentials
kbagent create-config keboola.wr-abra-flexi \
  --name "ABRA Flexi Demo" \
  --params '{"base_url":"https://demo.flexibee.eu","company":"demo","username":"winstrom","#password":"winstrom"}'

# 3. Add a row for adresar evidence
kbagent add-config-row keboola.wr-abra-flexi <config_id> \
  --name "Address book" \
  --params '{"evidence":"adresar","id_column":"kod","id_type":"ext","batch_size":50}'

# 4. Upload a small test CSV to the input table
# 5. Run the job and verify:
#    - Job exits 0
#    - write_errors table exists and is empty
#    - ABRA Flexi demo shows the written records
```

### What a successful end-to-end run looks like
- Job: exit 0
- Output: `write_errors` table written to Storage (0 rows on clean data)
- Logs: `INFO: Created 5, updated 0, failed 0` (or similar)
- ABRA Flexi: records visible in the `adresar` evidence

## 9. Open risks & blockers

| # | Risk | Severity | Owner | Mitigation |
|---|------|----------|-------|------------|
| 1 | Demo instance may have write restrictions on some evidence types | Low | Developer | Test with `adresar` first; it's a basic evidence with no special license requirements |
| 2 | FlexiBee field names are Czech — users may not know the correct API field names | Medium | Product | Provide sync action or link to evidence properties in docs; extractor already handles this pattern |
| 3 | On-premises FlexiBee installations may use self-signed TLS certs | Low | Developer | `ssl_verify: false` config option already in scope |
| 4 | ABRA Flexi "REST API write access" requires a specific license tier | Medium | Customer | Document in README; surface clearly in UserException if 403 is returned with a license message |
| 5 | No documented rate limits — high-volume writes could hit undocumented throttles | Low | Developer | `batch_size` param limits per-request volume; retry on 429/503 already in HttpClient wrapper |
