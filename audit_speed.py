"""PageSpeed Insights and safe local website performance checks."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
import audit_urls


PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_CATEGORY_MAP = (
    ("accessibility", "accessibility"),
    ("best_practices", "best-practices"),
    ("seo", "seo"),
)
_LAB_METRICS = (
    ("fcp", "first-contentful-paint"),
    ("lcp", "largest-contentful-paint"),
    ("inp", "interaction-to-next-paint"),
    ("tbt", "total-blocking-time"),
    ("cls", "cumulative-layout-shift"),
    ("speed_index", "speed-index"),
)
_CRUX_METRICS = {
    "LARGEST_CONTENTFUL_PAINT_MS": ("lcp", "ms"),
    "INTERACTION_TO_NEXT_PAINT": ("inp", "ms"),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("cls", ""),
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": ("ttfb", "ms"),
}


def _crux_field_data(payload: dict[str, Any]) -> dict[str, Any]:
    if os.getenv("PAGESPEED_CRUX", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return {}
    experience = payload.get("loadingExperience") or {}
    metrics = experience.get("metrics") or {}
    data = {}
    for api_name, (name, unit) in _CRUX_METRICS.items():
        metric = metrics.get(api_name) or {}
        percentile = metric.get("percentile")
        if percentile is None:
            continue
        value = float(percentile)
        if name == "cls":
            value = value / 1000 if value > 1 else value
        data[name] = {
            "p75": round(value, 3) if name == "cls" else int(value),
            "unit": unit,
            "source": "field",
            "category": metric.get("category"),
        }
    if data:
        data["origin"] = experience.get("id")
    return data


def fetch_pagespeed(url: str, strategy: str = "mobile") -> dict[str, Any]:
    """Run one PSI test; failures are explicit so the caller can use local checks."""
    if os.getenv("PAGESPEED_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"error": "PageSpeed Insights is disabled.", "strategy": strategy}
    params = [
        ("url", url),
        ("strategy", strategy),
        ("category", "performance"),
        ("category", "accessibility"),
        ("category", "best-practices"),
        ("category", "seo"),
    ]
    api_key = os.getenv("PAGESPEED_API_KEY", "").strip()
    if api_key:
        params.append(("key", api_key))
    try:
        response = httpx.get(PAGESPEED_URL, params=params, timeout=80, follow_redirects=True)
        if response.status_code == 429:
            return {
                "error": "Google's PageSpeed service is busy; the local checks remain available.",
                "strategy": strategy,
            }
        if response.status_code != 200:
            return {
                "error": f"PageSpeed returned HTTP {response.status_code}.",
                "strategy": strategy,
            }
        payload = response.json()
        if not isinstance(payload, dict):
            return {"error": "PageSpeed returned an invalid response.", "strategy": strategy}
        lighthouse = payload.get("lighthouseResult") or {}
        if not lighthouse:
            return {"error": "PageSpeed did not return Lighthouse data.", "strategy": strategy}
        categories = lighthouse.get("categories") or {}
        audits = lighthouse.get("audits") or {}
        performance = (categories.get("performance") or {}).get("score")
        scores = {
            name: round((categories.get(api_name) or {}).get("score") * 100)
            for name, api_name in _CATEGORY_MAP
            if (categories.get(api_name) or {}).get("score") is not None
        }
        metrics = {
            name: (audits.get(audit_name) or {}).get("displayValue")
            for name, audit_name in _LAB_METRICS
            if (audits.get(audit_name) or {}).get("displayValue")
        }
        return {
            "strategy": strategy,
            "score": round(performance * 100) if performance is not None else None,
            "scores": {
                "performance": round(performance * 100) if performance is not None else None,
                **scores,
            },
            "categories": scores,
            "metrics": metrics,
            "metric_source": "lab",
            "crux": _crux_field_data(payload),
            "tested_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        message = f"PageSpeed could not run ({type(exc).__name__})."
        return {"error": message, "strategy": strategy}


def _safe_url(url: str) -> str:
    return audit_urls.safe_display_url(url)


def _research_fallback(research: dict[str, Any] | None) -> dict[str, Any]:
    pages = (research or {}).get("pages") or []
    if not pages:
        return {}
    page = pages[0]
    technical = page.get("technical") or {}
    return {
        "html_size_bytes": technical.get("response_bytes"),
        "image_count": technical.get("image_count"),
        "images_missing_alt": technical.get("images_missing_alt"),
        "viewport_present": technical.get("viewport_present"),
        "https_redirect": technical.get("https_redirect"),
        "https_redirect_chain": technical.get("https_redirect_chain") or [],
        "source": "local estimate from crawl",
    }


def local_site_checks(
    url: str,
    research: dict[str, Any] | None,
    client_factory: Callable[..., Any],
    get_bounded_response: Callable[..., Any],
    max_response_bytes: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    response_marks: list[float] = []
    result: dict[str, Any] = {}
    try:
        with client_factory(timeout=20) as client:
            hooks = getattr(client, "event_hooks", None)
            if isinstance(hooks, dict):
                hooks.setdefault("response", []).append(
                    lambda response: response_marks.append(time.perf_counter())
                )
            response = get_bounded_response(client, url, max_response_bytes)
        finished = time.perf_counter()
        html = response.text or ""
        soup = BeautifulSoup(html, "html.parser")
        images = soup.find_all("img")
        initial_url = urlsplit(url)
        final_url = urlsplit(str(response.url))
        history = list(getattr(response, "history", []) or [])
        chain = [
            f"{_safe_url(str(item.request.url))} -> {_safe_url(str(item.url))}"
            for item in history
        ]
        result = {
            "response_time_ms": round((finished - started) * 1000),
            "ttfb_ms": (
                round((response_marks[0] - started) * 1000)
                if response_marks
                else round((finished - started) * 1000)
            ),
            "html_size_bytes": len(getattr(response, "content", b"") or b""),
            "image_count": len(images),
            "images_missing_alt": sum(
                not str(image.get("alt") or "").strip() for image in images
            ),
            "viewport_present": bool(
                soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
            ),
            "https_redirect": (
                initial_url.scheme.lower() == "http" and final_url.scheme.lower() == "https"
            ),
            "https_redirect_chain": chain,
            "source": "local estimate",
        }
    except Exception as exc:
        fallback = _research_fallback(research)
        if fallback:
            result = fallback
            result["error"] = f"Live local checks could not complete ({type(exc).__name__})."
        else:
            result = {
                "source": "local estimate",
                "error": f"Local website checks could not complete ({type(exc).__name__}).",
            }
    for key in ("response_time_ms", "ttfb_ms", "html_size_bytes"):
        if result.get(key) is not None:
            result[key] = int(result[key])
    return result


def measure_website(
    url: str,
    research: dict[str, Any] | None,
    client_factory: Callable[..., Any],
    get_bounded_response: Callable[..., Any],
    max_response_bytes: int,
) -> dict[str, Any]:
    mobile = fetch_pagespeed(url, "mobile")
    desktop = fetch_pagespeed(url, "desktop")
    local = local_site_checks(
        url, research, client_factory, get_bounded_response, max_response_bytes
    )
    mobile.setdefault("scores", {})
    desktop.setdefault("scores", {})
    for strategy in (mobile, desktop):
        metrics = strategy.get("metrics") or {}
        strategy["metric_sources"] = {name: "lab" for name in metrics}
        strategy["metric_sources"].update(
            {name: "field" for name in (strategy.get("crux") or {}) if name != "origin"}
        )
    if mobile.get("error"):
        mobile["fallback_source"] = local.get("source", "local estimate")
    if desktop.get("error"):
        desktop["fallback_source"] = local.get("source", "local estimate")
    return {
        "mobile": mobile,
        "desktop": desktop,
        "crux": mobile.get("crux") or desktop.get("crux") or {},
        "local": local,
    }
