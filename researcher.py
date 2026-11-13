import re
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque

MAX_PAGES = 10

PRIORITY_WORDS = [
    "about",
    "service",
    "product",
    "portfolio",
    "project",
    "contact",
    "industry",
    "solution",
    "gallery",
    "case",
    "testimonial",
    "client",
    "customer",
    "faq",
    "team",
]

IGNORED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".mp4",
    ".mp3",
    ".avi",
    ".mov",
)

IGNORED_PATHS = (
    "/wp-json/",
    "/wp-admin/",
    "/feed/",
    "/comments/",
    "/tag/",
    "/author/",
)

IGNORED_QUERY_TERMS = (
    "share=",
    "sharer",
    "sharethis",
)


def clean_text(text):
    """Clean extracted webpage text."""

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url):
    """Normalize URLs for comparison."""

    parsed = urlparse(url)

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()

    path = parsed.path.rstrip("/")

    if not path:
        path = ""

    return f"{scheme}://{netloc}{path}"


def is_same_domain(url, domain):
    """Check whether a URL belongs to the target domain."""

    try:
        hostname = urlparse(url).netloc.lower()
        hostname = hostname.replace("www.", "")

        target = domain.lower().replace("www.", "")

        return hostname == target

    except Exception:
        return False


def is_ignored_url(url):
    """Ignore files, admin pages and social sharing URLs."""

    lower = url.lower()

    if lower.endswith(IGNORED_EXTENSIONS):
        return True

    for path in IGNORED_PATHS:
        if path in lower:
            return True

    for term in IGNORED_QUERY_TERMS:
        if term in lower:
            return True

    return False


def is_useful_page(url):
    """Determine whether a page is likely to contain useful business information."""

    lower = url.lower()

    for word in PRIORITY_WORDS:
        if word in lower:
            return True

    return urlparse(url).path in ("", "/")


def extract_page(url, client):
    """Download and extract useful information from one webpage."""

    try:
        response = client.get(url, follow_redirects=True)

        final_url = normalize_url(str(response.url))

        if response.status_code != 200:
            return {
                "success": False,
                "url": final_url,
                "status_code": response.status_code,
                "error": f"HTTP {response.status_code}",
            }

        soup = BeautifulSoup(response.text, "html.parser")

        for element in soup(
            [
                "script",
                "style",
                "noscript",
                "svg",
                "iframe",
            ]
        ):
            element.decompose()

        title = ""

        if soup.title:
            title = clean_text(soup.title.get_text())

        description = ""

        meta_description = soup.find(
            "meta",
            attrs={"name": "description"}
        )

        if meta_description:
            description = clean_text(
                meta_description.get("content", "")
            )

        headings = []

        for heading in soup.find_all(
            ["h1", "h2", "h3"]
        ):
            text = clean_text(heading.get_text(" ", strip=True))

            if text and text not in headings:
                headings.append(text)

        links = []
        emails = set()

        for link in soup.find_all("a", href=True):

            href = link.get("href", "").strip()

            text = clean_text(
                link.get_text(" ", strip=True)
            )

            if not href:
                continue

            absolute_url = urljoin(
                final_url,
                href
            )

            if href.lower().startswith("mailto:"):

                email = href[7:].split("?")[0].strip()

                if email:
                    emails.add(email)

                links.append(
                    {
                        "text": text,
                        "url": href,
                        "internal": False,
                        "priority": False,
                    }
                )

                continue

            parsed = urlparse(absolute_url)

            if parsed.scheme not in ("http", "https"):
                continue

            normalized = normalize_url(
                absolute_url
            )

            internal = is_same_domain(
                normalized,
                urlparse(final_url).netloc
            )

            priority = internal and is_useful_page(
                normalized
            )

            links.append(
                {
                    "text": text,
                    "url": normalized,
                    "internal": internal,
                    "priority": priority,
                }
            )

        body_text = clean_text(
            soup.get_text(" ", strip=True)
        )

        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"

        for email in re.findall(
            email_pattern,
            body_text
        ):
            emails.add(email)

        return {
            "success": True,
            "url": final_url,
            "status_code": response.status_code,
            "title": title,
            "description": description,
            "headings": headings,
            "links": links,
            "emails": sorted(emails),
            "text": body_text,
        }

    except Exception as e:

        return {
            "success": False,
            "url": url,
            "error": str(e),
        }


def extract_social_profiles(external_links):
    """Identify official social profiles found on the website."""

    profiles = {
        "facebook": [],
        "instagram": [],
        "linkedin": [],
        "youtube": [],
        "tiktok": [],
        "other": [],
    }

    for url in external_links:

        lower = url.lower()

        if "facebook.com" in lower:
            profiles["facebook"].append(url)

        elif "instagram.com" in lower:
            profiles["instagram"].append(url)

        elif "linkedin.com" in lower:
            profiles["linkedin"].append(url)

        elif "youtube.com" in lower or "youtu.be" in lower:
            profiles["youtube"].append(url)

        elif "tiktok.com" in lower:
            profiles["tiktok"].append(url)

        elif not lower.startswith("mailto:"):
            profiles["other"].append(url)

    return profiles


def extract_business_signals(combined_text):
    """Extract useful business information from the website text."""

    text = combined_text.lower()

    signals = {
        "established_year": None,
        "services": [],
        "locations": [],
        "service_areas": [],
        "has_free_quote": False,
        "has_contact_form": False,
        "has_phone": False,
        "has_email": False,
        "has_portfolio_language": False,
        "has_testimonial_language": False,
        "has_case_study_language": False,
        "has_reviews_language": False,
    }

    year_match = re.search(
        r"(?:established|since|founded|started)\D{0,20}(19\d{2}|20\d{2})",
        text,
    )

    if year_match:
        signals["established_year"] = int(
            year_match.group(1)
        )

    service_terms = [
        "services",
        "products",
        "solutions",
        "design",
        "printing",
        "manufacturing",
        "installation",
        "consulting",
        "repairs",
        "maintenance",
    ]

    for term in service_terms:

        if term in text:
            signals["services"].append(term)

    location_terms = [
        "south africa",
        "cape town",
        "durban",
        "johannesburg",
        "pretoria",
        "margate",
        "uvongo",
        "port shepstone",
    ]

    for term in location_terms:

        if term in text:
            signals["locations"].append(term)

    service_area_terms = [
        "local",
        "national",
        "international",
        "nationwide",
        "africa",
    ]

    for term in service_area_terms:

        if term in text:
            signals["service_areas"].append(term)

    signals["has_free_quote"] = (
        "free quote" in text
        or "free quotation" in text
    )

    signals["has_contact_form"] = (
        "name (required)" in text
        and "email (required)" in text
    )

    signals["has_phone"] = bool(
        re.search(
            r"\b0\d{2}\s*[-.]?\s*\d{3}\s*[-.]?\s*\d{4}\b",
            text,
        )
    )

    signals["has_email"] = bool(
        re.search(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            text,
        )
    )

    signals["has_portfolio_language"] = any(
        word in text
        for word in [
            "gallery",
            "portfolio",
            "our work",
            "projects",
            "completed projects",
        ]
    )

    signals["has_testimonial_language"] = any(
        word in text
        for word in [
            "testimonial",
            "testimonials",
            "client feedback",
        ]
    )

    signals["has_case_study_language"] = any(
        word in text
        for word in [
            "case study",
            "case studies",
        ]
    )

    signals["has_reviews_language"] = any(
        word in text
        for word in [
            "reviews",
            "review us",
            "customer reviews",
            "google reviews",
        ]
    )

    return signals


def research_website(start_url):
    """
    Crawl a business website and return structured research data.
    """

    if not start_url.startswith(
        ("http://", "https://")
    ):
        start_url = "https://" + start_url

    start_url = normalize_url(start_url)

    domain = urlparse(start_url).netloc

    queue = deque([start_url])

    visited = set()

    pages = []

    external_links = set()

    all_emails = set()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
    }

    with httpx.Client(
        headers=headers,
        timeout=20,
    ) as client:

        while queue and len(pages) < MAX_PAGES:

            url = queue.popleft()

            normalized_url = normalize_url(url)

            if normalized_url in visited:
                continue

            if is_ignored_url(normalized_url):
                continue

            visited.add(normalized_url)

            print(
                f"Researching {len(pages) + 1}/{MAX_PAGES}: {normalized_url}"
            )

            page = extract_page(
                normalized_url,
                client,
            )

            if not page.get("success"):
                continue

            pages.append(page)

            for email in page.get(
                "emails",
                []
            ):
                all_emails.add(email)

            for link in page.get(
                "links",
                []
            ):

                link_url = link["url"]

                if link["internal"]:

                    if (
                        link_url not in visited
                        and not is_ignored_url(link_url)
                    ):
                        queue.append(link_url)

                else:

                    if not link_url.startswith(
                        "mailto:"
                    ):
                        external_links.add(
                            link_url
                        )

            # Prioritise useful pages.
            priority_urls = []

            for link in page.get(
                "links",
                []
            ):

                if (
                    link["internal"]
                    and link["priority"]
                    and link["url"] not in visited
                ):
                    priority_urls.append(
                        link["url"]
                    )

            for priority_url in reversed(
                priority_urls
            ):
                queue.appendleft(
                    priority_url
                )

    combined_text_parts = []

    for page in pages:

        combined_text_parts.append(
            f"PAGE: {page['url']}"
        )

        combined_text_parts.append(
            f"TITLE: {page.get('title', '')}"
        )

        combined_text_parts.append(
            f"DESCRIPTION: {page.get('description', '')}"
        )

        combined_text_parts.append(
            page.get("text", "")
        )

    combined_text = "\n".join(
        combined_text_parts
    )

    social_profiles = extract_social_profiles(
        sorted(external_links)
    )

    business_signals = extract_business_signals(
        combined_text
    )

    return {
        "success": True,
        "start_url": start_url,
        "domain": domain,
        "pages_researched": len(pages),
        "pages": pages,
        "emails": sorted(all_emails),
        "external_links": sorted(external_links),
        "social_profiles": social_profiles,
        "business_signals": business_signals,
        "combined_text": combined_text,
    }