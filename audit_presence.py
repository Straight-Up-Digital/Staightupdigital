"""Verified and candidate social, travel, and directory profile discovery."""
from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlparse


_PLATFORMS: dict[str, tuple[str, ...]] = {
    "Facebook": ("facebook.com", "fb.com"),
    "Instagram": ("instagram.com",),
    "LinkedIn": ("linkedin.com",),
    "TripAdvisor": ("tripadvisor.com", "tripadvisor.co.za"),
    "Booking.com": ("booking.com",),
    "LekkeSlaap": ("lekkeslaap.co.za",),
    "Airbnb": ("airbnb.com",),
}
_DIRECTORIES = (
    "yellowpages", "snupit", "hotfrog", "cylex", "kompass", "africa2trust",
    "dnb.com", "yellosa", "safindit",
)
_SHARE_PATHS = ("/sharer", "/share", "/intent/", "/dialog/", "/plugins/")


def _host(url: str) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _platform(url: str) -> str:
    host = _host(url)
    if not host:
        return ""
    for platform, domains in _PLATFORMS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    if any(directory in host for directory in _DIRECTORIES):
        return "Directory"
    return ""


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) > 2 and token not in {"the", "and", "services", "company"}
    }


def _phone(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def discover_profiles(
    site_links: list[str] | None,
    search_results: list[dict[str, Any]] | None,
    business_name: str,
    business_phone: str = "",
) -> list[dict[str, Any]]:
    checked_on = date.today().isoformat()
    business_tokens = _tokens(business_name)
    wanted_phone = _phone(business_phone)
    profiles: dict[tuple[str, str], dict[str, Any]] = {}

    def add(url: str, source: str, text: str, verified_by_site: bool = False) -> None:
        platform = _platform(url)
        if not platform:
            return
        path = urlparse(url).path.lower()
        if any(bit in path for bit in _SHARE_PATHS) or path in {"", "/"}:
            return
        observed_tokens = _tokens(text)
        name_match = bool(business_tokens) and business_tokens.issubset(observed_tokens)
        found_phone = _phone(text)
        phone_match = bool(wanted_phone and len(wanted_phone) >= 7 and wanted_phone in found_phone)
        verified = verified_by_site or (name_match and phone_match)
        key = (platform, url.rstrip("/").casefold())
        item = {
            "platform": platform,
            "status": "Verified" if verified else "Candidate, not verified",
            "url": url,
            "source": source,
            "match_reason": (
                "Linked from the audited website."
                if verified_by_site
                else "Business name and phone both matched."
                if verified
                else "Candidate only; confirm ownership before relying on it."
            ),
            "checked_on": checked_on,
        }
        existing = profiles.get(key)
        if existing is None or (verified and existing["status"] != "Verified"):
            profiles[key] = item

    for url in site_links or []:
        add(str(url), "Website link", "", verified_by_site=True)
    for result in search_results or []:
        url = str(result.get("url") or result.get("link") or "").strip()
        if not url:
            continue
        text = " ".join(
            str(result.get(key) or "")
            for key in ("title", "snippet", "description", "phone")
        )
        add(url, str(result.get("provider") or "Search result"), text)
    return sorted(
        profiles.values(),
        key=lambda profile: (
            profile["platform"].casefold(),
            profile["status"] != "Verified",
            profile["url"].casefold(),
        ),
    )
