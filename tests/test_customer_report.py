import pytest

from customer_report import to_customer_report


def sample_audit():
    return {
        "success": True,
        "business_name": "Example Bakery",
        "website": "example-bakery.co.za",
        "location": "Cape Town",
        "generated_at": "2026-11-14T12:00:00Z",
        "executive_summary": "Internal score 42/100. Full scorecard and private analysis.",
        "scorecard": {"assessed_score": 42, "categories": [{"score": 5, "name": "Website structure"}]},
        "package_suggestion": {"estimated_price_zar": 8000, "monthly_retainer_options": ["private plan"]},
        "keyword_research": {"terms": ["private target phrase"]},
        "crm_notes": "Do not publish this note.",
        "opportunities": [
            {
                "priority": "High",
                "title": "Fix on-page SEO basics",
                "evidence": "2 of 4 crawled pages have no H1 heading; 3 of 8 images have no alt text.",
                "action": "Give every key page one clear H1, write a unique meta description, and add descriptive alt text to project images.",
                "service_fit": "private service packaging",
            },
            {
                "priority": "Medium",
                "title": "Verify Google Business Profile and local visibility",
                "evidence": "No Google/Maps candidate was surfaced by this search; this does not prove that no profile exists.",
                "action": "Confirm the correct profile manually and check its hours, contact details and service area.",
            },
        ],
    }


def test_report_keeps_true_findings_and_explains_them_for_customers():
    report = to_customer_report(sample_audit())
    assert report["businessName"] == "Example Bakery"
    assert report["website"] == "https://example-bakery.co.za"
    assert len(report["findings"]) == 2
    finding = report["findings"][0]
    assert finding["priority"] == "Start here"
    assert "main page heading" in finding["whatWeFound"]
    assert "2 of 4 crawled pages" in finding["whatWeFound"]
    assert "3 of 8 images" in finding["whatWeFound"]
    assert "why it may matter" in report["summary"]
    assert "search engines" in finding["whyItMatters"]
    assert "search-result summaries" in finding["whatToDo"]
    assert "42/100" not in str(report)


def test_unconfirmed_listing_is_not_presented_as_proof_of_absence():
    finding = to_customer_report(sample_audit())["findings"][1]
    assert "not proof" in finding["whyItMatters"]
    assert "does not prove" in finding["whatWeFound"]


def test_website_security_finding_uses_clear_language_and_keeps_the_observed_count():
    audit = sample_audit()
    audit["opportunities"] = [{
        "priority": "High",
        "title": "Move website to HTTPS",
        "evidence": "4 of 4 pages were served over plain http:// and the audit never ended on an https:// address.",
        "action": "Ask the host whether a free SSL certificate is available, install it, then redirect every http:// address to its https:// version.",
        "service_fit": "internal only",
    }]
    finding = to_customer_report(audit)["findings"][0]
    assert "secure version" in finding["title"].lower()
    assert "4 of 4 pages" in finding["whatWeFound"]
    assert "unencrypted website addresses" in finding["whatWeFound"]
    assert "the secure website address" in finding["whatWeFound"]
    assert "free website security certificate" in finding["whatToDo"].lower()
    assert "make the secure version the default for every page" in finding["whatToDo"].lower()
    assert "http://" not in str(finding)
    assert "ssl" not in str(finding).lower()


def test_internal_pricing_scores_keywords_and_crm_notes_are_excluded():
    report = to_customer_report(sample_audit())
    serialized = str(report)
    assert "scorecard" not in serialized.lower()
    assert "estimated_price" not in serialized
    assert "8000" not in serialized
    assert "private target phrase" not in serialized
    assert "private service packaging" not in serialized
    assert "Do not publish this note" not in serialized
    assert "assessed_score" not in serialized


def test_rejects_missing_business_or_unsafe_website():
    with pytest.raises(ValueError):
        to_customer_report({"success": True, "business_name": "Example", "website": "javascript:alert(1)"})
    with pytest.raises(ValueError):
        to_customer_report({"success": True, "website": "https://example.co.za", "opportunities": []})
