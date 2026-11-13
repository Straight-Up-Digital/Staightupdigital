"""
Business Opportunity Auditor
External Web Research Engine
"""

from ddgs import DDGS


def clean_url(url):
    if not url:
        return ""

    return url.strip()


def search_web(query, max_results=8):
    """
    Perform a public web search and return structured results.
    """

    results = []

    try:
        with DDGS() as ddgs:

            search_results = ddgs.text(
                query,
                max_results=max_results
            )

            for result in search_results:

                results.append({
                    "title": result.get("title", ""),
                    "url": clean_url(result.get("href", "")),
                    "snippet": result.get("body", "")
                })

    except Exception as error:

        print()
        print("SEARCH ERROR:", error)

        return {
            "success": False,
            "query": query,
            "error": str(error),
            "results": []
        }

    return {
        "success": True,
        "query": query,
        "results": results
    }


def identify_social_profiles(results):

    platforms = {
        "facebook.com": "Facebook",
        "instagram.com": "Instagram",
        "linkedin.com": "LinkedIn",
        "youtube.com": "YouTube",
        "tiktok.com": "TikTok"
    }

    profiles = {}

    for result in results:

        url = result.get("url", "").lower()

        for domain, platform in platforms.items():

            if domain in url:

                if platform not in profiles:

                    profiles[platform] = {
                        "platform": platform,
                        "url": result.get("url", ""),
                        "title": result.get("title", ""),
                        "snippet": result.get("snippet", "")
                    }

    return profiles


def find_business_results(business_name, location=""):

    query = f'"{business_name}"'

    if location:
        query += f' "{location}"'

    return search_web(
        query,
        max_results=10
    )


def find_google_results(business_name, location=""):

    query = f'"{business_name}"'

    if location:
        query += f' "{location}"'

    query += " Google reviews"

    return search_web(
        query,
        max_results=10
    )


def find_review_results(business_name, location=""):

    query = f'"{business_name}" reviews'

    if location:
        query += f' "{location}"'

    return search_web(
        query,
        max_results=10
    )


def find_competitors(
    business_name,
    industry="",
    location=""
):

    query = ""

    if industry:
        query += industry + " "

    query += "companies"

    if location:
        query += " " + location

    results = search_web(
        query,
        max_results=10
    )

    filtered_results = []

    business_lower = business_name.lower()

    for result in results.get(
        "results",
        []
    ):

        combined = (
            result.get("title", "")
            + " "
            + result.get("snippet", "")
        ).lower()

        if business_lower not in combined:

            filtered_results.append(
                result
            )

    results["results"] = filtered_results[:5]

    return results


def research_external(
    business_name,
    location="",
    industry=""
):

    print()
    print("=" * 60)
    print("EXTERNAL WEB RESEARCH")
    print("=" * 60)

    print()
    print("Business:", business_name)

    if location:
        print("Location:", location)

    if industry:
        print("Industry:", industry)

    print()
    print("Searching business...")

    business_results = find_business_results(
        business_name,
        location
    )

    print(
        "Business results found:",
        len(business_results.get("results", []))
    )

    print()
    print("Searching Google and reviews...")

    google_results = find_google_results(
        business_name,
        location
    )

    print(
        "Google results found:",
        len(google_results.get("results", []))
    )

    print()
    print("Searching review sources...")

    review_results = find_review_results(
        business_name,
        location
    )

    print(
        "Review results found:",
        len(review_results.get("results", []))
    )

    print()
    print("Identifying social profiles...")

    all_results = (
        business_results.get("results", [])
        + google_results.get("results", [])
        + review_results.get("results", [])
    )

    social_profiles = identify_social_profiles(
        all_results
    )

    print(
        "Social profiles found:",
        len(social_profiles)
    )

    print()
    print("Searching competitors...")

    competitor_results = find_competitors(
        business_name,
        industry,
        location
    )

    print(
        "Competitor results found:",
        len(competitor_results.get("results", []))
    )

    return {
        "business": {
            "name": business_name,
            "location": location,
            "industry": industry
        },

        "business_results": business_results,

        "google_results": google_results,

        "review_results": review_results,

        "social_profiles": social_profiles,

        "competitor_results": competitor_results
    }


def print_results(title, results):

    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    items = results.get(
        "results",
        []
    )

    if not items:

        print()
        print("No results found.")
        return

    for index, result in enumerate(
        items,
        start=1
    ):

        print()
        print(
            f"{index}. {result.get('title', '')}"
        )

        print(
            result.get('url', '')
        )

        print(
            result.get('snippet', '')[:400]
        )


def print_external_research(report):

    print_results(
        "BUSINESS SEARCH RESULTS",
        report["business_results"]
    )

    print()
    print("=" * 60)
    print("SOCIAL PROFILES")
    print("=" * 60)

    profiles = report["social_profiles"]

    if profiles:

        for platform, data in profiles.items():

            print()
            print(platform)
            print(data["url"])
            print(data["title"])

    else:

        print()
        print("No social profiles identified.")

    print_results(
        "GOOGLE / REVIEW RESULTS",
        report["google_results"]
    )

    print_results(
        "REVIEW RESULTS",
        report["review_results"]
    )

    print_results(
        "COMPETITOR RESULTS",
        report["competitor_results"]
    )


if __name__ == "__main__":

    business = input(
        "Business name: "
    ).strip()

    location = input(
        "Location: "
    ).strip()

    industry = input(
        "Industry: "
    ).strip()

    if not business:

        print(
            "Business name is required."
        )

    else:

        report = research_external(
            business,
            location,
            industry
        )

        print_external_research(
            report
        )