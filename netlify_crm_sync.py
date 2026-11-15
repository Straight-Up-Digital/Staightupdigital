"""Sync production website enquiries into the private local CRM.

The local CRM makes all outbound requests. No client data is exposed by a
listener on the local machine. Secrets are read from the user's local env file.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from config_loader import env, load_env
from crm_ops import save_website_enquiry
from crm import DEFAULT_DB

DEFAULT_URL = "https://straight-up-digital-site.netlify.app/api/private-enquiries"


def _request(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "StraightUpDigital-LocalCRM/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = response.read(1_000_000)
            result = json.loads(payload.decode("utf-8"))
            if response.status >= 400:
                raise RuntimeError("Private enquiry sync request was refused")
            return result
    except urllib.error.HTTPError as exc:
        # Do not include body or headers; this path may contain client details.
        raise RuntimeError(f"Website enquiry sync returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach the production enquiry sync endpoint") from None


def sync_website_enquiries(db_path=None, tenant_id=None) -> dict[str, int]:
    load_env()
    token = (env("CRM_ENQUIRY_SYNC_TOKEN", "") or "").strip()
    base_url = (env("CRM_ENQUIRY_SYNC_URL", DEFAULT_URL) or DEFAULT_URL).strip().rstrip("/")
    if not token:
        raise RuntimeError("Local CRM_ENQUIRY_SYNC_TOKEN is not configured")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "straight-up-digital-site.netlify.app", "straightupdigital.co.za", "www.straightupdigital.co.za"
    } or parsed.path.rstrip("/") != "/api/private-enquiries":
        raise ValueError("CRM sync URL must be the approved Straight Up Digital HTTPS endpoint")

    imported = acknowledged = failed = 0
    cursor = ""
    while True:
        query = urllib.parse.urlencode({"cursor": cursor}) if cursor else ""
        page_url = base_url + ("?" + query if query else "")
        response = _request(page_url, token)
        items = response.get("items")
        if not isinstance(items, list):
            raise RuntimeError("Website enquiry sync returned an invalid page")
        acknowledged_ids: list[str] = []
        for item in items:
            try:
                external_id = str(item.get("id") or "")
                fields = item.get("fields")
                if not external_id or not isinstance(fields, dict):
                    raise ValueError("invalid record")
                save_website_enquiry(external_id, fields, str(item.get("receivedAt") or ""),
                                     db_path or DEFAULT_DB, tenant_id)
                acknowledged_ids.append(external_id)
                imported += 1
            except Exception:
                # Leave the blob in place; an owner can retry after reviewing the local issue.
                failed += 1
        if acknowledged_ids:
            ack = _request(base_url, token, "DELETE", {"ids": acknowledged_ids})
            count = int(ack.get("deleted", 0) or 0)
            acknowledged += count
        next_cursor = response.get("nextCursor")
        if not response.get("hasMore") or not next_cursor:
            break
        cursor = str(next_cursor)
    return {"imported": imported, "acknowledged": acknowledged, "failed": failed}
