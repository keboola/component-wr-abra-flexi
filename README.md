# ABRA Flexi Writer

Writes rows from a Keboola Storage input table into an ABRA Flexi (formerly FlexiBee) evidence type (e.g. `adresar`, `faktura-vydana`) via the FlexiBee REST API.

Each configuration row targets one evidence type. Records are upserted in batches: the value of a chosen ID column becomes the FlexiBee record identifier — either an external ID (`ext:`) for idempotent upserts, or an internal numeric ID for updating existing records. Records that the API rejects are written to a `write_errors` output table (id, error, field, code) so you can review and re-run them; set **Fail on error** to abort the whole job on the first failure instead.

## Configuration

### Connection (per configuration)

- **Instance URL** – base URL of the ABRA Flexi instance (e.g. `https://demo.flexibee.eu`).
- **Company** – company identifier (firma) as it appears in the API path.
- **Username / Password** – HTTP Basic credentials with REST API write access.
- **Verify TLS** – disable only for self-signed certificates on on-premises installs.

Use **Test Connection** to validate the credentials.

### Evidence row

- **Evidence** – the ABRA Flexi evidence type to write into (pick from the dropdown).
- **ID column** – the input-table column whose value identifies each record.
- **ID type** – `ext` upserts on an external ID; `internal` updates by FlexiBee numeric ID.
- **Batch size** – records sent per API request (1–500).
- **Fail on error** – abort on the first rejected record instead of collecting failures in the `write_errors` table.

## Development

Clone this repository and run tests and linting with the following commands:

```bash
uv run pytest                        # run all tests
uv run ruff check src tests          # lint
uv run ruff format src tests         # format
```

For API reference and documentation, see the [FlexiBee API docs](https://www.flexibee.eu/api/).

## Integration

For details about deployment and integration with Keboola, refer to the
[deployment section of the developer
documentation](https://developers.keboola.com/extend/component/deployment/).
