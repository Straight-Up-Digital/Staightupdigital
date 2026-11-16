from __future__ import annotations

import json

import pytest

import customer_report_publisher as reports
import netlify_crm_sync as enquiries


WORKER_HOST = "straight-up-digital-migration.acct123.workers.dev"
WORKER_REPORT_ENDPOINT = f"https://{WORKER_HOST}/api/publish-audit-report"


def _env(values: dict[str, str]):
    return lambda key, default=None: values.get(key, default)


def test_existing_netlify_hosts_remain_allowed_and_worker_host_is_opt_in(monkeypatch):
    monkeypatch.setattr(enquiries, "env", _env({}))
    assert enquiries._allowed_hosts() == {
        "straight-up-digital-site.netlify.app", "straightupdigital.co.za", "www.straightupdigital.co.za"
    }

    monkeypatch.setattr(enquiries, "env", _env({"CLOUDFLARE_WORKER_HOST": WORKER_HOST}))
    assert WORKER_HOST in enquiries._allowed_hosts()

    for invalid in (
        "https://" + WORKER_HOST,
        WORKER_HOST + "/api",
        "unrelated.example.com",
        "one-label.workers.dev",
        "worker.workers.dev:8443",
    ):
        monkeypatch.setattr(enquiries, "env", _env({"CLOUDFLARE_WORKER_HOST": invalid}))
        with pytest.raises(ValueError, match="workers.dev"):
            enquiries._allowed_hosts()


def test_local_crm_sync_accepts_only_explicit_worker_endpoint_without_network(monkeypatch):
    values = {
        "CLOUDFLARE_CRM_ENQUIRY_SYNC_TOKEN": "synthetic-cloudflare-owner-token",
        "CLOUDFLARE_CRM_ENQUIRY_SYNC_URL": f"https://{WORKER_HOST}/api/private-enquiries",
        "CLOUDFLARE_WORKER_HOST": WORKER_HOST,
    }
    monkeypatch.setattr(enquiries, "env", _env(values))
    monkeypatch.setattr(enquiries, "load_env", lambda: None)
    calls = []

    def fake_request(url, token, method="GET", body=None):
        calls.append((url, token, method, body))
        return {"items": [], "hasMore": False, "nextCursor": None}

    monkeypatch.setattr(enquiries, "_request", fake_request)
    assert enquiries.sync_website_enquiries(db_path="unused-synthetic-path") == {
        "imported": 0, "acknowledged": 0, "failed": 0
    }
    assert calls == [(values["CLOUDFLARE_CRM_ENQUIRY_SYNC_URL"], "synthetic-cloudflare-owner-token", "GET", None)]

    values["CLOUDFLARE_CRM_ENQUIRY_SYNC_URL"] = "https://unrelated.workers.dev/api/private-enquiries"
    with pytest.raises(ValueError, match="approved Straight Up Digital"):
        enquiries.sync_website_enquiries(db_path="unused-synthetic-path")
    assert len(calls) == 1


def test_report_publisher_uses_exact_worker_host_and_preserves_https_paths(monkeypatch):
    values = {
        "CLOUDFLARE_WORKER_HOST": WORKER_HOST,
        "CLOUDFLARE_AUDIT_REPORT_PUBLISH_TOKEN": "synthetic-cloudflare-report-token",
        "CLOUDFLARE_AUDIT_REPORT_PUBLISH_URL": WORKER_REPORT_ENDPOINT,
    }
    monkeypatch.setattr(reports, "env", _env(values))
    report_token = "R" * 43
    report_url = f"https://{WORKER_HOST}/report/{report_token}"

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"url": report_url, "expiresAt": "2026-12-16T12:00:00Z"}).encode()

    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(reports, "urlopen", fake_urlopen)
    result = reports.publish_customer_report({"synthetic": True})
    assert result["url"] == report_url
    assert requests[0][0].full_url == WORKER_REPORT_ENDPOINT
    assert requests[0][0].get_method() == "POST"
    assert requests[0][0].get_header("Authorization") == "Bearer synthetic-cloudflare-report-token"
    assert requests[0][1] == 15

    assert not reports._approved_url(f"https://{WORKER_HOST}/api/publish-audit-report?redirect=evil.example", paths=reports._PUBLISH_PATHS)
    assert not reports._approved_url(f"https://user@{WORKER_HOST}/api/publish-audit-report", paths=reports._PUBLISH_PATHS)
    assert not reports._approved_url(f"https://unrelated.workers.dev/report/{report_token}", report_link=True)


def test_report_revocation_accepts_only_report_urls_from_the_exact_configured_worker(monkeypatch):
    values = {
        "CLOUDFLARE_WORKER_HOST": WORKER_HOST,
        "CLOUDFLARE_AUDIT_REPORT_PUBLISH_TOKEN": "synthetic-cloudflare-report-token",
        "CLOUDFLARE_AUDIT_REPORT_PUBLISH_URL": WORKER_REPORT_ENDPOINT,
    }
    monkeypatch.setattr(reports, "env", _env(values))
    report_url = f"https://{WORKER_HOST}/report/{'T' * 43}"

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    requests = []
    monkeypatch.setattr(reports, "urlopen", lambda request, timeout: requests.append(request) or Response())
    reports.delete_published_report(report_url)
    assert requests[0].get_method() == "DELETE"
    assert json.loads(requests[0].data) == {"reportUrl": report_url}

    with pytest.raises(ValueError, match="approved Straight Up Digital"):
        reports.delete_published_report(f"https://other.workers.dev/report/{'T' * 43}")
    assert len(requests) == 1


def test_netlify_sync_endpoint_and_token_remain_default_when_worker_settings_are_absent(monkeypatch):
    values = {"CRM_ENQUIRY_SYNC_TOKEN": "synthetic-netlify-token"}
    monkeypatch.setattr(enquiries, "env", _env(values))
    monkeypatch.setattr(enquiries, "load_env", lambda: None)
    calls = []
    monkeypatch.setattr(enquiries, "_request", lambda url, token, method="GET", body=None: (
        calls.append((url, token, method)) or {"items": [], "hasMore": False, "nextCursor": None}
    ))
    assert enquiries.sync_website_enquiries(db_path="unused-synthetic-path") == {
        "imported": 0, "acknowledged": 0, "failed": 0
    }
    assert calls == [(enquiries.DEFAULT_URL, "synthetic-netlify-token", "GET")]


def test_netlify_report_publish_url_remains_allowed_without_a_worker_opt_in(monkeypatch):
    monkeypatch.setattr(reports, "env", _env({}))
    assert reports._approved_url(reports.DEFAULT_PUBLISH_URL, paths=reports._PUBLISH_PATHS)
    assert "straight-up-digital-site.netlify.app" in reports._allowed_report_hosts()
    assert "straight-up-digital-site.netlify.app.workers.dev" not in reports._allowed_report_hosts()
