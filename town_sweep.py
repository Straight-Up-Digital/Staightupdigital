"""
Straight Up Digital - town sweep

Give it a South African town. It asks Google Maps (through your free SerpApi key) for local
businesses in a list of trades, removes duplicates, and splits them into:

    A  businesses with their own website listed on Google
    B  businesses with no website (or only a Facebook/Instagram page, or only a directory page)

Results go to reports/sweeps/ as JSON + CSV, in the same shape the CRM import already reads.

Usage (from the business_auditor folder, with the .venv active and SERPAPI_API_KEY set):

    python town_sweep.py "Port Alfred"
    python town_sweep.py "Port Alfred" --trades plumber,electrician,"hair salon"
    python town_sweep.py "Port Alfred" --trades all --max-searches 25
    python town_sweep.py "Port Alfred" --dry-run          (shows the plan, spends nothing)
    python town_sweep.py "Port Alfred" --import-crm       (also loads the leads into the CRM)
    python town_sweep.py "Port Alfred" --osm              (adds free OpenStreetMap results)

Each Google Maps search costs 1 of your free monthly searches and returns up to about 20
businesses. Results are cached for 30 days, so repeating a sweep costs nothing.

Honest limits: "no website on Google" is a strong hint, not proof. Check each lead by hand
before you pitch it. Contact details come from public Google listings, so follow the
direct-marketing rules that apply to you (POPIA) and always offer an opt-out.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import random
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from security import load_environment, active_tenant_id, tenant_context
from config_loader import env
from security_schema import eligible_for_crm
from resilient import retry_call

__version__ = "1.0"

HERE = Path(__file__).resolve().parent
load_environment()
REPORT_DIR = HERE / "reports"
SWEEP_DIR = REPORT_DIR / "sweeps"
CACHE_DIR = REPORT_DIR / "cache"
USAGE_PATH = REPORT_DIR / "town_sweep_usage.json"
_DAILY_USAGE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)

SERPAPI_URL = env("SERPAPI_BASE_URL", "https://serpapi.com").rstrip("/") + "/search.json"
ACCOUNT_URL = env("SERPAPI_BASE_URL", "https://serpapi.com").rstrip("/") + "/account.json"
OVERPASS_URL = env("OVERPASS_API_URL", "https://overpass-api.de/api/interpreter")

# Default trades: the kinds of local business that most often need a website or a refresh.
DEFAULT_TRADES = [
    "plumber", "electrician", "builder", "painter", "roofing", "hair salon", "beauty salon",
    "restaurant", "guest house", "mechanic", "garden services", "cleaning services",
]
ALL_TRADES = DEFAULT_TRADES + [
    "panel beater", "pest control", "security company", "accountant", "estate agent", "dentist",
    "veterinarian", "gym", "hardware store", "pool services", "air conditioning", "solar installer",
    "carpenter", "tiler", "photographer", "caterer", "florist", "tyre shop", "driving school",
    "tutor", "physiotherapist", "attorney", "printing", "signage",
]

# Websites that are not the business's own site.
SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "linkedin.com", "tiktok.com", "x.com",
                "twitter.com", "youtube.com", "pinterest.", "wa.me", "whatsapp.com", "linktr.ee")
DIRECTORY_HOSTS = ("yellowpages", "brabys", "cylex", "hotfrog", "snupit", "tripadvisor", "yelp.",
                   "gumtree", "findit", "trustpilot.com", "hellopeter.com", "booking.com", "airbnb.",
                   "lekkeslaap", "nightsbridge", "wikipedia.org", "google.com", "g.page", "business.site",
                   "wixsite.com/", "mapquest", "zoominfo", "dnb.com", "bizcommunity", "yep.co.za")


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def host_of(url: str) -> str:
    try:
        host = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    except Exception:
        return ""
    return host.removeprefix("www.")


def classify_website(url: str) -> tuple[str, str]:
    """Return (status, detail). status is one of the two CRM statuses."""
    url = clean(url)
    if not url:
        return "no_website_surfaced_in_search", "no_website_listed"
    lowered = url.lower()
    if any(h in lowered for h in SOCIAL_HOSTS):
        return "no_website_surfaced_in_search", "social_page_only"
    if any(h in lowered for h in DIRECTORY_HOSTS):
        return "no_website_surfaced_in_search", "directory_page_only"
    return "website_found_in_search_unverified", "own_website_listed"


def norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", "", clean(name).lower())
    n = re.sub(r"\b(pty|ltd|cc|the|and|co)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", clean(phone))
    if digits.startswith("27"):
        digits = "0" + digits[2:]
    return digits


# ---------------------------------------------------------------- budget and cache

def month() -> str:
    return datetime.now().strftime("%Y-%m")


@contextmanager
def _usage_file_lock():
    with _DAILY_USAGE_LOCK:
        lock_path = USAGE_PATH.with_name(f"{USAGE_PATH.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_usage_data() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Town sweep usage data must be a JSON object.")
    return data


def _write_usage_data(data: dict) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = USAGE_PATH.with_name(
        f"{USAGE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp_path, USAGE_PATH)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def usage_get() -> int:
    try:
        return int(json.loads(USAGE_PATH.read_text(encoding="utf-8")).get(month(), 0))
    except Exception:
        return 0


def daily_usage_get(town: str) -> int:
    try:
        data=json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return int(data.get("daily",{}).get(datetime.now().strftime("%Y-%m-%d"),{}).get(town.casefold(),0))
    except Exception:return 0


def daily_usage_total() -> int:
    try:
        data=json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return sum(int(v) for v in data.get("daily",{}).get(datetime.now().strftime("%Y-%m-%d"),{}).values())
    except Exception:return 0


def reserve_daily_search(town: str, per_town_cap: int, total_cap: int) -> bool:
    with _usage_file_lock():
        data = _read_usage_data()

        daily = data.setdefault("daily", {})
        if not isinstance(daily, dict):
            raise ValueError("Town sweep daily usage must be a JSON object.")
        day = datetime.now().strftime("%Y-%m-%d")
        daily_counts = daily.setdefault(day, {})
        if not isinstance(daily_counts, dict):
            raise ValueError("Town sweep daily counts must be a JSON object.")

        town_key = town.casefold()
        town_used = int(daily_counts.get(town_key, 0))
        total_used = sum(int(value) for value in daily_counts.values())
        if town_used >= per_town_cap or total_used >= total_cap:
            return False

        daily_counts[town_key] = town_used + 1
        _write_usage_data(data)
        return True


def daily_usage_bump(town: str) -> None:
    with _usage_file_lock():
        data = _read_usage_data()
        day = datetime.now().strftime("%Y-%m-%d")
        daily = data.setdefault("daily", {})
        if not isinstance(daily, dict):
            raise ValueError("Town sweep daily usage must be a JSON object.")
        counts = daily.setdefault(day, {})
        if not isinstance(counts, dict):
            raise ValueError("Town sweep daily counts must be a JSON object.")
        town_key = town.casefold()
        counts[town_key] = int(counts.get(town_key, 0)) + 1
        _write_usage_data(data)


def usage_bump() -> None:
    with _usage_file_lock():
        try:
            data = _read_usage_data()
            period = month()
            data[period] = int(data.get(period, 0)) + 1
            _write_usage_data(data)
        except (OSError, TypeError, ValueError) as exc:
            _LOGGER.warning("Unable to record town sweep monthly usage: %s", exc)


def cache_file(key: str) -> Path:
    return CACHE_DIR / ("maps-" + hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cache_get(key: str, days: int = 30):
    try:
        p = cache_file(key)
        if p.exists() and (time.time() - p.stat().st_mtime) < days * 86400:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def cache_put(key: str, value) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file(key).write_text(json.dumps(value), encoding="utf-8")
    except Exception:
        pass


def searches_left(api_key: str, client: httpx.Client) -> int | None:
    try:
        r = client.get(ACCOUNT_URL, params={"api_key": api_key}, timeout=15)
        if r.status_code == 200:
            d = r.json()
            return int(d.get("total_searches_left", d.get("plan_searches_left")))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- Google Maps via SerpApi

def maps_search(client: httpx.Client, api_key: str, trade: str, town: str, start: int = 0,
                per_town_cap: int | None = None, total_cap: int | None = None) -> dict:
    """One Google Maps search. Returns {'status', 'results', 'from_cache'}."""
    key = f"{trade}|{town}|{start}".lower()
    cached = cache_get(key)
    if cached is not None:
        return {"status": "ok", "results": cached, "from_cache": True}
    reserved = False
    if per_town_cap is not None and total_cap is not None:
        try:
            reserved = reserve_daily_search(town, per_town_cap, total_cap)
        except (OSError, TypeError, ValueError) as exc:
            return {
                "status": "budget_unavailable",
                "error": f"Daily sweep quota could not be safely enforced ({type(exc).__name__}).",
                "results": [],
            }
        if not reserved:
            return {
                "status": "budget_reached",
                "error": "Daily sweep search quota reached; no provider request was made.",
                "results": [],
            }
    params = {"engine": "google_maps", "q": f"{trade} in {town}, South Africa", "type": "search",
              "hl": "en", "gl": "za", "api_key": api_key}
    if start:
        params["start"] = start
    for attempt in range(2):
        try:
            r = retry_call(lambda: client.get(SERPAPI_URL, params=params, timeout=45), attempts=3)
        except Exception as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "results": [],
                    "reserved": reserved}
        if r.status_code == 200:
            data = r.json()
            if data.get("error") and "hasn't returned any results" in str(data["error"]).lower():
                usage_bump()
                cache_put(key, [])
                return {"status": "ok", "results": [], "from_cache": False, "reserved": reserved}
            if data.get("error"):
                return {"status": "error", "error": str(data["error"])[:200], "results": [],
                        "reserved": reserved}
            results = data.get("local_results") or []
            if not results and isinstance(data.get("place_results"), dict):
                results = [data["place_results"]]
            usage_bump()
            cache_put(key, results)
            return {"status": "ok", "results": results, "from_cache": False, "reserved": reserved}
        if r.status_code in (401, 403):
            return {"status": "error", "error": f"SerpApi refused the key (HTTP {r.status_code}).", "results": [],
                    "reserved": reserved}
        if r.status_code == 429 and attempt == 0:
            time.sleep(5)
            continue
        if attempt == 1 or r.status_code not in (429, 500, 502, 503):
            return {"status": "error", "error": f"HTTP {r.status_code}", "results": [],
                    "reserved": reserved}
        time.sleep(2)
    return {"status": "error", "error": "unknown", "results": [], "reserved": reserved}


def lead_from_maps(item: dict, trade: str, town: str) -> dict | None:
    name = clean(item.get("title"))
    if not name:
        return None
    website = clean(item.get("website"))
    status, detail = classify_website(website)
    types = item.get("types") or ([item["type"]] if item.get("type") else [])
    sources = []
    if item.get("place_id"):
        sources.append("https://www.google.com/maps/place/?q=place_id:" + str(item["place_id"]))
    if website and detail != "own_website_listed":
        sources.append(website)
    gps = item.get("gps_coordinates") or {}
    return {
        "business_name": name,
        "sector": trade,
        "categories": [clean(t) for t in types][:4],
        "website_status": status,
        "website_detail": detail,
        "website_url": website if detail == "own_website_listed" else "",
        "directory_or_profile_sources": sources,
        "phone": clean(item.get("phone")),
        "address": clean(item.get("address")),
        "rating": item.get("rating"),
        "review_count": item.get("reviews"),
        "latitude": gps.get("latitude"),
        "longitude": gps.get("longitude"),
        "place_id": clean(item.get("place_id")),
        "towns_found": [town],
        "found_via": "google_maps_serpapi",
    }


# ---------------------------------------------------------------- OpenStreetMap (optional, free)

def osm_search(client: httpx.Client, town: str) -> list[dict]:
    """Free supplement. Small SA towns are patchy on OpenStreetMap, so treat this as a bonus."""
    query = f"""[out:json][timeout:60];
area["name"="{town.replace('"', '')}"]["boundary"="administrative"]->.a;
(nwr["shop"](area.a); nwr["craft"](area.a); nwr["office"](area.a);
 nwr["amenity"~"restaurant|cafe|dentist|doctors|veterinary|bar|pub|fast_food"](area.a);
 nwr["tourism"~"guest_house|hotel|motel|hostel|apartment"](area.a););
out center tags 400;"""
    try:
        r = client.post(OVERPASS_URL, data={"data": query}, timeout=90)
        if r.status_code != 200:
            return []
        elements = r.json().get("elements", [])
    except Exception:
        return []
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        name = clean(tags.get("name"))
        if not name:
            continue
        website = clean(tags.get("website") or tags.get("contact:website") or tags.get("url"))
        status, detail = classify_website(website)
        kind = clean(tags.get("shop") or tags.get("craft") or tags.get("office")
                     or tags.get("amenity") or tags.get("tourism"))
        street = " ".join(x for x in (clean(tags.get("addr:housenumber")), clean(tags.get("addr:street"))) if x)
        center = el.get("center") or {}
        out.append({
            "business_name": name, "sector": kind, "categories": [kind] if kind else [],
            "website_status": status, "website_detail": detail,
            "website_url": website if detail == "own_website_listed" else "",
            "directory_or_profile_sources": ["https://www.openstreetmap.org/%s/%s" % (el.get("type"), el.get("id"))],
            "phone": clean(tags.get("phone") or tags.get("contact:phone")),
            "address": ", ".join(x for x in (street, clean(tags.get("addr:suburb")), clean(tags.get("addr:city"))) if x),
            "rating": None, "review_count": None,
            "latitude": el.get("lat") or center.get("lat"), "longitude": el.get("lon") or center.get("lon"),
            "place_id": "", "towns_found": [town], "found_via": "openstreetmap",
        })
    return out


# ---------------------------------------------------------------- merge and export

def merge_leads(leads: list[dict]) -> list[dict]:
    """Remove duplicates by Google place id, then by name plus phone, then by name plus address."""
    merged: dict[str, dict] = {}
    index: dict[str, str] = {}
    for lead in leads:
        keys = []
        if lead.get("place_id"):
            keys.append("pid:" + lead["place_id"])
        n = norm_name(lead["business_name"])
        p = norm_phone(lead.get("phone", ""))
        if p:
            keys.append(f"np:{n}|{p}")
        if lead.get("address"):
            keys.append(f"na:{n}|{norm_name(lead['address'])[:25]}")
        keys.append("n:" + n)
        hit = next((index[k] for k in keys if k in index and (k.startswith(("pid:", "np:", "na:")) or not lead.get("address"))), None)
        if hit is None:
            hit = keys[0]
            merged[hit] = lead
        else:
            base = merged[hit]
            for f in ("phone", "address", "rating", "review_count", "latitude", "longitude", "place_id"):
                if not base.get(f) and lead.get(f):
                    base[f] = lead[f]
            if lead["website_status"] == "website_found_in_search_unverified" and not base.get("website_url"):
                base["website_status"], base["website_detail"] = lead["website_status"], lead["website_detail"]
                base["website_url"] = lead["website_url"]
            base["directory_or_profile_sources"] = sorted(set(base["directory_or_profile_sources"] + lead["directory_or_profile_sources"]))
            base["towns_found"] = sorted(set(base["towns_found"] + lead["towns_found"]))
            if lead["sector"] and lead["sector"] not in base["sector"]:
                base["sector"] = base["sector"] + ", " + lead["sector"]
        for k in keys:
            index.setdefault(k, hit)
    return list(merged.values())


def scope_estimate(lead: dict) -> dict:
    """Entry offer only. Larger scopes are quoted after a conversation."""
    if lead["website_status"] == "no_website_surfaced_in_search":
        return {"offer": "Website build, up to 5 pages", "once_off_zar": 1500, "monthly_hosting_zar": 99}
    return {"offer": "Audit, then a revamp or monthly content support", "once_off_zar": None, "monthly_hosting_zar": 99}


def write_outputs(town: str, leads: list[dict], plan: dict) -> dict:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", town.lower()).strip("-") or "town"
    base = SWEEP_DIR / f"town-sweep-{slug}-{stamp}"
    for lead in leads:
        lead["preliminary_scope_estimate"] = scope_estimate(lead)
    a = [x for x in leads if x["website_status"] == "website_found_in_search_unverified"]
    b = [x for x in leads if x["website_status"] == "no_website_surfaced_in_search"]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "town": town, "tool": f"town_sweep {__version__}",
        "counts": {"total": len(leads), "website_found_unverified": len(a), "no_website_surfaced": len(b),
                   "social_page_only": sum(1 for x in b if x["website_detail"] == "social_page_only"),
                   "searches_made": plan.get("searches_made", 0), "searches_from_cache": plan.get("from_cache", 0)},
        "classification_note": "List B means no own website was listed in these sources. This is not proof. "
                               "Verify each lead before outreach, and confirm contact details.",
        "errors": plan.get("errors", []),
        "leads": leads,
    }
    json_path = Path(str(base) + ".json")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    cols = ["business_name", "sector", "phone", "address", "website_url", "website_detail", "rating",
            "review_count", "towns_found", "directory_or_profile_sources"]
    paths = {"json_path": str(json_path)}
    for label, rows in (("A-with-website", a), ("B-no-website", b)):
        p = Path(str(base) + f"-{label}.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([("; ".join(r[c]) if isinstance(r[c], list) else r.get(c, "")) for c in cols])
        paths["csv_" + label[0].lower()] = str(p)
    payload["paths"] = paths
    return payload


# ---------------------------------------------------------------- main

def run_sweep(town: str, trades: list[str], max_searches: int, pages: int = 1, osm: bool = False,
              dry_run: bool = False, progress=print) -> dict:
    api_key = env("SERPAPI_API_KEY")
    if not api_key and not dry_run and not (osm and not trades):
        raise SystemExit("SERPAPI_API_KEY is not set. In this window run:  set SERPAPI_API_KEY=your-key")
    daily_cap=max(1,int(env("CRM_SWEEP_MAX_NEW_PER_TOWN_PER_DAY","50")))
    daily_query_cap=max(1,int(env("CRM_SWEEP_MAX_SEARCHES_PER_DAY","12")))
    used_today=daily_usage_get(town)
    total_used_today=daily_usage_total()
    if not dry_run and total_used_today>=daily_query_cap:
        raise SystemExit(f"Daily sweep query cap reached: {total_used_today}/{daily_query_cap}. No provider request was made.")
    if not dry_run and used_today>=daily_cap:
        raise SystemExit(f"Town sweep daily cap reached for {town}: {used_today}/{daily_cap} searches. Try tomorrow or review the existing results.")
    planned = [(t, s * 20) for t in trades for s in range(pages)]
    cost_if_uncached = sum(1 for t, s in planned if cache_get(f"{t}|{town}|{s}".lower()) is None)
    progress(f"Plan: {len(planned)} Google Maps search(es) for {town}; about {cost_if_uncached} would use your free quota "
             f"(cached ones are free). Limit this run: {max_searches}.")
    if dry_run:
        for t, s in planned:
            progress(f"   {t} (results {s + 1}-{s + 20})")
        return {"dry_run": True, "planned": len(planned), "would_cost": cost_if_uncached}
    client = httpx.Client(base_url=None,headers={"User-Agent": "StraightUpDigital-TownSweep/1.0"}, follow_redirects=True)
    leads, errors, made, cached = [], [], 0, 0
    if api_key:
        left = searches_left(api_key, client)
        if left is not None:
            progress(f"SerpApi searches left this month: {left}")
            max_searches = min(max_searches, left)
        for trade, start in planned:
            fresh = cache_get(f"{trade}|{town}|{start}".lower()) is None
            if fresh and (made >= max_searches or used_today+made >= daily_cap or total_used_today+made >= daily_query_cap):
                errors.append(f"Stopped at the search limit before '{trade}'. Raise --max-searches to continue.")
                progress("Search limit reached. The rest of the trades were skipped.")
                break
            res = maps_search(client, api_key, trade, town, start, daily_cap, daily_query_cap)
            if res.get("reserved"):
                made += 1
            if res["status"] != "ok":
                errors.append(f"{trade}: {res.get('error')}")
                progress(f"   {trade}: problem ({res.get('error')})")
                if res["status"] in ("budget_reached", "budget_unavailable"):
                    break
                if "refused the key" in str(res.get("error", "")):
                    break
                continue
            if res.get("from_cache"):
                cached += 1
            else:
                time.sleep(random.uniform(float(env("SWEEP_MIN_DELAY_SECONDS","1")),float(env("SWEEP_MAX_DELAY_SECONDS","3"))))
            found = [lead_from_maps(i, trade, town) for i in res["results"]]
            found = [f for f in found if f]
            leads.extend(found)
            progress(f"   {trade}: {len(found)} business(es){' (cached)' if res.get('from_cache') else ''}")
    if osm:
        progress("Adding OpenStreetMap results...")
        extra = osm_search(client, town)
        progress(f"   OpenStreetMap: {len(extra)} business(es)")
        leads.extend(extra)
    client.close()
    merged = merge_leads(leads)
    return write_outputs(town, merged, {"searches_made": made, "from_cache": cached, "errors": errors})


def import_into_crm(json_path: str) -> None:
    try:
        from sweep_compliance import import_existing_sweep
        print("CRM import with suppression checks:", import_existing_sweep(Path(json_path)))
    except Exception as exc:
        print(f"CRM import failed: {type(exc).__name__}: {exc}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Collect local businesses in a South African town and split them by website.")
    ap.add_argument("town", help='Town name, for example "Port Alfred"')
    ap.add_argument("--trades", default="", help='Comma list, or "all". Default is 12 common trades.')
    ap.add_argument("--max-searches", type=int, default=12, help="Most Google Maps searches to spend this run (default 12)")
    ap.add_argument("--pages", type=int, default=1, help="Pages of 20 results per trade (default 1)")
    ap.add_argument("--osm", action="store_true", help="Also add free OpenStreetMap results")
    ap.add_argument("--dry-run", action="store_true", help="Show the plan and spend nothing")
    ap.add_argument("--import-crm", action="store_true", help="Load the results into the CRM (needs crm.py)")
    a = ap.parse_args()
    town = clean(a.town)
    if not town:
        raise SystemExit("Give a town name.")
    if a.trades.strip().lower() == "all":
        trades = ALL_TRADES
    elif a.trades.strip():
        trades = [clean(t) for t in a.trades.split(",") if clean(t)]
    else:
        trades = DEFAULT_TRADES
    result = run_sweep(town, trades, max(1, a.max_searches), max(1, a.pages), a.osm, a.dry_run)
    if result.get("dry_run"):
        return
    c = result["counts"]
    print(f"\nTown: {town}")
    print(f"Businesses found: {c['total']}")
    print(f"  A  with a website listed:      {c['website_found_unverified']}")
    print(f"  B  no website listed:          {c['no_website_surfaced']}  (of which only a social page: {c['social_page_only']})")
    print(f"Searches used this run: {c['searches_made']}  (free from cache: {c['searches_from_cache']})")
    for e in result.get("errors", []):
        print("  note:", e)
    p = result["paths"]
    print("\nFiles:")
    for k in ("json_path", "csv_a", "csv_b"):
        print("  ", p[k])
    print("\nList B is a hint, not proof. Check each lead before you pitch it.")
    if a.import_crm:
        import_into_crm(p["json_path"])


if __name__ == "__main__":
    main()
