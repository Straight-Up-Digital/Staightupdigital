"""Website conversion, trust, email, NAP, and report-pillar signals."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


def detect_site_signals(html: str) -> dict[str, bool]:
    soup = BeautifulSoup(html or "", "html.parser")
    hrefs = [
        str(tag.get("href") or "").strip()
        for tag in soup.find_all("a", href=True)
    ]
    source = (html or "").casefold()
    tel_link = any(href.lower().startswith("tel:") for href in hrefs)
    whatsapp_link = any(
        "wa.me/" in href.lower()
        or "api.whatsapp.com/" in href.lower()
        or href.lower().startswith("whatsapp:")
        for href in hrefs
    )
    privacy_link = any(
        re.search(r"(privacy|popia|data-protection)", href, re.IGNORECASE)
        for href in hrefs
    )
    ga4 = bool(
        re.search(r"googletagmanager\.com/gtag/js\?id=g-[a-z0-9]+", source)
        or re.search(r"google-analytics\.com/g/collect", source)
        or re.search(r"gtag\s*\(\s*['\"]config['\"]\s*,\s*['\"]g-", source)
    )
    gtm = bool(re.search(r"googletagmanager\.com/gtm\.js\?id=gtm-", source))
    meta_pixel = bool(
        "connect.facebook.net" in source and "fbevents.js" in source
        or re.search(r"fbq\s*\(\s*['\"]init['\"]", source)
    )
    email_link = any(href.lower().startswith("mailto:") for href in hrefs)
    contact_form = bool(soup.find("form"))
    visible_text = soup.get_text(" ", strip=True)
    phone_number = bool(
        re.search(r"(?:\+27|0)\s?\d{2,3}[\s().-]?\d{3}[\s.-]?\d{4}", visible_text)
    )
    email_address = bool(
        re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", visible_text, re.I)
    )
    return {
        "tel_link": tel_link,
        "whatsapp_link": whatsapp_link,
        "privacy_link": privacy_link,
        "ga4": ga4,
        "gtm": gtm,
        "meta_pixel": meta_pixel,
        "analytics": ga4 or gtm or meta_pixel,
        "email_link": email_link,
        "phone_number": phone_number,
        "email_address": email_address,
        "contact_form": contact_form,
        "has_contact_route": (
            tel_link or whatsapp_link or email_link or contact_form or phone_number or email_address
        ),
    }


def email_posture(
    mx_records: list[str] | None,
    txt_records: list[str] | None,
    dmarc_records: list[str] | None,
    lookup_worked: bool | None = None,
) -> dict[str, Any]:
    mx_values = [str(value).strip().rstrip(".") for value in (mx_records or []) if value]
    txt_values = [str(value).strip().strip('"') for value in (txt_records or []) if value]
    dmarc_values = [str(value).strip().strip('"') for value in (dmarc_records or []) if value]
    mx_host = ""
    if mx_values:
        mx_host = re.sub(r"^\d+\s+", "", mx_values[0]).rstrip(".").split()[-1]
    provider = "Unknown"
    mx_lower = mx_host.lower()
    if mx_lower:
        if "google" in mx_lower or "googlemail" in mx_lower:
            provider = "Google Workspace"
        elif "outlook" in mx_lower or "protection.outlook" in mx_lower:
            provider = "Microsoft 365"
        elif "zoho" in mx_lower:
            provider = "Zoho Mail"
        elif "hostafrica" in mx_lower:
            provider = "HostAfrica"
        elif "xneelo" in mx_lower:
            provider = "Xneelo"
        elif "afrihost" in mx_lower:
            provider = "Afrihost"
        elif "mimecast" in mx_lower:
            provider = "Mimecast"
        elif "mailgun" in mx_lower:
            provider = "Mailgun"
        else:
            provider = mx_host
    spf = next((value for value in txt_values if value.lower().startswith("v=spf1")), "")
    dmarc = next(
        (value for value in dmarc_values if value.lower().startswith("v=dmarc1")),
        "",
    )
    policy_match = re.search(r"(?:^|;)\s*p\s*=\s*(none|quarantine|reject)\b", dmarc, re.I)
    if lookup_worked is False:
        policy = "unknown"
        strength = "unknown"
        spf_present = None
        dmarc_present = None
        mx_present = None
    else:
        policy = policy_match.group(1).lower() if policy_match else "none"
        strength = {"reject": "strong", "quarantine": "moderate", "none": "monitoring only"}[policy]
        spf_present = bool(spf)
        dmarc_present = bool(dmarc)
        mx_present = bool(mx_values)
    return {
        "lookup_worked": lookup_worked is not False,
        "mx_present": mx_present,
        "mx_host": mx_host,
        "mx_provider": provider,
        "spf_present": spf_present,
        "spf_record": spf,
        "dmarc_present": dmarc_present,
        "dmarc_policy": policy,
        "dmarc_strength": strength,
    }


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _phone_digits(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return "0" + digits[2:] if digits.startswith("27") and len(digits) == 11 else digits


def compare_nap(website: dict[str, Any], google: dict[str, Any]) -> dict[str, Any]:
    website = website or {}
    google = google or {}
    results = {}
    for field in ("name", "phone", "address"):
        site_value = str(website.get(field) or "").strip()
        google_value = str(google.get(field) or "").strip()
        if not site_value and not google_value:
            status = "both missing"
        elif not site_value:
            status = "missing on website"
        elif not google_value:
            status = "missing on Google profile"
        elif field == "phone":
            status = "match" if _phone_digits(site_value) == _phone_digits(google_value) else "different"
        else:
            status = "match" if _normalized(site_value) == _normalized(google_value) else "different"
        results[field] = {
            "website": site_value or None,
            "google": google_value or None,
            "status": status,
        }
    return results


def build_pillars(audit: dict[str, Any]) -> list[dict[str, str]]:
    external = audit.get("external") or audit.get("external_research") or {}
    search_sets = external.get("searches") or {}
    if isinstance(search_sets, dict):
        searches = [value for value in search_sets.values() if isinstance(value, dict)]
    elif isinstance(search_sets, list):
        searches = [value for value in search_sets if isinstance(value, dict)]
    else:
        searches = []
    searches = [item for item in searches if item.get("query")]
    successful_searches = [item for item in searches if item.get("success")]
    speed = audit.get("pagespeed") or {}
    mobile = speed.get("mobile") or {}
    desktop = speed.get("desktop") or {}
    signals = audit.get("site_signals") or {}
    profiles = audit.get("profile_presence") or []
    verified = sum(profile.get("status") == "Verified" for profile in profiles)
    candidates = sum(profile.get("status") != "Verified" for profile in profiles)
    nap = audit.get("nap_comparison") or {}
    differing_nap = sum(
        item.get("status") in {"different", "missing on website", "missing on Google profile"}
        for item in nap.values()
    )
    mobile_score = (mobile.get("scores") or {}).get("performance")
    desktop_score = (desktop.get("scores") or {}).get("performance")
    query_details = [
        f"{item.get('query')} ({item.get('provider')}, {item.get('date')})"
        for item in searches
        if item.get("query") and item.get("provider")
    ]
    visibility_evidence = "; ".join(query_details[:2]) or "No live search results were collected."
    scores = []
    if mobile_score is not None:
        scores.append(f"Mobile lab performance: {mobile_score}/100")
    if desktop_score is not None:
        scores.append(f"Desktop lab performance: {desktop_score}/100")
    local = speed.get("local") or {}
    if local.get("response_time_ms") is not None:
        scores.append(f"Local response time: {local['response_time_ms']} ms")
    if not scores:
        scores.append("Local website checks are included where available.")
    contact_routes = [
        label
        for key, label in (
            ("tel_link", "phone"),
            ("phone_number", "phone"),
            ("whatsapp_link", "WhatsApp"),
            ("email_link", "email"),
            ("email_address", "email"),
            ("contact_form", "form"),
        )
        if signals.get(key)
    ]
    email = audit.get("email_posture") or {}
    spf_label = (
        "present" if email.get("spf_present") is True
        else "not detected" if email.get("spf_present") is False
        else "not verified"
    )
    return [
        {
            "name": "Visibility & Rankings",
            "verdict": (
                f"{len(successful_searches)} search query result set(s) returned."
                if successful_searches
                else "No search result set was returned for this audit."
            ),
            "evidence": visibility_evidence,
            "fix": "Use the recorded query, provider, date, and competitor results to prioritize local search improvements.",
        },
        {
            "name": "Website & Speed Health",
            "verdict": " | ".join(scores),
            "evidence": (
                f"Email posture: MX {email.get('mx_provider', 'Unknown')}; "
                f"SPF {spf_label}; "
                f"DMARC {email.get('dmarc_policy', 'none')}."
            ),
            "fix": "Address the measured performance and technical checks; use field data only when it is available.",
        },
        {
            "name": "Conversion & Lead Capture",
            "verdict": (
                "Detected contact routes: " + ", ".join(contact_routes) + "."
                if contact_routes
                else "No phone, WhatsApp, email link, or contact form was detected."
            ),
            "evidence": f"{differing_nap} Google-versus-website NAP field(s) need attention.",
            "fix": "Make the primary contact action and the business name, phone, and address consistent.",
        },
        {
            "name": "Reputation & Social Footprint",
            "verdict": f"{verified} verified profile(s); {candidates} candidate profile(s) not verified.",
            "evidence": "A profile is verified only when linked from the site or matched by business name and phone.",
            "fix": "Confirm candidate profiles manually before relying on them as official business listings.",
        },
    ]
