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
                f"Unexpected non-JSON response (HTTP {response.status_code}) from '{evidence}': {response.text[:500]}"
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
            data = self._http.get(endpoint_path=endpoint, verify=self.ssl_verify, timeout=self._HTTP_TIMEOUT)
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
