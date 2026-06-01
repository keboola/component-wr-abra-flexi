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