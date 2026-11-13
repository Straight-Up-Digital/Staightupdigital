
"""
Business Opportunity Auditor
Evidence Based Digital Presence Scoring Engine

Scores observable website evidence only.
External platforms and customer reviews are not treated as verified
unless the researcher has actually supplied supporting evidence.
"""

import re
from urllib.parse import urlparse


CATEGORY_MAX = {
    "website": 20,
    "google": 20,
    "social": 15,
    "content": 15,
    "reputation": 15,
    "brand": 15,
}


def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))


def clean_text(value):
    """Convert common researcher values into searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(clean_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(clean_text(item) for item in value.values())
    return str(value)


def get_page_text(pages):
    """Collect text fields without assuming every page has every field."""
    parts = []

    for page in pages or []:
        if not isinstance(page, dict):
            continue

        for key in (
            "title",
            "description",
            "meta_description",
            "headings",
            "text",
            "page_text",
            "content",
        ):
            value = page.get(key)
            if value:
                parts.append(clean_text(value))

    return " ".join(parts)


def get_page_count(research):
    pages = research.get("pages", [])
    if isinstance(pages, list):
        return len(pages)
    return int(research.get("pages_researched", 0) or 0)


def get_links(research):
    links = research.get("external_links", [])
    return [clean_text(link).lower() for link in links]


def get_social_profiles(research):
    profiles = research.get("social_profiles", [])

    if isinstance(profiles, dict):
        return [
            {"platform": str(platform), "url": clean_text(url)}
            for platform, url in profiles.items()
        ]

    if isinstance(profiles, list):
        result = []
        for profile in profiles:
            if isinstance(profile, dict):
                result.append(profile)
            elif isinstance(profile, str):
                result.append({"url": profile})
        return result

    return []


def score_website(research):
    """Score the website's observable structure, not just keyword presence."""
    pages = research.get("pages", [])
    page_count = get_page_count(research)
    successful_pages = [
        page for page in pages
        if isinstance(page, dict)
        and page.get("success", True) is not False
    ]

    score = 0
    evidence = []

    if page_count >= 5:
        score += 5
        evidence.append(f"{page_count} pages were researched.")
    elif page_count >= 2:
        score += 3
        evidence.append(f"{page_count} pages were researched.")
    elif page_count == 1:
        score += 1
        evidence.append("Only one page was researched.")
    else:
        evidence.append("No page content was available to score.")

    titles = [
        clean_text(page.get("title")).strip()
        for page in successful_pages
        if clean_text(page.get("title")).strip()
    ]
    descriptions = [
        clean_text(
            page.get("description") or page.get("meta_description")
        ).strip()
        for page in successful_pages
        if clean_text(
            page.get("description") or page.get("meta_description")
        ).strip()
    ]
    headings = [
        clean_text(page.get("headings")).strip()
        for page in successful_pages
        if clean_text(page.get("headings")).strip()
    ]

    if titles:
        score += 4
        evidence.append(f"Page titles were found on {len(titles)} pages.")
    else:
        evidence.append("Page titles were not found in the collected data.")

    if descriptions:
        score += 4
        evidence.append(
            f"Meta descriptions were found on {len(descriptions)} pages."
        )
    else:
        evidence.append(
            "Meta descriptions were not found in the collected data."
        )

    if headings:
        score += 3
        evidence.append("Page headings were found.")
    else:
        evidence.append("Page headings were not found in the collected data.")

    combined = get_page_text(successful_pages).lower()
    contact_signals = [
        word for word in ("contact", "phone", "email", "get a quote")
        if word in combined
    ]

    if contact_signals:
        score += 4
        evidence.append(
            "Contact or enquiry wording was found: "
            + ", ".join(contact_signals)
            + "."
        )
    else:
        evidence.append(
            "A clear contact or enquiry path was not established "
            "from the collected text."
        )

    return {
        "score": clamp(score, 0, 20),
        "max_score": 20,
        "status": "assessed" if successful_pages else "limited",
        "evidence": evidence,
    }


def score_google(research):
    """A website link is a clue, not proof that a Google profile is complete."""
    links = get_links(research)
    social_profiles = get_social_profiles(research)

    candidates = links + [
        clean_text(profile.get("url", profile.get("link", ""))).lower()
        for profile in social_profiles
        if isinstance(profile, dict)
    ]

    google_links = [
        link for link in candidates
        if any(domain in link for domain in (
            "google.com/maps",
            "maps.google.",
            "g.page/",
            "google.com/search",
            "google.co.za/maps",
        ))
    ]

    if google_links:
        return {
            "score": None,
            "max_score": 20,
            "status": "partial",
            "evidence": [
                "A Google or Maps link was found on the website.",
                "The Google Business Profile itself has not been audited.",
            ],
        }

    return {
        "score": None,
        "max_score": 20,
        "status": "not_verified",
        "evidence": [
            "The website crawl did not establish a Google Business Profile.",
            "This does not prove that a profile is absent.",
        ],
    }


def score_social(research):
    """Score linked social platform coverage, not posting activity."""
    profiles = get_social_profiles(research)
    links = get_links(research)

    found = set()
    all_values = links[:]

    for profile in profiles:
        if isinstance(profile, dict):
            url = clean_text(
                profile.get("url", profile.get("link", ""))
            ).lower()
            platform = clean_text(profile.get("platform", "")).lower()
            all_values.extend([url, platform])

    platform_domains = {
        "Facebook": ("facebook.com", "fb.com"),
        "Instagram": ("instagram.com",),
        "LinkedIn": ("linkedin.com",),
        "YouTube": ("youtube.com", "youtu.be"),
        "TikTok": ("tiktok.com",),
    }

    for platform, domains in platform_domains.items():
        if any(
            domain in value
            for value in all_values
            for domain in domains
        ):
            found.add(platform)

    score = min(15, len(found) * 3)
    evidence = []

    if found:
        evidence.append("Linked social platforms found: " + ", ".join(sorted(found)) + ".")
        evidence.append(
            "This measures linked platform coverage, not account activity "
            "or audience engagement."
        )
    else:
        evidence.append(
            "No supported social profile links were identified in the "
            "collected website data."
        )
        evidence.append(
            "Social accounts may exist without being linked from the website."
        )

    return {
        "score": score,
        "max_score": 15,
        "status": "assessed",
        "evidence": evidence,
        "platforms_found": sorted(found),
    }


def score_content(research):
    """Score basic content coverage using observable page evidence."""
    pages = research.get("pages", [])
    page_count = get_page_count(research)
    text = get_page_text(pages).lower()

    score = 0
    evidence = []

    if page_count >= 5:
        score += 5
        evidence.append("The crawl found at least five pages.")
    elif page_count >= 3:
        score += 3
        evidence.append("The crawl found three or four pages.")
    elif page_count >= 1:
        score += 1
        evidence.append("The crawl found only one or two pages.")
    else:
        evidence.append("No page count was available for content scoring.")

    content_groups = {
        "Services or products": (
            "services", "our services", "products", "solutions",
            "what we do",
        ),
        "Location or service area": (
            "service area", "areas we serve", "located in", "south africa",
            "cape town", "durban", "johannesburg",
        ),
        "Portfolio or project examples": (
            "portfolio", "our work", "projects", "case studies",
            "gallery", "completed work",
        ),
        "Helpful educational content": (
            "blog", "articles", "guides", "frequently asked questions",
            "faq", "how to",
        ),
        "Customer proof": (
            "testimonials", "customer reviews", "client feedback",
            "what our clients say",
        ),
    }

    found_groups = []

    for label, terms in content_groups.items():
        if any(term in text for term in terms):
            found_groups.append(label)

    score += min(10, len(found_groups) * 2)

    if found_groups:
        evidence.append(
            "Content types indicated by the collected text: "
            + ", ".join(found_groups)
            + "."
        )
    else:
        evidence.append(
            "The collected text did not establish the listed content types."
        )

    return {
        "score": clamp(score, 0, 15),
        "max_score": 15,
        "status": "assessed" if pages else "limited",
        "evidence": evidence,
        "content_types_found": found_groups,
    }


def score_reputation(research):
    """Do not claim to measure reviews without actual review data."""
    signals = research.get("business_signals", {})
    if not isinstance(signals, dict):
        signals = {}

    return {
        "score": None,
        "max_score": 15,
        "status": "not_verified",
        "evidence": [
            "Customer ratings, review counts and review sentiment were not "
            "independently verified by the current audit.",
            "Testimonials mentioned on a website are not the same as "
            "independently verified public reviews.",
        ],
    }


def score_brand(research):
    """Measure clarity of basic business identity and service information."""
    pages = research.get("pages", [])
    text = get_page_text(pages).lower()
    score = 0
    evidence = []

    titles = [
        clean_text(page.get("title")).strip()
        for page in pages
        if isinstance(page, dict) and clean_text(page.get("title")).strip()
    ]

    if titles:
        score += 3
        evidence.append("The website provides page title information.")
    else:
        evidence.append("Page title information was not available.")

    service_terms = (
        "services", "products", "solutions", "we offer", "our services",
        "manufacture", "installation", "repairs", "cleaning", "consulting",
    )
    if any(term in text for term in service_terms):
        score += 4
        evidence.append("The collected text indicates services or products.")
    else:
        evidence.append(
            "The collected text did not clearly establish the services "
            "or products offered."
        )

    contact_terms = ("contact", "phone", "telephone", "email", "address")
    if any(term in text for term in contact_terms):
        score += 4
        evidence.append("Contact information or contact wording was found.")
    else:
        evidence.append("Contact information was not established in the text.")

    identity_terms = (
        "about us", "our story", "our team", "years of experience",
        "established", "since ",
    )
    if any(term in text for term in identity_terms):
        score += 4
        evidence.append("Business identity or background wording was found.")
    else:
        evidence.append(
            "A clear business background statement was not established."
        )

    return {
        "score": clamp(score, 0, 15),
        "max_score": 15,
        "status": "assessed" if pages else "limited",
        "evidence": evidence,
    }


def calculate_digital_presence_score(research):
    """
    Return category scores, assessment coverage and an evidence-adjusted score.

    Unverified categories are excluded from the assessed score rather than
    being silently treated as zero.
    """
    categories = {
        "website": score_website(research),
        "google": score_google(research),
        "social": score_social(research),
        "content": score_content(research),
        "reputation": score_reputation(research),
        "brand": score_brand(research),
    }

    earned = 0
    assessed_max = 0
    total_max = sum(CATEGORY_MAX.values())

    for name, result in categories.items():
        result["max_score"] = CATEGORY_MAX[name]
        if result.get("score") is not None:
            earned += result["score"]
            assessed_max += result["max_score"]

    assessed_score = (
        round((earned / assessed_max) * 100)
        if assessed_max
        else None
    )
    coverage = round((assessed_max / total_max) * 100)

    return {
        "categories": categories,
        "points_earned": earned,
        "points_assessed": assessed_max,
        "points_possible": total_max,
        "overall_score": assessed_score,
        "assessment_coverage": coverage,
        "score_label": (
            f"{assessed_score}/100 of assessed categories"
            if assessed_score is not None
            else "Not enough evidence to calculate a score"
        ),
        "limitations": [
            "The score reflects only categories that can be assessed "
            "from the collected website data.",
            "Google profile quality and independent customer reputation "
            "require separate verification.",
            "A high score is not proof of strong sales performance.",
        ],
    }