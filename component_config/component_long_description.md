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
