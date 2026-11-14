"""Build customer-safe report summaries from the private Business Auditor result.

This module deliberately excludes scorecards, package/pricing suggestions,
keywords, competitor notes, CRM fields, raw crawl data and all other internal
analysis. The public customer sees only evidence-led findings and fixes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse


_IMPACTS = (
    ("https", "A browser warning or an unencrypted connection can make visitors hesitate before they contact you."),
    ("booking", "When prices or booking details are difficult to find, interested customers may leave before enquiring."),
    ("enquiry", "If the route to call, message or request a quote is unclear, interested visitors may not take the next step."),
    ("on-page seo", "Clear page headings, summaries and image descriptions help visitors understand the page and give search engines useful context."),
    ("service", "Visitors and search engines may not find a clear page explaining each important service."),
    ("social", "Customers may find it harder to confirm that an account belongs to the business or is currently active."),
    ("google business profile", "Accurate hours, contact details and service-area information make it easier for local customers to choose and reach the business."),
    ("google", "A complete, correctly matched local profile can help nearby customers verify how to contact or visit the business."),
    ("review", "Public reviews help potential customers judge what working with the business may be like."),
    ("offer", "If the offer or service area is vague, a potential customer may not know whether the business is right for them."),
    ("business name", "Using the same business name across the website and public listings makes the business easier to recognise."),
    ("structured data", "Clear business details help search services interpret who the business is and where it operates."),
    ("keyword", "Checking what customers actually search for helps avoid spending time on topics with little evidence of demand."),
    ("page title", "Distinct page titles help visitors and search engines understand what each page covers."),
    ("internal linking", "Useful links help visitors reach important pages and help search engines discover them."),
    ("ai search", "Clear, consistent business information and useful answers make it easier for search tools to understand the business."),
    ("location", "A genuinely useful area page can answer local customers' questions; a thin or inaccurate page could reduce trust."),
)


def _plain(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"<[^>]{0,300}>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _customer_words(value: object, limit: int) -> str:
    text = _plain(value, limit)
    text = re.sub(r"redirect every http:// address to its https:// version", "make the secure version the default for every page", text, flags=re.IGNORECASE)
    text = re.sub(r"plain http://", "unencrypted website addresses", text, flags=re.IGNORECASE)
    text = re.sub(r"\ban?\s+https?:// address", "the secure website address", text, flags=re.IGNORECASE)
    text = re.sub(r"https?:// address", "the secure website address", text, flags=re.IGNORECASE)
    text = re.sub(r"http://", "unencrypted version", text, flags=re.IGNORECASE)
    text = re.sub(r"https://", "secure version", text, flags=re.IGNORECASE)
    text = re.sub(r"free SSL certificate", "free website security certificate", text, flags=re.IGNORECASE)
    text = re.sub(r"SSL certificate", "website security certificate", text, flags=re.IGNORECASE)
    text = re.sub(r"Google['’]s Rich Results Test", "Google's structured-information checker", text, flags=re.IGNORECASE)
    text = re.sub(r"Google Search Console or a keyword data provider", "a keyword research tool", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsameAs links\b", "links", text, flags=re.IGNORECASE)
    replacements = (
        (r"\bH1 heading\b", "main page heading"),
        (r"\bH1\b", "main heading"),
        (r"\bmeta descriptions?\b", "short search-result summaries"),
        (r"\balt text\b", "descriptive text for images"),
        (r"\bLocalBusiness schema\b", "structured business details"),
        (r"\bstructured data\b", "business information search engines can read"),
        (r"\bschema markup\b", "business information search engines can read"),
        (r"\bSERP\b", "search results"),
        (r"\bSEO\b", "search visibility"),
        (r"\bAI search readiness\b", "visibility in newer search tools"),
        (r"\bHTTPS\b", "secure version"),
        (r"\bHTTP\b", "unencrypted version"),
        (r"\bHTTP[S]?\b", "website connection"),
        (r"\brobots\.txt\b", "search-engine access settings"),
        (r"\bcanonical URL\b", "preferred website address"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Internal category scores are not part of the customer report.
    text = re.sub(r"\b(?:observed\s+)?(?:website structure|business identity clarity|profile) score:?\s*\d+\s*/\s*\d+[^.;]*[.;]?", "The audit identified an area to review.", text, flags=re.IGNORECASE)
    return text[:limit].strip()


def _impact(title: str, evidence: str) -> str:
    combined = f"{title} {evidence}".lower()
    if any(term in combined for term in ("not verified", "not confirm", "candidate", "did not match", "not prove", "not verified")):
        return "This check could not confirm the public information automatically. Treat it as a prompt to verify the correct business details, not proof that a listing or review does not exist."
    for term, explanation in _IMPACTS:
        if term in title.lower():
            return explanation
    return "This can make it harder for customers to find the right information, understand the offer, or feel confident taking the next step."


def _safe_website(value: object) -> str:
    raw = _plain(value, 300)
    if not raw:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, flags=re.IGNORECASE) and not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return candidate
    except ValueError:
        return ""


def to_customer_report(audit: dict) -> dict:
    """Return the narrowly scoped fields approved for an online client report."""
    if not isinstance(audit, dict) or audit.get("success") is False:
        raise ValueError("A completed successful audit is required")

    business_name = _plain(audit.get("business_name"), 120)
    website = _safe_website(audit.get("website"))
    if not business_name or not website:
        raise ValueError("The completed audit must include a business name and valid website")

    opportunities = audit.get("opportunities")
    if not isinstance(opportunities, list):
        opportunities = []

    findings = []
    for item in opportunities[:12]:
        if not isinstance(item, dict):
            continue
        title = _plain(item.get("title"), 160)
        evidence = _customer_words(item.get("evidence"), 700)
        action = _customer_words(item.get("action"), 800)
        if not title or not action:
            continue
        priority = str(item.get("priority") or "Medium").strip().lower()
        priority_label = "Start here" if priority == "high" else "Plan next" if priority == "medium" else "Consider later"
        findings.append({
            "title": _customer_words(title, 160),
            "priority": priority_label,
            "whatWeFound": evidence or "This area needs a manual check before a firm conclusion can be made.",
            "whyItMatters": _impact(title, evidence),
            "whatToDo": action,
        })

    try:
        parsed_date = datetime.fromisoformat(str(audit.get("generated_at", "")).replace("Z", "+00:00"))
        report_date = parsed_date.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        report_date = datetime.now(timezone.utc).date().isoformat()

    location = _plain(audit.get("location"), 120)
    report = {
        "schemaVersion": 1,
        "businessName": business_name,
        "website": website,
        "location": location,
        "reportDate": report_date,
        "summary": (
            f"This review found {len(findings)} areas to check or improve. Each item below explains what was observed, "
            "why it may matter to customers, and a practical next step."
        ),
        "scopeNote": (
            "This review uses public website and listing evidence available on the date shown. It does not measure sales, "
            "website traffic or live search rankings. Where something could not be verified, the report says so. "
            "Recommendations are practical suggestions, not promises of leads or rankings."
        ),
        "findings": findings,
    }
    if len(str(report).encode("utf-8")) > 18_000:
        raise ValueError("The customer report is too large to publish safely")
    return report
