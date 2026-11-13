"""
Straight Up Digital - find businesses that Google Maps misses.

Searches the free way (the ddgs package you already have) for Facebook pages and South African
directory listings, per trade, for one town. It skips anyone already in your CRM and writes a CSV
for you to review by hand. It does NOT add anything to the CRM and does NOT use your SerpApi quota.

Run from the business_auditor folder with the .venv active:

    python find_unlisted.py "Port Alfred" --dry-run
    python find_unlisted.py "Port Alfred"
    python find_unlisted.py "Port Alfred" --trades plumber,electrician --max-queries 10

Honest limits:
- A hit is a lead to check, not proof. A Facebook page may be dead, a directory entry may be old.
- ddgs is an unofficial free route. It can be slow or get rate limited. The script stops politely
  if that happens, and you can run it again later.
- Contact details come from public pages. Follow POPIA direct-marketing rules and offer an opt-out.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import random
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

try:
    from ddgs import DDGS
except Exception:  # package missing
    DDGS = None

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "reports" / "straight_up_digital.sqlite3"
OUT_DIR = HERE / "reports" / "finder"

DEFAULT_TRADES = [
    "plumber", "electrician", "builder", "painter", "roofing", "hair salon", "beauty salon",
    "restaurant", "guest house", "mechanic", "garden services", "cleaning services",
]

DIRECTORY_HOSTS = ("brabys.com", "cylex.co.za", "yellowpages.co.za", "hotfrog.co.za", "snupit.co.za",
                   "yep.co.za", "findit.co.za", "bizcommunity.com")
DIRECTORY_QUERY = " OR ".join(f"site:{h}" for h in ("brabys.com", "cylex.co.za", "yellowpages.co.za", "hotfrog.co.za", "snupit.co.za"))

# Facebook paths that are not a business page.
FB_SKIP = ("/groups/", "/events/", "/posts/", "/photos/", "/videos/", "/watch", "/marketplace", "/public/",
           "/hashtag/", "/story.php", "/permalink", "/sharer", "/login", "/help", "/people/", "/reel", "/share/")
LIST_PAGE_WORDS = re.compile(r"\b(top \d+|best \d+|\d+ best|list of|directory of|results for|search results)\b", re.I)
PHONE_RE = re.compile(r"(?:\+27|0)[\s\-]?\(?\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{4}")


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


def norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", clean(phone))
    if digits.startswith("27"):
        digits = "0" + digits[2:]
    return digits


def source_type(url: str) -> str:
    host = host_of(url)
    if host.endswith("facebook.com") or host == "fb.com":
        return "facebook"
    if any(host.endswith(d) for d in DIRECTORY_HOSTS):
        return "directory"
    return "other"


def is_business_page(url: str, title: str) -> bool:
    kind = source_type(url)
    path = urlparse(url).path.lower()
    if kind == "facebook":
        if any(s in path + "/" for s in FB_SKIP):
            return False
        return bool(path.strip("/"))
    if kind == "directory":
        return not LIST_PAGE_WORDS.search(title or "")
    return False


def business_name_from_title(title: str) -> str:
    """Titles look like 'Name - Port Alfred - Home | Facebook' or 'Name - Brabys'. Keep the name part."""
    t = clean(title)
    t = re.split(r"\s+[|–—]\s+|\s+-\s+", t)[0]
    t = re.sub(r"\s*\((?:\d+\+?\s*)?(?:reviews?|likes?)\)\s*$", "", t, flags=re.I)
    return clean(t)[:120]


def fb_handle(url: str) -> str:
    if source_type(url) != "facebook":
        return ""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return ""
    if parts[0] in ("p", "pages") and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


# ---------------------------------------------------------------- CRM (read only)

def load_crm(db_path: Path, tenant: str) -> dict:
    """Read names, phones and URLs already in the CRM. Opens the database read only."""
    out = {"names": [], "phones": set(), "hosts": set(), "fb": set(), "count": 0}
    if not db_path.is_file():
        return out
    conn = sqlite3.connect(f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(contacts)")}
        where = "WHERE tenant_id=?" if "tenant_id" in cols else ""
        rows = conn.execute(f"SELECT * FROM contacts {where}", (tenant,) if where else ()).fetchall()
    finally:
        conn.close()
    for r in rows:
        d = dict(r)
        out["count"] += 1
        out["names"].append(norm_name(d.get("business_name", "")))
        p = norm_phone(d.get("phone", ""))
        if p:
            out["phones"].add(p)
        urls = [d.get("website_url", "")]
        for key in ("social_links_json", "source_urls_json"):
            try:
                urls += json.loads(d.get(key) or "[]")
            except Exception:
                pass
        for u in urls:
            h = host_of(u)
            if not u:
                continue
            if h.endswith("facebook.com"):
                if fb_handle(u):
                    out["fb"].add(fb_handle(u))
            elif h:
                out["hosts"].add(h)
    return out


def already_in_crm(item: dict, crm: dict) -> bool:
    name = norm_name(item["business_name"])
    if item.get("phone") and norm_phone(item["phone"]) in crm["phones"]:
        return True
    if item["source_type"] == "facebook" and fb_handle(item["listing_url"]) in crm["fb"]:
        return True
    if name and name in crm["names"]:
        return True
    if name and len(name) >= 6:
        for existing in crm["names"]:
            if existing and difflib.SequenceMatcher(None, name, existing).ratio() >= 0.9:
                return True
    return False


# ---------------------------------------------------------------- searching

def run_query(query: str, per_query: int) -> list[dict]:
    if DDGS is None:
        raise RuntimeError("The ddgs package is not installed. Run: python -m pip install ddgs")
    results = []
    with DDGS() as search:
        try:
            iterator = search.text(query, region="za-en", max_results=per_query)
        except TypeError:
            iterator = search.text(query, max_results=per_query)
        for item in iterator:
            results.append({"title": clean(item.get("title")), "url": clean(item.get("href")), "snippet": clean(item.get("body"))})
    return results


def build_queries(town: str, trades: list[str]) -> list[dict]:
    plan = []
    for trade in trades:
        plan.append({"trade": trade, "kind": "facebook", "query": f"{trade} {town} site:facebook.com"})
        plan.append({"trade": trade, "kind": "directory", "query": f"{trade} {town} ({DIRECTORY_QUERY})"})
    return plan


def to_items(raw: list[dict], trade: str, town: str, query: str) -> list[dict]:
    items = []
    for r in raw:
        url, title, snippet = r["url"], r["title"], r["snippet"]
        if not url or not is_business_page(url, title):
            continue
        blob = f"{title} {snippet} {url}".lower()
        town_match = town.lower() in blob
        phones = PHONE_RE.findall(snippet + " " + title)
        name = business_name_from_title(title)
        if not name:
            continue
        items.append({
            "business_name": name, "trade": trade, "town": town, "source_type": source_type(url),
            "listing_url": url, "phone": clean(phones[0]) if phones else "", "snippet": snippet[:240],
            "town_match": "yes" if town_match else "no",
            "confidence": "medium" if town_match else "low", "found_via_query": query,
        })
    return items


def merge_items(items: list[dict]) -> list[dict]:
    """Same business seen twice (two trades, or Facebook plus a directory) becomes one row."""
    merged: dict[str, dict] = {}
    for it in items:
        key = norm_name(it["business_name"]) or it["listing_url"]
        if key in merged:
            base = merged[key]
            if it["listing_url"] not in base["listing_url"]:
                base["listing_url"] += " | " + it["listing_url"]
            if it["trade"] not in base["trade"]:
                base["trade"] += ", " + it["trade"]
            if not base["phone"] and it["phone"]:
                base["phone"] = it["phone"]
            if it["source_type"] not in base["source_type"]:
                base["source_type"] += " + " + it["source_type"]
            if it["town_match"] == "yes":
                base["town_match"], base["confidence"] = "yes", "medium"
        else:
            merged[key] = dict(it)
    for base in merged.values():
        if " + " in base["source_type"] and base["town_match"] == "yes":
            base["confidence"] = "high"
    return list(merged.values())


def write_csv(rows: list[dict], path: Path) -> None:
    cols = ["business_name", "trade", "town", "source_type", "listing_url", "phone", "snippet",
            "town_match", "confidence", "found_via_query"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def run_finder(town: str, trades: list[str] | None = None, per_query: int = 10, max_queries: int = 24,
               db: Path | str = DEFAULT_DB, tenant: str = "straight-up-digital", keep_low: bool = False,
               dry_run: bool = False, progress=print, out_dir: Path | None = None) -> dict:
    """Search, merge, skip anyone already in the CRM and write the review files. Used by the CLI and the CRM."""
    town = clean(town)
    if not town:
        raise ValueError("Give a town name.")
    trades = [clean(t) for t in (trades or []) if clean(t)] or DEFAULT_TRADES
    plan = build_queries(town, trades)[: max(1, max_queries)]
    progress(f"Plan: {len(plan)} search(es) for {town}. Free route, no SerpApi quota used.")
    if dry_run:
        for p in plan:
            progress("   " + p["query"])
        return {"dry_run": True, "plan": plan, "leads": [], "skipped": 0, "errors": [], "paths": {}}

    crm = load_crm(Path(db), tenant)
    progress(f"CRM contacts loaded for duplicate checking: {crm['count']}")
    found, errors, consecutive = [], [], 0
    for i, p in enumerate(plan, 1):
        try:
            raw = run_query(p["query"], per_query)
            consecutive = 0
        except Exception as exc:
            consecutive += 1
            errors.append(f"{p['query']}: {type(exc).__name__}: {exc}")
            progress(f"  [{i}/{len(plan)}] problem: {type(exc).__name__}")
            if consecutive >= 3:
                progress("Three failures in a row. Probably rate limited. Stopping here, try again later.")
                break
            time.sleep(5)
            continue
        items = to_items(raw, p["trade"], town, p["query"])
        found.extend(items)
        progress(f"  [{i}/{len(plan)}] {p['trade']} ({p['kind']}): {len(raw)} results, {len(items)} look like business pages")
        time.sleep(random.uniform(2.0, 4.0))

    merged = merge_items(found)
    new, skipped = [], 0
    for it in merged:
        if already_in_crm(it, crm):
            skipped += 1
        elif it["town_match"] == "no" and not keep_low:
            continue
        else:
            new.append(it)

    out_dir = Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", town.lower()).strip("-") or "town"
    base = out_dir / f"finder-{slug}-{stamp}"
    write_csv(new, Path(str(base) + ".csv"))
    Path(str(base) + ".json").write_text(json.dumps(
        {"town": town, "generated_at": datetime.now().isoformat(timespec="seconds"), "queries": [p["query"] for p in plan],
         "errors": errors, "skipped_already_in_crm": skipped, "leads": new}, indent=2, ensure_ascii=False), encoding="utf-8")
    progress(f"Business pages found: {len(merged)}. Already in your CRM (skipped): {skipped}. New to review: {len(new)}.")
    return {"dry_run": False, "plan": plan, "leads": new, "skipped": skipped, "errors": errors,
            "paths": {"csv": str(base) + ".csv", "json": str(base) + ".json"}, "found_total": len(merged)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Find Facebook and directory businesses that may not be on Google Maps.")
    ap.add_argument("town")
    ap.add_argument("--trades", default="", help="Comma list. Default is 12 common trades.")
    ap.add_argument("--per-query", type=int, default=10, help="Results to read per search (default 10)")
    ap.add_argument("--max-queries", type=int, default=24, help="Most searches this run (default 24)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--tenant", default="straight-up-digital")
    ap.add_argument("--keep-low", action="store_true", help="Keep results where the town name was not seen")
    ap.add_argument("--dry-run", action="store_true", help="Show the plan and search nothing")
    a = ap.parse_args()
    res = run_finder(a.town, [t for t in a.trades.split(",")], a.per_query, a.max_queries, a.db, a.tenant, a.keep_low, a.dry_run)
    if res.get("dry_run"):
        return
    print(f"\nReview file: {res['paths']['csv']}")
    print("Nothing was added to the CRM from the command line. Review the leads in the CRM under Not listed, or check the CSV by hand.")


if __name__ == "__main__":
    main()
