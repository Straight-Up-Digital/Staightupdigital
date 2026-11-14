import io
import json
from unittest.mock import patch

import pytest

from customer_report_publisher import DEFAULT_PUBLISH_URL, publish_customer_report


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


def test_publisher_sends_only_client_safe_payload_and_returns_secure_link():
    report = {"schemaVersion": 1, "businessName": "Example", "findings": []}
    token = "private-owner-token"
    expected_url = "https://straight-up-digital-site.netlify.app/report/" + "A" * 43
    with patch("customer_report_publisher.urlopen", return_value=FakeResponse({"url": expected_url, "expiresAt": "2026-12-14T00:00:00Z"})) as open_url:
        result = publish_customer_report(report, token=token)

    request = open_url.call_args.args[0]
    assert request.full_url == DEFAULT_PUBLISH_URL
    assert request.get_header("Authorization") == f"Bearer {token}"
    assert json.loads(request.data) == {"report": report}
    assert result["url"] == expected_url
    assert token not in request.data.decode("utf-8")


def test_publisher_refuses_non_https_or_other_hosts():
    with pytest.raises(RuntimeError, match="approved"):
        publish_customer_report({"schemaVersion": 1}, token="x", endpoint="http://attacker.invalid/publish")


def test_publisher_rejects_unapproved_report_host():
    with patch("customer_report_publisher.urlopen", return_value=FakeResponse({"url": "https://attacker.invalid/report/abc"})):
        with pytest.raises(RuntimeError, match="valid secure"):
            publish_customer_report({"schemaVersion": 1}, token="x")
