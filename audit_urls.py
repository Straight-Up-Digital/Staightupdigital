"""Canonical URL helpers shared by crawling and audit source reporting."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


_INDEX_FILES = re.compile(r"/(?:index\.(?:html?|php)|default\.(?:html?|aspx?))$", re.IGNORECASE)
_TRACKING_KEYS = {"fbclid", "gclid", "gbraid", "wbraid", "msclkid", "mc_cid", "mc_eid"}


def _parts(url: str) -> tuple[str, str, str, str] | None:
    value = str(url or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
        host = f"{host}:{port}"
    path = _INDEX_FILES.sub("", parts.path or "/")
    path = path.rstrip("/") or "/"
    query: list[tuple[str, str]] = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_KEYS:
            continue
        query.append((key, val))
    query.sort()
    return scheme, host, path, urlencode(query, doseq=True)


def normalize_url(url: str) -> str:
    """Normalize URL spelling while retaining its scheme and host for safe fetching."""
    parts = _parts(url)
    if not parts:
        return str(url or "").strip()
    scheme, host, path, query = parts
    return urlunsplit((scheme, host, path, query, ""))


def safe_display_url(url: str) -> str:
    """Display a redirect URL without credentials, query values, or fragments."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = parts.hostname or ""
        if not host:
            return ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parts.port
        if port and not (
            parts.scheme.lower() == "http" and port == 80
            or parts.scheme.lower() == "https" and port == 443
        ):
            host = f"{host}:{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path, "", ""))
    except ValueError:
        return ""


def canonical_key(url: str) -> str:
    """Return a scheme- and www-independent key for deduplicating the same page."""
    normalized = _parts(url)
    if not normalized:
        return str(url or "").strip().casefold()
    _, host, path, query = normalized
    if host.startswith("www."):
        host = host[4:]
    return host + path + (f"?{query}" if query else "")


def canonical_page_url(page_url: str, canonical_href: str = "") -> str:
    """Use a same-host rel=canonical URL when valid; otherwise use the fetched URL."""
    normalized_page = normalize_url(page_url)
    if not canonical_href:
        return normalized_page
    candidate = normalize_url(urljoin(normalized_page, str(canonical_href).strip()))
    page_parts = _parts(normalized_page)
    candidate_parts = _parts(candidate)
    if not page_parts or not candidate_parts:
        return normalized_page
    page_host = page_parts[1].removeprefix("www.")
    candidate_host = candidate_parts[1].removeprefix("www.")
    return candidate if page_host == candidate_host else normalized_page


def dedupe_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = []
    seen: set[str] = set()
    for page in pages:
        key = canonical_key(page.get("canonical_url") or page.get("url") or "")
        if key and key not in seen:
            seen.add(key)
            unique.append(page)
    return unique


def dedupe_urls(urls: list[str]) -> list[str]:
    unique = []
    seen: set[str] = set()
    for url in urls:
        normalized = normalize_url(url)
        key = canonical_key(normalized)
        if key and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique
