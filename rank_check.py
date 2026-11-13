"""
Straight Up Digital - ranking check for businesses you choose.

For each chosen business it searches a few local keywords (for example "plumber Port Alfred") and
reports where the business shows up: its own website, its Facebook page, or a listing that carries
its name. It saves the results so you can quote them in a pitch, and the audit can use them later.

Two ways to run it:
  --source serpapi   (default) Real Google results from South Africa through your SerpApi key.
                     Costs 1 search per keyword per 10 results. It shows the plan and asks first.
  --source ddgs      Free, uses the ddgs package. Indicative only: unofficial, not personalised,
                     can differ from what customers see on Google.

Run from the business_auditor folder with the .venv active:

    python rank_check.py --contact 12 --dry-run
    python rank_check.py --contact 12 --contact 15
    python rank_check.py --business "Kowie Plumbing" --site kowieplumbing.co.za --town "Port Alfred" --trade plumber
    python rank_check.py --contact 12 --source ddgs
    python rank_check.py --contact 12 --keywords "plumber port alfred;emergency plumber port alfred"

Honest limits: rankings change by location, device and day. A result here is a snapshot of one
logged-out search from South Africa, not a promise of what every customer sees. Say it that way.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

HERE = Path(__file__).resolve().parent
REPORT_DIR = HERE / "reports"
CACHE_DIR = REPORT_DIR / "cache"
OUT_DIR = REPORT_DIR / "rankings"
USAGE_PATH = REPORT_DIR / "town_sweep_usage.json"
DEFAULT_DB = REPORT_DIR / "straight_up_digital.sqlite3"
SERP_BASE = "https://serpapi.com"


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def host_of(url: str) -> str:
    try:
        return (urlparse(url if "//" in url else "https://" + url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", clean(name).lower())
    n = re.sub(r"\b(pty|ltd|cc|the|and|co|t a)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def fb_handle(url: str) -> str:
    if not host_of(url).endswith("facebook.com"):
        return ""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return ""
    if parts[0] in ("p", "pages") and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def name_from_title(title: str) -> str:
    return clean(re.split(r"\s+[|–—]\s+|\s+-\s+", clean(title))[0])


def get_env(name: str) -> str:
    """Environment first, then the project's .env file."""
    if os.environ.get(name):
        return os.environ[name].strip()
    try:
        for line in (HERE / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- businesses

def load_contacts(ids: list[int], db_path: Path, tenant: str) -> list[dict]:
    if not db_path.is_file():
        raise SystemExit(f"CRM database not found: {db_path}")
    conn = sqlite3.connect(f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = []
    try:
        for cid in ids:
            row = conn.execute("SELECT * FROM contacts WHERE id=? AND tenant_id=?", (cid, tenant)).fetchone()
            if not row:
                print(f"Contact {cid} not found for tenant {tenant}. Skipped.")
                continue
            d = dict(row)
            try:
                socials = json.loads(d.get("social_links_json") or "[]")
            except Exception:
                socials = []
            handle = next((fb_handle(u) for u in socials if fb_handle(u)), "")
            town = d.get("town") if d.get("town") and d.get("town") != "other" else clean(d.get("address", "")).split(",")[-1]
            out.append({"label": f"contact-{cid}", "contact_id": cid, "business_name": clean(d.get("business_name")),
                        "website": clean(d.get("website_url")), "fb_handle": handle,
                        "town": clean(town), "trade": clean(d.get("sector")).split(",")[0].strip()})
    finally:
        conn.close()
    return out


def default_keywords(b: dict, limit: int) -> list[str]:
    town, trade, name = b["town"], b["trade"], b["business_name"]
    words = []
    if trade and town:
        words += [f"{trade} {town}", f"best {trade} {town}"]
    if name:
        words.append(f"{name} {town}".strip())
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


# ---------------------------------------------------------------- cache and usage

def month() -> str:
    return datetime.now().strftime("%Y-%m")


def cache_file(key: str) -> Path:
    return CACHE_DIR / ("rank-" + hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cache_get(key: str, days: int):
    try:
        p = cache_file(key)
        if days > 0 and p.exists() and (time.time() - p.stat().st_mtime) < days * 86400:
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


def usage_bump(n: int) -> None:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[month()] = int(data.get(month(), 0)) + n
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- fetching results

def serpapi_page(client, key: str, query: str, start: int, cache_days: int) -> dict:
    ck = f"google|{query}|{start}|za".lower()
    hit = cache_get(ck, cache_days)
    if hit is not None:
        return {"organic": hit.get("organic", []), "local": hit.get("local", []), "cached": True}
    params = {"engine": "google", "q": query, "google_domain": "google.co.za", "gl": "za", "hl": "en", "api_key": key}
    if start:
        params["start"] = start
    r = client.get(SERP_BASE + "/search.json", params=params, timeout=45)
    if r.status_code in (401, 403):
        raise RuntimeError(f"SerpApi refused the key (HTTP {r.status_code}).")
    data = r.json() if r.content else {}
    if r.status_code != 200 or data.get("error"):
        raise RuntimeError(f"SerpApi: {clean(data.get('error') or r.text)[:160]}")
    organic = [{"link": clean(o.get("link")), "title": clean(o.get("title"))} for o in (data.get("organic_results") or [])]
    local = data.get("local_results")
    if isinstance(local, dict):
        local = local.get("places") or []
    local = [{"title": clean(p.get("title"))} for p in (local or []) if isinstance(p, dict)]
    cache_put(ck, {"organic": organic, "local": local})
    usage_bump(1)
    return {"organic": organic, "local": local, "cached": False}


def ddgs_results(query: str, engine: str, count: int, cache_days: int) -> dict:
    ck = f"ddgs:{engine}|{query}|{count}".lower()
    hit = cache_get(ck, cache_days)
    if hit is not None:
        return {"organic": hit["organic"], "local": [], "cached": True}
    try:
        from ddgs import DDGS
    except Exception:
        raise RuntimeError("The ddgs package is not installed. Run: python -m pip install ddgs")
    organic = []
    with DDGS() as search:
        kwargs = {"region": "za-en", "max_results": count}
        if engine and engine != "auto":
            kwargs["backend"] = engine
        try:
            items = list(search.text(query, **kwargs))
        except TypeError:
            items = list(search.text(query, max_results=count))
    for it in items:
        organic.append({"link": clean(it.get("href")), "title": clean(it.get("title"))})
    cache_put(ck, {"organic": organic})
    return {"organic": organic, "local": [], "cached": False}


# ---------------------------------------------------------------- matching

def find_position(b: dict, organic: list[dict]) -> dict:
    """Best (lowest) position where this business appears, and how we recognised it."""
    own = host_of(b["website"]) if b.get("website") else ""
    name = norm_name(b["business_name"])
    for pos, o in enumerate(organic, 1):
        link, title = o["link"], o["title"]
        h = host_of(link)
        if own and (h == own or h.endswith("." + own)):
            return {"position": pos, "match_type": "own website", "matched_url": link}
        if b.get("fb_handle") and fb_handle(link) == b["fb_handle"]:
            return {"position": pos, "match_type": "facebook page", "matched_url": link}
        tn = norm_name(name_from_title(title))
        if name and tn and difflib.SequenceMatcher(None, name, tn).ratio() >= 0.85:
            return {"position": pos, "match_type": "listing with the business name", "matched_url": link}
    return {"position": None, "match_type": "", "matched_url": ""}


def map_pack_position(b: dict, local: list[dict]):
    name = norm_name(b["business_name"])
    for i, p in enumerate(local, 1):
        if name and difflib.SequenceMatcher(None, name, norm_name(p["title"])).ratio() >= 0.85:
            return i
    return None


def check_keyword(b: dict, query: str, a, client, key: str) -> dict:
    organic, local, cached_all = [], [], True
    if a.source == "serpapi":
        for page in range(max(1, a.depth // 10)):
            res = serpapi_page(client, key, query, page * 10, a.cache_days)
            organic += res["organic"]
            local = local or res["local"]
            cached_all = cached_all and res["cached"]
            if not res["cached"]:
                time.sleep(random.uniform(1.0, 2.0))
    else:
        res = ddgs_results(query, a.engine, a.depth, a.cache_days)
        organic, cached_all = res["organic"], res["cached"]
        if not res["cached"]:
            time.sleep(random.uniform(2.0, 4.0))
    found = find_position(b, organic)
    top = [{"position": i, "host": host_of(o["link"]), "title": o["title"]} for i, o in enumerate(organic[:5], 1)]
    return {"business": b["business_name"], "contact_id": b.get("contact_id", ""), "keyword": query,
            "source": a.source, "engine": "google.co.za" if a.source == "serpapi" else a.engine,
            "checked_depth": len(organic), "position": found["position"], "match_type": found["match_type"],
            "matched_url": found["matched_url"], "map_pack_position": map_pack_position(b, local) if a.source == "serpapi" else None,
            "top_results": top, "from_cache": cached_all, "checked_at": datetime.now().isoformat(timespec="seconds")}


def save_outputs(label: str, results: list[dict], errors: list[str], source: str, out_dir: Path | None = None) -> Path:
    out_dir = Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"rank-{label}-{stamp}"
    Path(str(base) + ".json").write_text(json.dumps(
        {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": source, "errors": errors,
         "note": "Snapshot of one logged-out search from South Africa. Rankings vary by location, device and day.",
         "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    cols = ["business", "contact_id", "keyword", "source", "engine", "position", "match_type", "matched_url",
            "map_pack_position", "checked_depth", "top_competitors", "checked_at"]
    with open(str(base) + ".csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in results:
            row = {c: r.get(c, "") for c in cols}
            row["position"] = r["position"] if r["position"] else f"not in top {r['checked_depth']}"
            row["top_competitors"] = ", ".join(t["host"] for t in r["top_results"][:3])
            w.writerow(row)
    return base


def run_for_business(b: dict, keywords: list[str] | None = None, source: str = "serpapi", engine: str = "auto",
                     depth: int = 10, cache_days: int = 7, max_keywords: int = 3, progress=print) -> dict:
    """Used by the CRM button. Checks the balance first and refuses if there are not enough searches left."""
    from types import SimpleNamespace
    kws = [clean(k) for k in (keywords or []) if clean(k)] or default_keywords(b, max_keywords)
    if not kws:
        raise ValueError("This business needs a trade and a town, or type your own keywords.")
    kws = kws[:5]
    depth = 20 if depth >= 20 else 10
    a = SimpleNamespace(source=source, engine=engine, depth=depth, cache_days=cache_days)
    client, key = None, ""
    if source == "serpapi":
        import httpx
        key = get_env("SERPAPI_API_KEY")
        if not key:
            raise RuntimeError("SERPAPI_API_KEY was not found. Check your .env file, or use the free option.")
        client = httpx.Client(follow_redirects=True, headers={"User-Agent": "StraightUpDigital-RankCheck/1.0"})
        fresh = sum(1 for k in kws for page in range(depth // 10)
                    if cache_get(f"google|{k}|{page * 10}|za".lower(), cache_days) is None)
        try:
            r = client.get(SERP_BASE + "/account.json", params={"api_key": key}, timeout=15)
        except Exception as exc:
            client.close()
            raise RuntimeError(f"Could not reach SerpApi to check your balance ({type(exc).__name__}).")
        if r.status_code in (401, 403):
            client.close()
            raise RuntimeError("SerpApi refused the key. Run check_key.py.")
        if r.status_code == 200:
            data = r.json()
            left = int(data.get("total_searches_left", data.get("plan_searches_left", 0)))
            if fresh > left:
                client.close()
                raise RuntimeError(f"Only {left} SerpApi searches left this month, and this check needs {fresh}.")
    results, errors = [], []
    try:
        for k in kws:
            try:
                results.append(check_keyword(b, k, a, client, key))
                progress(f"{b['business_name']} | {k}: {describe(results[-1])}")
            except Exception as exc:
                errors.append(f"{k}: {type(exc).__name__}: {exc}")
                if "refused the key" in str(exc):
                    break
    finally:
        if client:
            client.close()
    base = save_outputs(b.get("label", "business"), results, errors, source) if results else None
    return {"results": results, "errors": errors, "path": str(base) if base else ""}


def describe(r: dict) -> str:
    if r["position"]:
        s = f"#{r['position']} ({r['match_type']})"
    else:
        s = f"not in the top {r['checked_depth'] or '?'}"
    if r.get("map_pack_position"):
        s += f", map pack #{r['map_pack_position']}"
    return s


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Check where chosen businesses rank for local searches.")
    ap.add_argument("--contact", type=int, action="append", default=[], help="CRM contact id (repeat for several)")
    ap.add_argument("--business", default="")
    ap.add_argument("--site", default="")
    ap.add_argument("--town", default="")
    ap.add_argument("--trade", default="")
    ap.add_argument("--keywords", default="", help="Semicolon list. Overrides the automatic keywords.")
    ap.add_argument("--max-keywords", type=int, default=3)
    ap.add_argument("--source", choices=("serpapi", "ddgs"), default="serpapi")
    ap.add_argument("--engine", default="auto", help="ddgs only: auto, bing, duckduckgo, yahoo, brave, google")
    ap.add_argument("--depth", type=int, default=10, help="Results to look through: 10 or 20 (20 costs double on serpapi)")
    ap.add_argument("--cache-days", type=int, default=7)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--tenant", default="straight-up-digital")
    ap.add_argument("--yes", action="store_true", help="Do not ask before spending SerpApi searches")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.depth = 20 if a.depth >= 20 else 10

    businesses = load_contacts(a.contact, Path(a.db), a.tenant) if a.contact else []
    if a.business:
        businesses.append({"label": re.sub(r"[^a-z0-9]+", "-", a.business.lower()).strip("-") or "business",
                           "contact_id": "", "business_name": clean(a.business), "website": clean(a.site),
                           "fb_handle": "", "town": clean(a.town), "trade": clean(a.trade)})
    if not businesses:
        raise SystemExit("Choose at least one business: --contact ID or --business NAME.")

    custom = [clean(k) for k in a.keywords.split(";") if clean(k)]
    plan = []
    for b in businesses:
        kws = custom or default_keywords(b, max(1, a.max_keywords))
        if not kws:
            print(f"No keywords for {b['business_name']}: it needs a trade and a town. Use --keywords or --trade/--town.")
        plan += [(b, k) for k in kws]
    if not plan:
        raise SystemExit("Nothing to check.")

    per_kw = (a.depth // 10) if a.source == "serpapi" else 0
    fresh = 0
    if a.source == "serpapi":
        for b, k in plan:
            for page in range(per_kw):
                if cache_get(f"google|{k}|{page * 10}|za".lower(), a.cache_days) is None:
                    fresh += 1
    print(f"Plan: {len(plan)} keyword check(s) via {a.source}.")
    for b, k in plan:
        print(f"   {b['business_name']}: {k}")
    if a.source == "serpapi":
        print(f"This would use about {fresh} SerpApi search(es) (cached ones are free).")
    if a.dry_run:
        return

    client, key = None, ""
    if a.source == "serpapi":
        import httpx
        key = get_env("SERPAPI_API_KEY")
        if not key:
            raise SystemExit("SERPAPI_API_KEY not found in the environment or .env. Or use --source ddgs.")
        client = httpx.Client(follow_redirects=True, headers={"User-Agent": "StraightUpDigital-RankCheck/1.0"})
        try:
            r = client.get(SERP_BASE + "/account.json", params={"api_key": key}, timeout=15)
            if r.status_code == 200:
                left = int(r.json().get("total_searches_left", r.json().get("plan_searches_left", 0)))
                print(f"SerpApi searches left this month: {left}")
                if fresh > left:
                    raise SystemExit(f"Not enough searches left ({left}) for this plan ({fresh}). Reduce keywords or businesses.")
            elif r.status_code in (401, 403):
                raise SystemExit("SerpApi refused the key. Run check_key.py.")
        except SystemExit:
            raise
        except Exception as exc:
            print(f"Could not read the account balance ({type(exc).__name__}). Continuing.")
        if fresh and not a.yes:
            try:
                answer = input(f"Spend up to {fresh} search(es)? Type y to continue: ").strip().lower()
            except EOFError:
                answer = ""
            if answer != "y":
                raise SystemExit("Stopped. Nothing was spent.")

    results, errors = [], []
    for b, k in plan:
        try:
            res = check_keyword(b, k, a, client, key)
        except Exception as exc:
            msg = f"{b['business_name']} / {k}: {type(exc).__name__}: {exc}"
            errors.append(msg)
            print("   problem:", msg)
            if "refused the key" in str(exc):
                break
            continue
        results.append(res)
        print(f"   {b['business_name']} | {k}: {describe(res)}{' (cached)' if res['from_cache'] else ''}")
    if client:
        client.close()

    label = businesses[0]["label"] if len(businesses) == 1 else "batch"
    base = save_outputs(label, results, errors, a.source)

    ranked = sum(1 for r in results if r["position"])
    print(f"\nShowed up for {ranked} of {len(results)} keyword check(s).")
    print(f"Saved: {base}.csv  and  {base}.json")
    if a.source == "ddgs":
        print("These free results are indicative only. Use the SerpApi version for numbers you quote to a client.")
    print("Rankings are a snapshot. Describe them as 'when I searched on <date>', not as a guarantee.")


if __name__ == "__main__":
    main()
