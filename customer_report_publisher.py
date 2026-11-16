"""Owner-side client report publishing for the private local CRM."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config_loader import env

DEFAULT_PUBLISH_URL = "https://straight-up-digital-site.netlify.app/.netlify/functions/publish-audit-report"
_DEFAULT_REPORT_HOSTS = {
    "straight-up-digital-site.netlify.app",
    "straightupdigital.co.za",
    "www.straightupdigital.co.za",
}
_PUBLISH_PATHS = {"/.netlify/functions/publish-audit-report", "/api/publish-audit-report"}
_REPORT_PATH = re.compile(r"^/report/[A-Za-z0-9_-]{40,60}$")


def _allowed_report_hosts() -> set[str]:
    hosts = set(_DEFAULT_REPORT_HOSTS)
    raw = str(env("CLOUDFLARE_WORKER_HOST", "") or "").strip().lower()
    if raw:
        parsed = urlparse(f"https://{raw}")
        try:
            port = parsed.port
        except ValueError:
            port = -1
        labels = raw.split(".")
        valid_labels = all(label and len(label) <= 63 and label[0] != "-" and label[-1] != "-"
                           and all(char.isascii() and (char.isalnum() or char == "-") for char in label)
                           for label in labels)
        if (not raw.endswith(".workers.dev") or len(labels) < 4 or not parsed.hostname
                or parsed.hostname != raw or parsed.netloc != raw or port is not None
                or parsed.path or parsed.query or parsed.fragment or len(raw) > 253 or not valid_labels):
            raise ValueError("CLOUDFLARE_WORKER_HOST must be one exact workers.dev hostname without a scheme, port, or path")
        hosts.add(parsed.hostname)
    return hosts


def _approved_url(value: str, *, paths: set[str] | None = None, report_link: bool = False) -> bool:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https" and parsed.hostname in _allowed_report_hosts()
        and not parsed.username and not parsed.password and port is None
        and not parsed.query and not parsed.fragment
        and (paths is None or parsed.path in paths)
        and (not report_link or bool(_REPORT_PATH.fullmatch(parsed.path)))
    )


def publish_customer_report(report: dict, token: str | None = None, endpoint: str | None = None) -> dict:
    """Publish a customer-safe report. The caller must obtain owner review first."""
    target = (endpoint or env("CLOUDFLARE_AUDIT_REPORT_PUBLISH_URL", "")
              or env("AUDIT_REPORT_PUBLISH_URL", DEFAULT_PUBLISH_URL)).strip()
    worker_host = str(env("CLOUDFLARE_WORKER_HOST", "") or "").strip().lower()
    token_name = "CLOUDFLARE_AUDIT_REPORT_PUBLISH_TOKEN" if worker_host and urlparse(target).hostname == worker_host else "AUDIT_REPORT_PUBLISH_TOKEN"
    secret = (token if token is not None else env(token_name, "")).strip()
    if not secret:
        raise RuntimeError(f"Report publishing is not configured. Add {token_name} to the private local .env file after configuring it in the production site's secret store.")

    if not _approved_url(target, paths=_PUBLISH_PATHS):
        raise RuntimeError("The report publish endpoint must use an approved Straight Up Digital HTTPS route.")

    body = json.dumps({"report": report}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        target,
        data=body,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"The report service returned HTTP {response.status}.")
            result = json.loads(response.read(16_384).decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"The report service returned HTTP {exc.code}; check the private production publish settings.") from None
    except URLError as exc:
        raise RuntimeError("The report service could not be reached. Check the internet connection and production deployment.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("The report service returned an unreadable response.") from None

    report_url = result.get("url") if isinstance(result, dict) else None
    if not _approved_url(str(report_url or ""), report_link=True):
        raise RuntimeError("The report service did not return a valid secure client report link.")
    return {"url": report_url, "expiresAt": str(result.get("expiresAt") or "")}


def delete_published_report(report_url: str, token: str | None = None, endpoint: str | None = None) -> None:
    """Revoke one owned report link before its CRM contact is erased."""
    target = (endpoint or env("CLOUDFLARE_AUDIT_REPORT_PUBLISH_URL", "")
              or env("AUDIT_REPORT_PUBLISH_URL", DEFAULT_PUBLISH_URL)).strip()
    worker_host = str(env("CLOUDFLARE_WORKER_HOST", "") or "").strip().lower()
    token_name = "CLOUDFLARE_AUDIT_REPORT_PUBLISH_TOKEN" if worker_host and urlparse(target).hostname == worker_host else "AUDIT_REPORT_PUBLISH_TOKEN"
    secret = (token if token is not None else env(token_name, "")).strip()
    if not secret:
        raise RuntimeError(f"Cannot erase this record safely until {token_name} is configured locally to revoke its online report.")
    if not _approved_url(target, paths=_PUBLISH_PATHS):
        raise RuntimeError("The report revocation endpoint is not an approved Straight Up Digital HTTPS address.")
    if not _approved_url(str(report_url or ""), report_link=True):
        raise ValueError("The report link is not an approved Straight Up Digital report.")
    request = Request(
        target,
        data=json.dumps({"reportUrl": report_url}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json", "Accept": "application/json"},
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"The report service returned HTTP {response.status}.")
    except HTTPError as exc:
        raise RuntimeError(f"Could not revoke the online report (HTTP {exc.code}); no local erasure was made.") from None
    except URLError:
        raise RuntimeError("Could not reach the report service; no local erasure was made.") from None
