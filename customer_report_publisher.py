"""Owner-side client report publishing for the private local CRM."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config_loader import env

DEFAULT_PUBLISH_URL = "https://straight-up-digital-site.netlify.app/.netlify/functions/publish-audit-report"
_ALLOWED_REPORT_HOSTS = {
    "straight-up-digital-site.netlify.app",
    "straightupdigital.co.za",
    "www.straightupdigital.co.za",
}


def publish_customer_report(report: dict, token: str | None = None, endpoint: str | None = None) -> dict:
    """Publish a customer-safe report. The caller must obtain owner review first."""
    secret = (token if token is not None else env("AUDIT_REPORT_PUBLISH_TOKEN")).strip()
    if not secret:
        raise RuntimeError("Report publishing is not configured. Add AUDIT_REPORT_PUBLISH_TOKEN to the private local .env file after configuring it in Netlify.")

    target = (endpoint or env("AUDIT_REPORT_PUBLISH_URL", DEFAULT_PUBLISH_URL)).strip()
    parsed = urlparse(target)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in _ALLOWED_REPORT_HOSTS:
        raise RuntimeError("The report publish endpoint must use the approved Straight Up Digital HTTPS site.")

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
        raise RuntimeError(f"The report service returned HTTP {exc.code}; check the private Netlify publish settings.") from None
    except URLError as exc:
        raise RuntimeError("The report service could not be reached. Check the internet connection and Netlify deployment.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("The report service returned an unreadable response.") from None

    report_url = result.get("url") if isinstance(result, dict) else None
    report_parts = urlparse(str(report_url or ""))
    if (
        report_parts.scheme != "https"
        or report_parts.hostname not in _ALLOWED_REPORT_HOSTS
        or not report_parts.path.startswith("/report/")
    ):
        raise RuntimeError("The report service did not return a valid secure client report link.")
    return {"url": report_url, "expiresAt": str(result.get("expiresAt") or "")}
