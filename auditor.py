
"""
Business Opportunity Auditor
Main Analysis and Commercial Recommendation Engine
"""

from urllib.parse import urlparse

from researcher import research_website
from scoring import calculate_digital_presence_score


def safe_text(value):
    """Convert a value into readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return ", ".join(safe_text(item) for item in value if item)
    if isinstance(value, dict):
        return ", ".join(
            f"{key}: {safe_text(item)}"
            for key, item in value.items()
            if item
        )
    return str(value)


def get_domain(url):
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.netloc or parsed.path).lower().removeprefix("www.")


def get_business_name(research, url):
    """Use available page titles, then fall back to the domain."""
    pages = research.get("pages", [])

    for page in pages:
        if not isinstance(page, dict):
            continue

        title = safe_text(page.get("title"))
        if title:
            # Avoid using a generic title as the business name.
            cleaned = title.split("|")[0].split(" – ")[0].split(" - ")[0].strip()
            if cleaned and cleaned.lower() not in {
                "home", "homepage", "welcome", "index"
            }:
                return cleaned

    domain = get_domain(url)
    return domain or "Unknown business"


def category_result(scoring, category):
    categories = scoring.get("categories", {})
    result = categories.get(category, {})
    return result if isinstance(result, dict) else {}


def add_opportunity(
    opportunities,
    title,
    priority,
    reason,
    action,
    service_fit,
    evidence,
):
    opportunities.append({
        "title": title,
        "priority": priority,
        "reason": reason,
        "recommended_action": action,
        "service_fit": service_fit,
        "evidence": evidence,
    })


def determine_opportunities(research, scoring):
    """
    Generate opportunities from collected evidence.

    Recommendations are phrased cautiously when a weakness has not
    actually been verified.
    """
    opportunities = []

    website = category_result(scoring, "website")
    google = category_result(scoring, "google")
    social = category_result(scoring, "social")
    content = category_result(scoring, "content")
    reputation = category_result(scoring, "reputation")
    brand = category_result(scoring, "brand")

    website_score = website.get("score")
    social_score = social.get("score")
    content_score = content.get("score")
    brand_score = brand.get("score")

    page_count = research.get("pages_researched", 0)
    if not page_count:
        pages = research.get("pages", [])
        page_count = len(pages) if isinstance(pages, list) else 0

    if website_score is not None and website_score < 12:
        add_opportunity(
            opportunities,
            "Improve website enquiry conversion",
            "High",
            "The collected website evidence indicates areas where the "
            "visitor journey or contact path may be strengthened.",
            "Review the main service pages, calls to action, contact options "
            "and quote request process. Prioritise the changes that make "
            "it easier for a prospective customer to enquire.",
            "Website improvement",
            website.get("evidence", []),
        )

    if content_score is not None and content_score < 10:
        add_opportunity(
            opportunities,
            "Build useful service content",
            "High",
            "The crawl found limited evidence of a broad range of useful "
            "content types.",
            "Create clear service descriptions, answers to common customer "
            "questions, project examples and location relevant pages where "
            "these would help customers make a buying decision.",
            "Content creation",
            content.get("evidence", []),
        )

    if social_score is not None and social_score < 9:
        add_opportunity(
            opportunities,
            "Review social profile coverage",
            "Medium",
            "The website crawl identified a limited number of linked "
            "social platforms.",
            "Confirm which social profiles the business actively uses, "
            "make relevant profiles easy to find from the website, and "
            "build a realistic content plan for the channels that matter "
            "to its customers.",
            "Social media support",
            social.get("evidence", []),
        )

    if google.get("status") in {"not_verified", "partial"}:
        add_opportunity(
            opportunities,
            "Verify Google Business visibility",
            "High",
            "The current website audit could not confirm the quality or "
            "completeness of the business's Google Business Profile.",
            "Search for the correct business listing, confirm ownership "
            "and key details where possible, and check categories, contact "
            "details, service areas, photos and review activity. Do not "
            "create a duplicate listing.",
            "Local presence review",
            google.get("evidence", []),
        )

    if reputation.get("status") == "not_verified":
        add_opportunity(
            opportunities,
            "Assess customer reviews and trust signals",
            "Medium",
            "Independent public reviews and ratings were not verified "
            "during this website audit.",
            "Check the main review platforms relevant to the business, "
            "record the rating and review count with the source and date, "
            "and identify a compliant process for requesting genuine "
            "customer feedback.",
            "Reputation support",
            reputation.get("evidence", []),
        )

    if brand_score is not None and brand_score < 9:
        add_opportunity(
            opportunities,
            "Clarify the business offer",
            "Medium",
            "The collected text did not establish all the basic business "
            "identity and contact signals.",
            "Make the core services, target customers, service area and "
            "contact route easy to understand. Use specific evidence and "
            "customer benefits rather than unsupported marketing claims.",
            "Website copy improvement",
            brand.get("evidence", []),
        )

    if page_count and page_count < 3:
        add_opportunity(
            opportunities,
            "Review website coverage",
            "Medium",
            "The crawl reached only a small number of pages.",
            "Confirm whether important service, location, project and "
            "contact pages exist and can be reached by visitors and search "
            "engines. A small site is not automatically a poor site.",
            "Website and content review",
            [f"Pages researched: {page_count}."],
        )

    if not opportunities:
        add_opportunity(
            opportunities,
            "Complete a deeper growth audit",
            "Low",
            "The current crawl did not reveal a strong, specific weakness "
            "that can safely be confirmed from the available evidence.",
            "Verify the business's Google listing, public reviews, active "
            "social channels and enquiry process before proposing changes.",
            "Business presence audit",
            [
                "The current audit is limited to information collected "
                "from the website."
            ],
        )

    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    opportunities.sort(
        key=lambda item: priority_order.get(item["priority"], 3)
    )

    return opportunities[:6]


def recommend_package(score, opportunities):
    """
    Recommend a starting service level.

    Package choice is based on observable opportunity and audit coverage,
    not on an unverified claim that the business needs every service.
    """
    coverage = score.get("assessment_coverage", 0)
    high_count = sum(
        1 for item in opportunities
        if item.get("priority") == "High"
    )
    medium_count = sum(
        1 for item in opportunities
        if item.get("priority") == "Medium"
    )

    if coverage < 60:
        return {
            "name": "Business Presence",
            "monthly_price": 500,
            "currency": "ZAR",
            "fit": "Entry level, subject to confirming the opportunities.",
            "reason": (
                "The audit has limited verified coverage. Start with a "
                "focused service and verify the main opportunities before "
                "selling a broader package."
            ),
            "suggested_scope": [
                "One agreed monthly digital presence task",
                "A short monthly progress summary",
                "A prioritised next action",
            ],
            "important_note": (
                "Confirm the scope and expected workload before offering "
                "this price. This recommendation is not a promise of results."
            ),
        }

    if high_count >= 2 or (
        high_count >= 1 and medium_count >= 3
    ):
        return {
            "name": "Professional Presence",
            "monthly_price": 1500,
            "currency": "ZAR",
            "fit": "Broader monthly support, if the client needs it.",
            "reason": (
                "The audit identified several potential work areas. "
                "Confirm which ones matter to the client before proposing "
                "a larger monthly scope."
            ),
            "suggested_scope": [
                "A monthly content plan",
                "Agreed website or content improvements",
                "Social content support where relevant",
                "A monthly progress summary",
            ],
            "important_note": (
                "Agree deliverables and workload before quoting. Do not "
                "include unlimited work or guaranteed outcomes."
            ),
        }

    if high_count >= 1 or medium_count >= 2:
        return {
            "name": "Business Growth",
            "monthly_price": 750,
            "currency": "ZAR",
            "fit": "A focused step up from the entry package.",
            "reason": (
                "The audit suggests a small number of areas that may "
                "benefit from structured monthly attention."
            ),
            "suggested_scope": [
                "One or two agreed monthly improvement tasks",
                "Basic content or profile support where appropriate",
                "A monthly progress summary",
            ],
            "important_note": (
                "Confirm the actual need and scope with the business owner."
            ),
        }

    return {
        "name": "Business Presence",
        "monthly_price": 500,
        "currency": "ZAR",
        "fit": "A low commitment starting point.",
        "reason": (
            "The available evidence supports starting small rather than "
            "assuming that the business needs a larger package."
        ),
        "suggested_scope": [
            "One agreed monthly digital presence task",
            "A short monthly progress summary",
            "A prioritised next action",
        ],
        "important_note": (
            "Confirm the deliverables and workload before offering this price."
        ),
    }


def create_outreach_opener(business_name, opportunities):
    """Create a short, evidence conscious opening for a sales conversation."""
    if opportunities:
        first = opportunities[0]
        topic = first.get("title", "digital presence")
        reason = first.get("reason", "")
        return (
            f"Hi, I was looking at {business_name}'s website and noticed "
            f"an area worth checking: {topic.lower()}. {reason} "
            "I have a couple of practical ideas that may help. "
            "Would you be open to a brief conversation?"
        )

    return (
        f"Hi, I was reviewing {business_name}'s online presence and have "
        "a couple of ideas that may be worth checking. Would you be open "
        "to a brief conversation?"
    )


def generate_audit(url):
    """Run research, scoring and commercial analysis for one website."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Please provide a valid website URL.")

    url = url.strip()
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        raise ValueError(
            "The website URL does not look valid. Example: https://example.co.za"
        )

    research = research_website(url)

    if not isinstance(research, dict):
        raise TypeError("The researcher did not return the expected dictionary.")

    scoring = calculate_digital_presence_score(research)
    opportunities = determine_opportunities(research, scoring)
    package = recommend_package(scoring, opportunities)

    business_name = get_business_name(research, url)

    return {
        "business_name": business_name,
        "website": url,
        "domain": get_domain(url),
        "research": research,
        "score": scoring,
        "opportunities": opportunities,
        "package": package,
        "outreach_opener": create_outreach_opener(
            business_name, opportunities
        ),
        "audit_limitations": [
            "This audit evaluates information collected from the website.",
            "Google profile quality, independent review data, social activity, "
            "traffic and conversion rates have not been independently measured.",
            "Recommendations are opportunities to investigate, not proof "
            "that the business is losing sales.",
        ],
    }


def print_audit(audit):
    """Print a readable report in the terminal."""
    print("\n" + "=" * 68)
    print("BUSINESS OPPORTUNITY AUDITOR")
    print("=" * 68)

    print(f"\nBusiness: {audit.get('business_name', 'Unknown business')}")
    print(f"Website:  {audit.get('website', 'Not available')}")

    research = audit.get("research", {})
    print(f"Pages researched: {research.get('pages_researched', 0)}")

    score = audit.get("score", {})
    overall = score.get("overall_score")
    coverage = score.get("assessment_coverage", 0)

    print("\nDIGITAL PRESENCE SCORE")
    if overall is None:
        print("Assessed score: Not available")
    else:
        print(f"Assessed score: {overall}/100")
    print(f"Assessment coverage: {coverage}%")
    print(
        f"Assessed points: {score.get('points_earned', 0)} / "
        f"{score.get('points_assessed', 0)}"
    )

    categories = score.get("categories", {})
    category_labels = {
        "website": "Website",
        "google": "Google presence",
        "social": "Social links",
        "content": "Content",
        "reputation": "Customer reputation",
        "brand": "Business identity",
    }

    for key, label in category_labels.items():
        result = categories.get(key, {})
        value = result.get("score")
        maximum = result.get("max_score", 0)
        status = result.get("status", "unknown")

        if value is None:
            print(f"  {label}: Not scored / {maximum} ({status})")
        else:
            print(f"  {label}: {value} / {maximum} ({status})")

        for item in result.get("evidence", []):
            print(f"    Evidence: {item}")

    print("\nPRIORITISED OPPORTUNITIES")
    for index, item in enumerate(audit.get("opportunities", []), start=1):
        print(
            f"\n{index}. [{item.get('priority', 'Unrated')}] "
            f"{item.get('title', 'Opportunity')}"
        )
        print(f"   Why: {item.get('reason', '')}")
        print(f"   Action: {item.get('recommended_action', '')}")
        print(f"   Service fit: {item.get('service_fit', '')}")

    package = audit.get("package", {})
    print("\nSUGGESTED MONTHLY PACKAGE")
    print(
        f"{package.get('name', 'Not determined')}: "
        f"R{package.get('monthly_price', 0):,}/month"
    )
    print(f"Fit: {package.get('fit', '')}")
    print(f"Reason: {package.get('reason', '')}")

    if package.get("suggested_scope"):
        print("Suggested scope:")
        for task in package["suggested_scope"]:
            print(f"  • {task}")

    print("\nSUGGESTED SALES OPENER")
    print(audit.get("outreach_opener", ""))

    print("\nLIMITATIONS")
    for limitation in audit.get("audit_limitations", []):
        print(f"  • {limitation}")

    print("\n" + "=" * 68)


if __name__ == "__main__":
    website = input("Enter the business website: ").strip()

    try:
        result = generate_audit(website)
        print_audit(result)
    except Exception as exc:
        print(f"\nAudit failed: {exc}")
        print(
            "Check the website address and internet connection. "
            "If the error persists, inspect the researcher error."
        )