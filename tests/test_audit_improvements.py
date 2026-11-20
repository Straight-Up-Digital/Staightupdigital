from types import SimpleNamespace

import audit_presence
import audit_priority
import audit_serp
import audit_signals
import audit_speed
import audit_urls
from client_pdf import build_client_pdf


def test_url_canonicalization_deduplicates_scheme_www_index_and_tracking():
    first = "http://www.Example.co.za/services/index.html?utm_source=mail&x=1"
    second = "https://example.co.za/services/?x=1"

    assert audit_urls.canonical_key(first) == audit_urls.canonical_key(second)
    assert audit_urls.normalize_url(first) == "http://www.example.co.za/services?x=1"
    pages = [
        {"url": first, "canonical_url": first},
        {"url": second, "canonical_url": second},
    ]
    assert len(audit_urls.dedupe_pages(pages)) == 1


def test_same_host_canonical_link_is_used_but_external_canonical_is_ignored():
    page = "https://www.example.co.za/services"
    assert audit_urls.canonical_page_url(page, "/services/") == "https://www.example.co.za/services"
    assert (
        audit_urls.canonical_page_url(page, "https://other.example/page")
        == page
    )
    assert audit_urls.safe_display_url(
        "https://user:secret@example.co.za/path?token=hidden#top"
    ) == "https://example.co.za/path"


def test_priority_is_blocker_gated_and_groups_low_technical_findings():
    findings = [
        {"priority": "High", "title": "Move the website to HTTPS", "action": "Install TLS."},
        {"priority": "High", "title": "Verify Google Business Profile", "action": "Check the profile."},
        {"priority": "Low", "title": "Add structured data", "action": "Add schema markup."},
        {"priority": "Medium", "title": "Rebuild website content", "action": "Rebuild the site."},
    ]
    context = {
        "pages": [{"technical": {"https": False}}],
        "google": {"matched": False},
    }

    result = audit_priority.finalize_findings(findings, context)

    assert result[0]["priority"] == "High"
    assert result[0]["blocker"] is True
    assert next(item for item in result if item["title"] == "Verify Google Business Profile")["priority"] == "Medium"
    tidy = next(item for item in result if item["title"] == "Technical tidy-up")
    assert tidy["items"][0]["title"] == "Add structured data"
    assert next(item for item in result if item["title"] == "Rebuild website content")["priority"] == "Low"
    assert audit_priority.priority_counts(result) == {"High": 1, "Medium": 1, "Low": 2}


def test_missing_contact_route_and_unusable_mobile_performance_are_confirmed_blockers():
    findings = [
        {"title": "Improve website enquiry path"},
        {"title": "Speed up the website on phones"},
    ]
    result = audit_priority.finalize_findings(
        findings,
        {
            "signals": {"has_contact_route": False},
            "speed": {"mobile": {"scores": {"performance": 39}}},
        },
    )

    assert [item["priority"] for item in result] == ["High", "High"]
    assert all(item["blocker"] for item in result)


def test_conversion_trust_email_and_nap_signals_are_separate():
    html = """
    <a href="tel:+27460001111">Call</a>
    <a href="https://wa.me/27460001111">WhatsApp</a>
    <a href="/privacy-policy">Privacy</a>
    <script src="https://www.googletagmanager.com/gtag/js?id=G-ABC123"></script>
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC"></script>
    <script>fbq('init', '123');</script>
    """
    signals = audit_signals.detect_site_signals(html)

    assert signals["tel_link"] and signals["whatsapp_link"] and signals["privacy_link"]
    assert signals["ga4"] and signals["gtm"] and signals["meta_pixel"]
    assert signals["has_contact_route"]

    posture = audit_signals.email_posture(
        ["10 mx.zoho.com."],
        ["v=spf1 include:zoho.com ~all"],
        ["v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com"],
        True,
    )
    assert posture["mx_provider"] == "Zoho Mail"
    assert posture["spf_present"] is True
    assert posture["dmarc_policy"] == "quarantine"
    assert posture["dmarc_strength"] == "moderate"

    comparison = audit_signals.compare_nap(
        {"name": "Acme Heating", "phone": "046 000 1111", "address": "Main St, Port Alfred"},
        {"name": "Acme Heating", "phone": "+27 46 999 1111", "address": "Main Street, Port Alfred"},
    )
    assert comparison["name"]["status"] == "match"
    assert comparison["phone"]["status"] == "different"
    assert comparison["address"]["status"] == "different"
    assert audit_signals.compare_nap(
        {"phone": "046 000 1111"},
        {"phone": "+27 46 000 1111"},
    )["phone"]["status"] == "match"


def test_profile_presence_only_verifies_site_links_or_name_and_phone_matches():
    profiles = audit_presence.discover_profiles(
        ["https://www.facebook.com/acme-heating"],
        [
            {
                "url": "https://www.instagram.com/acmeheating/",
                "title": "Acme Heating",
                "snippet": "046 000 1111",
                "provider": "Serper.dev",
            },
            {
                "url": "https://www.linkedin.com/company/other/",
                "title": "Other business",
                "snippet": "046 123 0000",
            },
        ],
        "Acme Heating",
        "046 000 1111",
    )

    by_platform = {profile["platform"]: profile for profile in profiles}
    assert by_platform["Facebook"]["status"] == "Verified"
    assert by_platform["Instagram"]["status"] == "Verified"
    assert by_platform["LinkedIn"]["status"] == "Candidate, not verified"


def test_serp_search_falls_back_to_serper_and_redacts_provider_secrets(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "serpapi-secret")
    monkeypatch.setenv("SERPER_API_KEY", "serper-secret")
    cache = {}
    used = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "organic": [
                    {
                        "title": "Example competitor",
                        "link": "https://competitor.example/",
                        "snippet": "A local service.",
                    }
                ]
            }

    class Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            raise RuntimeError("request failed with serpapi-secret")

        def post(self, *args, **kwargs):
            return Response()

    result = audit_serp.web_search(
        "plumber Port Alfred",
        5,
        cache_get=lambda key, age: cache.get(key),
        cache_put=lambda key, value: cache.__setitem__(key, value),
        usage_reserve=lambda kind, period, limit: used.append(kind) or True,
        monthly_budget=100,
        client_factory=Client,
        provider_order="serpapi,serper,ddgs",
    )

    assert result["provider"] == "Serper.dev", result.get("provider_errors")
    assert result["results"][0]["title"] == "Example competitor"
    assert "serpapi-secret" not in str(result["provider_errors"])
    assert used == ["serpapi", "serper"]


def test_serp_search_uses_searxng_before_paid_fallback_when_serpapi_allowance_is_spent(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "serpapi-secret")
    monkeypatch.setenv("SERPER_API_KEY", "serper-secret")
    monkeypatch.delenv("SERP_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
    cache = {}
    used = []
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "Example local service",
                        "url": "https://example.co.za/",
                        "content": "A local business website.",
                    }
                ]
            }

    class Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None, **kwargs):
            requests.append((url, params))
            return Response()

        def post(self, *args, **kwargs):
            raise AssertionError("Paid Serper should not run after SearXNG returns results")

    result = audit_serp.web_search(
        "plumber Port Alfred",
        5,
        cache_get=lambda key, age: cache.get(key),
        cache_put=lambda key, value: cache.__setitem__(key, value),
        usage_reserve=lambda kind, period, limit: used.append(kind) or False,
        monthly_budget=100,
        client_factory=Client,
    )

    assert result["provider"] == "SearXNG"
    assert result["results"][0]["snippet"] == "A local business website."
    assert requests == [
        (
            "http://127.0.0.1:8080/search",
            {"q": "plumber Port Alfred", "format": "json", "pageno": 1},
        )
    ]
    assert used == ["serpapi"]


def test_searxng_refuses_cleartext_remote_endpoints_and_falls_through(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://search.example.org")

    class Client:
        def __init__(self, timeout=None):
            raise AssertionError("An insecure remote endpoint must be rejected before connecting")

    result = audit_serp.web_search(
        "plumber Port Alfred",
        5,
        cache_get=lambda key, age: None,
        cache_put=lambda key, value: None,
        usage_reserve=lambda kind, period, limit: True,
        monthly_budget=100,
        client_factory=Client,
        provider_order="searxng",
    )

    assert result["provider"] == "Unavailable"
    assert "must use HTTPS" in result["provider_errors"][0]["error"]


def test_empty_searxng_results_continue_to_ddgs(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setenv("SEARXNG_BASE_URL", "https://search.example.org")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return Response()

    class DDGS:
        def text(self, query, max_results):
            return [
                {
                    "title": "Fallback result",
                    "href": "https://fallback.example/",
                    "body": "Found through the free fallback.",
                }
            ]

    result = audit_serp.web_search(
        "plumber Port Alfred",
        5,
        cache_get=lambda key, age: None,
        cache_put=lambda key, value: None,
        usage_reserve=lambda kind, period, limit: True,
        monthly_budget=100,
        ddgs_class=DDGS,
        client_factory=Client,
        provider_order="searxng,ddgs",
    )

    assert result["provider"] == "DDGS"
    assert result["results"][0]["title"] == "Fallback result"
    assert result["provider_errors"] == [
        {"provider": "SearXNG", "error": "No results returned."}
    ]


def test_pagespeed_mobile_desktop_and_crux_are_parsed_without_live_requests(monkeypatch):
    monkeypatch.setenv("PAGESPEED_ENABLED", "1")
    monkeypatch.setenv("PAGESPEED_CRUX", "1")
    payload = {
        "lighthouseResult": {
            "categories": {
                "performance": {"score": 0.39},
                "accessibility": {"score": 0.9},
                "best-practices": {"score": 0.8},
                "seo": {"score": 1.0},
            },
            "audits": {
                "largest-contentful-paint": {"displayValue": "4.2 s"},
                "total-blocking-time": {"displayValue": "500 ms"},
                "cumulative-layout-shift": {"displayValue": "0.12"},
            },
        },
        "loadingExperience": {
            "id": "https://example.co.za",
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2500},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 200},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 120},
            },
        },
    }

    class Response:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(audit_speed.httpx, "get", lambda *args, **kwargs: Response())

    mobile = audit_speed.fetch_pagespeed("https://example.co.za", "mobile")
    desktop = audit_speed.fetch_pagespeed("https://example.co.za", "desktop")

    assert mobile["score"] == 39
    assert desktop["strategy"] == "desktop"
    assert mobile["metrics"]["lcp"] == "4.2 s"
    assert mobile["crux"]["inp"]["source"] == "field"
    assert mobile["crux"]["cls"]["p75"] == 0.12


def test_local_speed_fallback_reports_real_html_and_redirect_metrics(monkeypatch):
    monkeypatch.setenv("PAGESPEED_ENABLED", "0")

    class FakeClient:
        event_hooks = {"response": []}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    response = SimpleNamespace(
        text='<meta name="viewport"><img><img alt="photo">',
        content=b'<meta name="viewport"><img><img alt="photo">',
        url="https://example.co.za/",
        history=[
            SimpleNamespace(
                request=SimpleNamespace(url="http://example.co.za/"),
                url="http://www.example.co.za/",
            ),
            SimpleNamespace(
                request=SimpleNamespace(url="http://www.example.co.za/"),
                url="https://example.co.za/",
            ),
        ],
    )

    def get_response(client, url, max_bytes):
        for hook in client.event_hooks["response"]:
            hook(response)
        return response

    measured = audit_speed.measure_website(
        "http://example.co.za/",
        {},
        lambda **kwargs: FakeClient(),
        get_response,
        1024,
    )

    assert measured["mobile"]["error"]
    assert measured["desktop"]["error"]
    assert measured["local"]["image_count"] == 2
    assert measured["local"]["images_missing_alt"] == 1
    assert measured["local"]["viewport_present"] is True
    assert measured["local"]["https_redirect"] is True
    assert len(measured["local"]["https_redirect_chain"]) == 2


def test_customer_pdf_renders_pillars_and_shared_priority_counts():
    audit = {
        "business_name": "Example Services",
        "website": "https://example.co.za",
        "generated_at": "2026-11-20T12:00:00",
        "scorecard": {"assessed_score": 70, "categories": []},
        "opportunities": [
            {
                "priority": "Medium",
                "impact": "Medium",
                "effort": "Low",
                "title": "Improve contact details",
                "evidence": "Website and profile phone numbers differ.",
                "action": "Use the same primary phone number in both places.",
            }
        ],
        "priority_counts": {"High": 0, "Medium": 1, "Low": 0},
        "external_research": {"searches": {}, "google_places": {}},
        "pagespeed": {"local": {}},
        "site_signals": {},
        "nap_comparison": {},
        "profile_presence": [],
        "package_suggestion": {},
    }

    pdf = build_client_pdf(audit, {"show_price": False})

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
