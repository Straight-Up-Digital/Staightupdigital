"""Consistent impact, effort, priority, and count rules for audit findings."""
from __future__ import annotations

import re
from typing import Any


_TECHNICAL_TERMS = re.compile(
    r"\b(canonical|schema markup|structured data|robots\.txt|sitemap|"
    r"meta description|alt text|crawlability|redirect chain|technical seo)\b",
    re.IGNORECASE,
)
_LOW_EFFORT_TERMS = re.compile(
    r"\b(add|update|fix|configure|verify|enable|link|record|redirect|"
    r"certificate|phone|contact|profile)\b",
    re.IGNORECASE,
)
_HIGH_EFFORT_TERMS = re.compile(
    r"\b(rebuild|redesign|migration|new website|rewrite|campaign|"
    r"content strategy|site architecture)\b",
    re.IGNORECASE,
)

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _text(finding: dict[str, Any]) -> str:
    return " ".join(
        str(finding.get(key) or "")
        for key in ("title", "evidence", "action", "service_fit")
    )


def _confirmed_blocker(
    finding: dict[str, Any], context: dict[str, Any]
) -> tuple[bool, str]:
    text = _text(finding).casefold()
    site = context.get("site") or {}
    site_health = context.get("site_health") or {}
    signals = context.get("signals") or {}
    pages = context.get("pages") or []
    google = context.get("google") or {}
    speed = context.get("speed") or {}

    if "https" in text or "ssl" in text or "certificate" in text:
        if pages and all(not (page.get("technical") or {}).get("https") for page in pages):
            return True, "The crawled pages do not use HTTPS."
        if site_health.get("ssl_days_left") is not None and site_health["ssl_days_left"] < 0:
            return True, "The website's HTTPS certificate has expired."
        if context.get("ssl_invalid"):
            return True, "The website's HTTPS certificate could not be verified."
    if any(term in text for term in ("contact route", "contact method", "phone number", "enquiry path")):
        if not signals.get("has_contact_route") and not any(
            signals.get(key)
            for key in (
                "tel_link",
                "whatsapp_link",
                "email_link",
                "contact_form",
                "phone_number",
                "email_address",
            )
        ):
            return True, "No working contact route was detected on the website."
    if any(term in text for term in ("google business profile", "google maps listing", "google listing")):
        if google.get("manual_verified_absent") or google.get("manual_confirmed_wrong"):
            return True, "The Google Business Profile issue was manually confirmed."
    if any(
        term in text
        for term in (
            "mobile speed",
            "mobile performance",
            "mobile usability",
            "speed up the website on phones",
        )
    ):
        mobile = speed.get("mobile") or {}
        if (mobile.get("scores") or {}).get("performance") is not None:
            if mobile["scores"]["performance"] < 40:
                return True, "The measured mobile performance score is below 40."
    status_code = site.get("status_code")
    if (
        ("unavailable" in text or "site is down" in text)
        and site.get("checked")
        and (
        status_code in (404, 410)
        or isinstance(status_code, int) and status_code >= 500
        )
    ):
        return True, "The website check confirmed the site is unavailable."
    return False, ""


def _effort(finding: dict[str, Any]) -> str:
    text = _text(finding)
    if _HIGH_EFFORT_TERMS.search(text):
        return "High"
    if _LOW_EFFORT_TERMS.search(text):
        return "Low"
    return "Medium"


def _is_minor_technical(finding: dict[str, Any]) -> bool:
    return bool(_TECHNICAL_TERMS.search(_text(finding)))


def annotate_findings(
    findings: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    context = context or {}
    annotated: list[dict[str, Any]] = []
    for source in findings:
        finding = dict(source)
        blocker, reason = _confirmed_blocker(finding, context)
        effort = _effort(finding)
        impact = "High" if blocker else ("Low" if _is_minor_technical(finding) else "Medium")
        if blocker:
            priority = "High"
        elif impact == "Medium" and effort != "High":
            priority = "Medium"
        else:
            priority = "Low"
        finding.update(
            impact=impact,
            effort=effort,
            priority=priority,
            blocker=blocker,
            priority_reason=reason or (
                "A verified visibility or conversion blocker."
                if blocker
                else "A useful improvement, not a verified blocker."
            ),
        )
        annotated.append(finding)
    return annotated


def group_low_technical(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    technical = [
        finding
        for finding in findings
        if finding.get("priority") == "Low" and _is_minor_technical(finding)
    ]
    if not technical:
        return findings
    details = [str(finding.get("title") or "Technical item") for finding in technical]
    grouped = {
        "title": "Technical tidy-up",
        "evidence": "Minor technical items: " + "; ".join(details),
        "action": "Address these lower-impact items: " + "; ".join(
            str(finding.get("action") or finding.get("title") or "Review item")
            for finding in technical
        ),
        "impact": "Low",
        "effort": "Medium",
        "priority": "Low",
        "blocker": False,
        "priority_reason": "Grouped minor technical maintenance.",
        "items": technical,
    }
    technical_ids = {id(finding) for finding in technical}
    remaining = [finding for finding in findings if id(finding) not in technical_ids]
    remaining.append(grouped)
    return remaining


def finalize_findings(
    findings: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    finalized = group_low_technical(annotate_findings(findings, context))
    finalized.sort(
        key=lambda finding: (
            PRIORITY_ORDER.get(finding.get("priority"), 1),
            str(finding.get("title") or "").casefold(),
        )
    )
    return finalized[:limit] if limit else finalized


def priority_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {priority: 0 for priority in PRIORITY_ORDER}
    for finding in findings:
        priority = finding.get("priority")
        if priority in counts:
            counts[priority] += 1
    return counts
