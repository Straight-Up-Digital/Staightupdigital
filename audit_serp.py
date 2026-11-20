"""Budget-aware Google search provider fallback for audit research."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import quote

import httpx


_PROVIDERS = {"serpapi", "serper", "ddgs"}
_SERPAPI_URL = "https://serpapi.com/search.json"
_SERPER_URL = "https://google.serper.dev/search"


def _normalize_results(results: Any, limit: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for rank, result in enumerate(results or [], start=1):
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or result.get("link") or result.get("href") or "").strip()
        if not url:
            continue
        normalized.append(
            {
                "rank": rank,
                "title": str(result.get("title") or "").strip(),
                "url": url,
                "snippet": str(
                    result.get("snippet")
                    or result.get("body")
                    or result.get("description")
                    or ""
                ).strip(),
            }
        )
        if len(normalized) >= limit:
            break
    return normalized


def _provider_order(value: str | None) -> list[str]:
    configured = [
        item.strip().lower()
        for item in (value or "serpapi,serper,ddgs").replace(";", ",").split(",")
        if item.strip().lower() in _PROVIDERS
    ]
    return list(dict.fromkeys(configured)) or ["serpapi", "serper", "ddgs"]


def _safe_error(exc: Exception, api_key: str = "") -> str:
    message = str(exc)
    if api_key:
        message = message.replace(api_key, "[redacted]")
        message = message.replace(quote(api_key, safe=""), "[redacted]")
    return message


def _query_provider(
    provider: str,
    query: str,
    limit: int,
    api_key: str,
    client: Any,
) -> list[dict[str, Any]]:
    if provider == "serpapi":
        response = client.get(
            _SERPAPI_URL,
            params={
                "engine": "google",
                "q": query,
                "num": limit,
                "api_key": api_key,
            },
        )
    elif provider == "serper":
        response = client.post(
            _SERPER_URL,
            headers={"X-API-KEY": api_key},
            json={"q": query, "num": limit},
        )
    else:
        raise ValueError(f"Unsupported HTTP search provider: {provider}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{provider} returned an invalid response.")
    if payload.get("error"):
        raise RuntimeError(f"{provider} error: {payload['error']}")
    return _normalize_results(payload.get("organic_results") or payload.get("organic"), limit)


def web_search(
    query: str,
    max_results: int,
    *,
    cache_get: Callable[[str, int], Any],
    cache_put: Callable[[str, dict[str, Any]], None],
    usage_reserve: Callable[[str, str, int], bool],
    monthly_budget: int,
    ddgs_class: Callable[[], Any] | None = None,
    client_factory: Callable[..., Any] = httpx.Client,
    provider_order: str | None = None,
) -> dict[str, Any]:
    query = " ".join(str(query or "").split())
    limit = max(1, min(int(max_results or 1), 20))
    cache_key = f"web_search:{query.casefold()}:{limit}"
    cached = cache_get(cache_key, 1)
    if isinstance(cached, dict) and isinstance(cached.get("results"), list):
        response = dict(cached)
        response["cached"] = True
        return response

    errors: list[dict[str, str]] = []
    now = datetime.now()
    order = _provider_order(
        provider_order if provider_order is not None else os.getenv("SERP_PROVIDER_ORDER")
    )
    keys = {
        "serpapi": os.getenv("SERPAPI_API_KEY", "").strip(),
        "serper": os.getenv("SERPER_API_KEY", "").strip(),
    }
    for provider in order:
        if provider == "ddgs":
            if ddgs_class is None:
                errors.append({"provider": "DDGS", "error": "DDGS search is unavailable."})
                continue
            try:
                search_client = ddgs_class()
                try:
                    raw = list(search_client.text(query, max_results=limit))
                finally:
                    close = getattr(search_client, "close", None)
                    if callable(close):
                        close()
                results = _normalize_results(raw, limit)
                response = {
                    "query": query,
                    "date": date.today().isoformat(),
                    "provider": "DDGS",
                    "results": results,
                    "provider_errors": errors,
                    "cached": False,
                }
                cache_put(cache_key, response)
                return response
            except Exception as exc:
                errors.append({"provider": "DDGS", "error": _safe_error(exc)})
                continue

        api_key = keys.get(provider, "")
        if not api_key:
            continue
        usage_kind = "serpapi" if provider == "serpapi" else "serper"
        try:
            if not usage_reserve(usage_kind, now.strftime("%Y-%m"), monthly_budget):
                errors.append({"provider": provider.title(), "error": "Monthly search allowance reached."})
                continue
            with client_factory(timeout=20) as client:
                results = _query_provider(provider, query, limit, api_key, client)
            response = {
                "query": query,
                "date": date.today().isoformat(),
                "provider": "SerpApi" if provider == "serpapi" else "Serper.dev",
                "results": results,
                "provider_errors": errors,
                "cached": False,
            }
            cache_put(cache_key, response)
            return response
        except Exception as exc:
            errors.append({"provider": provider.title(), "error": _safe_error(exc, api_key)})

    response = {
        "query": query,
        "date": date.today().isoformat(),
        "provider": "Unavailable",
        "results": [],
        "provider_errors": errors,
        "cached": False,
        "error": "No configured search provider returned results.",
    }
    return response
