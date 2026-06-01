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
                "results": [{"id": 1}, {"id": 2}],
            }
        },
    )
    result = client.write_records("adresar", [{"id": "ext:A"}, {"id": "ext:B"}])
    assert isinstance(result, WriteResult)
    assert result.created == 2
    assert result.failed == 0
    assert result.failed_records == []


def test_write_records_partial_failure():
    """FlexiBee returns request-id / errors[].message / errors[].messageCode for failures."""
    client = _make_client()
    client._http.post_raw.return_value = _raw_response(
        400,
        {
            "winstrom": {
                "success": "false",
                "stats": {"created": "1", "updated": "0", "failed": "1"},
                "results": [
                    {"id": 123},
                    {
                        "request-id": "ext:B",
                        "errors": [{"message": "ic already exists", "messageCode": "UNIQUE"}],
                    },
                ],
            }
        },
    )
    result = client.write_records("adresar", [{"id": "ext:A"}, {"id": "ext:B"}])
    assert result.created == 1
    assert result.failed == 1
    assert result.failed_records == [{"id": "ext:B", "error": "ic already exists", "field": "", "code": "UNIQUE"}]


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
