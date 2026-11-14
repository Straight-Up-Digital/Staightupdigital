"""Small local SQLite CRM for Straight Up Digital leads and clients.

Uses Python's standard-library sqlite3 only; no cloud account or paid service needed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from config_loader import env, load_env
from security import active_tenant_id, tenant_context
from security_schema import eligible_for_crm

DEFAULT_DB = Path(__file__).resolve().parent / "reports" / "straight_up_digital.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    surname TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    website_url TEXT NOT NULL DEFAULT '',
    social_links_json TEXT NOT NULL DEFAULT '[]',
    sector TEXT NOT NULL DEFAULT '',
    lead_status TEXT NOT NULL DEFAULT 'new',
    website_status TEXT NOT NULL DEFAULT 'unknown',
    source_urls_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    audit_score INTEGER,
    audit_pdf TEXT NOT NULL DEFAULT '',
    retainer_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    rating REAL,
    review_count INTEGER,
    latitude REAL,
    longitude REAL,
    place_id TEXT,
    website_detail TEXT NOT NULL DEFAULT 'unknown',
    found_via TEXT NOT NULL DEFAULT '',
    categories_json TEXT NOT NULL DEFAULT '[]',
    town TEXT NOT NULL DEFAULT 'other',
    pipeline_stage TEXT NOT NULL DEFAULT 'new',
    next_follow_up_date TEXT NOT NULL DEFAULT '',
    quote_amount REAL,
    quote_type TEXT NOT NULL DEFAULT '',
    monthly_fee REAL,
    hosting_enabled INTEGER NOT NULL DEFAULT 0,
    outreach_allowed INTEGER NOT NULL DEFAULT 0,
    report_date TEXT NOT NULL DEFAULT '',
    details_verified INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(business_name, location)
);
CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(lead_status, website_status);
CREATE INDEX IF NOT EXISTS idx_contacts_location ON contacts(location);
CREATE TABLE IF NOT EXISTS audit_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    audit_date TEXT NOT NULL,
    score INTEGER,
    coverage INTEGER,
    confidence TEXT NOT NULL DEFAULT '',
    google_status TEXT NOT NULL DEFAULT '',
    google_rating REAL,
    google_review_count INTEGER,
    quote_amount REAL,
    pdf_path TEXT NOT NULL DEFAULT '',
    json_path TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    full_audit_json TEXT NOT NULL DEFAULT '{}',
    scope_items_json TEXT NOT NULL DEFAULT '[]',
    client_report_url TEXT NOT NULL DEFAULT '',
    client_report_published_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(contact_id) REFERENCES contacts(id)
);
CREATE INDEX IF NOT EXISTS idx_audit_history_contact ON audit_history(contact_id, audit_date DESC);
"""

class ClosingConnection(sqlite3.Connection):
    """Connection context manager that commits/rolls back and always closes."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


MIGRATIONS = {
    "address": "TEXT NOT NULL DEFAULT ''",
    "rating": "REAL",
    "review_count": "INTEGER",
    "latitude": "REAL",
    "longitude": "REAL",
    "place_id": "TEXT",
    "website_detail": "TEXT NOT NULL DEFAULT 'unknown'",
    "found_via": "TEXT NOT NULL DEFAULT ''",
    "categories_json": "TEXT NOT NULL DEFAULT '[]'",
    "town": "TEXT NOT NULL DEFAULT 'other'",
    "pipeline_stage": "TEXT NOT NULL DEFAULT 'new'",
    "next_follow_up_date": "TEXT NOT NULL DEFAULT ''",
    "quote_amount": "REAL",
    "quote_type": "TEXT NOT NULL DEFAULT ''",
    "monthly_fee": "REAL",
    "hosting_enabled": "INTEGER NOT NULL DEFAULT 0",
    "outreach_allowed": "INTEGER NOT NULL DEFAULT 0",
    "report_date": "TEXT NOT NULL DEFAULT ''",
    "details_verified": "INTEGER NOT NULL DEFAULT 0",
    "verified_at": "TEXT NOT NULL DEFAULT ''",
    "sweep_imported_at": "TEXT NOT NULL DEFAULT ''",
}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    load_env()
    if db_path is None:
        db_path = env("CRM_DATABASE_PATH", str(DEFAULT_DB))
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(contacts)")}
    for name, declaration in MIGRATIONS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE contacts ADD COLUMN {name} {declaration}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_place_id ON contacts(place_id) WHERE place_id IS NOT NULL AND place_id <> ''")
    history_cols={row[1] for row in conn.execute("PRAGMA table_info(audit_history)")}
    for name,declaration in {"full_audit_json":"TEXT NOT NULL DEFAULT '{}'","scope_items_json":"TEXT NOT NULL DEFAULT '[]'","client_report_url":"TEXT NOT NULL DEFAULT ''","client_report_published_at":"TEXT NOT NULL DEFAULT ''"}.items():
        if name not in history_cols: conn.execute(f"ALTER TABLE audit_history ADD COLUMN {name} {declaration}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_town_stage ON contacts(town, pipeline_stage)")
    conn.commit()
    return conn


def get_contact(contact_id: int, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> dict | None:
    tenant=tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=? AND tenant_id=?", (int(contact_id),tenant)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["social_links"] = json.loads(item.pop("social_links_json"))
    item["source_urls"] = json.loads(item.pop("source_urls_json"))
    item["categories"] = json.loads(item.pop("categories_json"))
    return item


def save_contact_fields(contact_id: int, data: dict, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> None:
    now = _now()
    verified = int(bool(data.get("details_verified")))
    verified_at = now if verified else ""
    with connect(db_path) as conn:
        conn.execute("""UPDATE contacts SET first_name=?,surname=?,phone=?,email=?,location=?,town=?,address=?,website_url=?,
          social_links_json=?,categories_json=?,sector=?,retainer_name=?,website_status=?,website_detail=?,lead_status=?,pipeline_stage=?,
          next_follow_up_date=?,quote_amount=?,quote_type=?,monthly_fee=?,hosting_enabled=?,outreach_allowed=?,
          details_verified=?,verified_at=CASE WHEN ?=1 THEN CASE WHEN verified_at='' THEN ? ELSE verified_at END ELSE '' END,marketing_consent=?,consent_recorded_at=?,active_outreach=?,last_contacted_at=?,lifecycle_status=?,extracted_at=CASE WHEN extracted_at='' THEN ? ELSE extracted_at END,notes=?,rating=?,review_count=?,latitude=?,longitude=?,place_id=?,updated_at=? WHERE id=? AND tenant_id=?""",
          (data.get("first_name", ""),data.get("surname", ""),data.get("phone", ""),data.get("email", ""),
           data.get("location", data.get("town", "")),data.get("town", "other"),data.get("address", ""),
           data.get("website_url", ""),_json_list(data.get("social_links")),_json_list(data.get("categories")),
           data.get("sector", ""),data.get("retainer_name", ""),data.get("website_status", "unknown"),data.get("website_detail", "unknown"),
           data.get("lead_status", "new"),data.get("pipeline_stage", "new"),data.get("next_follow_up_date", ""),
           data.get("quote_amount") or None,data.get("quote_type", ""),data.get("monthly_fee") or None,
           int(bool(data.get("hosting_enabled"))),int(bool(data.get("outreach_allowed"))),verified,verified,now,
           int(bool(data.get("marketing_consent"))),now if data.get("marketing_consent") else "",int(bool(data.get("active_outreach"))),data.get("last_contacted_at", ""),
           data.get("lifecycle_status", "Scraped"),now,data.get("notes", ""),data.get("rating") or None,data.get("review_count") or None,
           data.get("latitude") or None,data.get("longitude") or None,data.get("place_id", ""),now,int(contact_id),tenant_id or env("CRM_TENANT_ID", "straight-up-digital")))



def build_scope_items(audit: dict) -> list[dict]:
    steps=[]
    for finding in audit.get("opportunities", []):
        steps.append({"id":f"step-{len(steps)+1}","step":len(steps)+1,"title":str(finding.get("title") or "Audit action"),"evidence":str(finding.get("evidence") or ""),"action":str(finding.get("action") or ""),"done":False})
    for brief in (audit.get("strategy") or {}).get("briefs", []):
        steps.append({"id":f"step-{len(steps)+1}","step":len(steps)+1,"title":str(brief.get("working_title") or brief.get("page_type") or "Content brief"),"evidence":str(brief.get("why") or "Recommended content brief"),"action":"Prepare content using this outline: " + "; ".join(str(x) for x in brief.get("outline", [])),"done":False})
    return steps


def update_scope_item(history_id: int, item_id: str, done: bool, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> None:
    tenant=tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with connect(db_path) as conn:
        row=conn.execute("SELECT scope_items_json FROM audit_history WHERE id=? AND tenant_id=?",(int(history_id),tenant)).fetchone()
        if not row: raise ValueError("Audit history entry not found")
        items=json.loads(row["scope_items_json"] or "[]")
        target=next((item for item in items if item.get("id")==item_id),None)
        if target is None: raise ValueError("Scope step not found")
        target["done"]=bool(done)
        conn.execute("UPDATE audit_history SET scope_items_json=? WHERE id=? AND tenant_id=?",(json.dumps(items,ensure_ascii=False),int(history_id),tenant))

def save_audit_result(contact_id: int, audit: dict, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> int:
    report_date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scorecard = audit.get("scorecard") or {}
    google = (audit.get("external_research") or {}).get("google_places") or {}
    quote = audit.get("package_suggestion") or {}
    summary = {"business_name": audit.get("business_name"), "website": audit.get("website"),
               "scorecard": scorecard, "confidence": audit.get("confidence"),
               "opportunities": audit.get("opportunities", []), "package_suggestion": quote,
               "data_sources": audit.get("data_sources"), "strategy": audit.get("strategy"),
               "external_research": audit.get("external_research"), "website_research": audit.get("website_research"),
               "keyword_research": audit.get("keyword_research"), "review_checklist": audit.get("review_checklist")}
    pdf = str(audit.get("pdf_report_path") or "")
    json_path = str(Path(pdf).with_suffix(".json")) if pdf else ""
    if json_path:
        Path(json_path).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    full_snapshot=json.dumps(audit,ensure_ascii=False)
    scope_json=json.dumps(build_scope_items(audit),ensure_ascii=False)
    tenant=tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM contacts WHERE id=? AND tenant_id=?",(int(contact_id),tenant)).fetchone(): raise LookupError("Contact not found in active tenant")
        cursor = conn.execute("""INSERT INTO audit_history(contact_id,audit_date,score,coverage,confidence,google_status,
          google_rating,google_review_count,quote_amount,pdf_path,json_path,summary_json,full_audit_json,scope_items_json,tenant_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (int(contact_id),report_date,scorecard.get("assessed_score"),scorecard.get("assessment_coverage_percent"),
           (audit.get("confidence") or {}).get("level", ""),google.get("status", ""),google.get("rating"),
           google.get("review_count"),quote.get("estimated_price_zar"),pdf,json_path,json.dumps(summary,ensure_ascii=False),full_snapshot,scope_json,tenant))
        conn.execute("UPDATE contacts SET audit_score=?,report_date=?,audit_pdf=?,quote_amount=?,quote_type='audit fixes',updated_at=? WHERE id=? AND tenant_id=?",
                     (scorecard.get("assessed_score"),report_date,pdf,quote.get("estimated_price_zar"),report_date,int(contact_id),tenant))
        return int(cursor.lastrowid)


def audit_history(contact_id: int, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> list[dict]:
    tenant=tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with connect(db_path) as conn:
        rows=conn.execute("SELECT * FROM audit_history WHERE contact_id=? AND tenant_id=? ORDER BY audit_date DESC,id DESC",(int(contact_id),tenant)).fetchall()
    result=[]
    for row in rows:
        item=dict(row); item["summary"]=json.loads(item.pop("summary_json")); item["full_audit"]=json.loads(item.pop("full_audit_json")); item["scope_items"]=json.loads(item.pop("scope_items_json")); result.append(item)
    return result


def save_client_report_url(history_id: int, report_url: str, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> None:
    """Save the public link only in the private, tenant-scoped CRM history."""
    from urllib.parse import urlparse
    import re

    parsed = urlparse(str(report_url or ""))
    if (parsed.scheme != "https" or parsed.hostname not in {
            "straight-up-digital-site.netlify.app", "straightupdigital.co.za", "www.straightupdigital.co.za"
        } or not re.fullmatch(r"/report/[A-Za-z0-9_-]{40,60}", parsed.path)):
        raise ValueError("Refusing to save a non-Straight Up Digital report URL")
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    now = _now()
    with connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE audit_history SET client_report_url=?,client_report_published_at=? WHERE id=? AND tenant_id=?",
            (report_url, now, int(history_id), tenant),
        )
        if cursor.rowcount != 1:
            raise LookupError("Audit history entry not found in the active tenant")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_list(value: Any) -> str:
    if value is None or value == "":
        value = []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, (list, tuple)):
        value = [value]
    return json.dumps(list(value), ensure_ascii=False)


def add_contact(data: dict, db_path: str | Path = DEFAULT_DB, tenant_id: str | None = None) -> int:
    name=str(data.get("business_name") or "").strip()
    if not name: raise ValueError("business_name is required")
    tenant=tenant_id or env("CRM_TENANT_ID","straight-up-digital"); now=_now()
    location=str(data.get("location") or data.get("town") or "").strip()
    social=_json_list(data.get("social_links") or data.get("social_profiles"))
    sources=_json_list(data.get("source_urls") or data.get("directory_or_profile_sources"))
    fields={"first_name":str(data.get("first_name") or "").strip(),"surname":str(data.get("surname") or "").strip(),
      "phone":str(data.get("phone") or "").strip(),"email":str(data.get("email") or "").strip(),
      "website_url":str(data.get("website_url") or "").strip(),"sector":str(data.get("sector") or "").strip(),
      "lead_status":str(data.get("lead_status") or "new"),"website_status":str(data.get("website_status") or "unknown"),
      "notes":str(data.get("notes") or "").strip(),"audit_score":data.get("audit_score"),
      "audit_pdf":str(data.get("audit_pdf") or ""),"retainer_name":str(data.get("retainer_name") or "")}
    try:
        with connect(db_path) as conn:
            existing=conn.execute("SELECT id FROM contacts WHERE tenant_id=? AND lower(trim(business_name))=lower(trim(?)) AND location=? LIMIT 1",(tenant,name,location)).fetchone()
            if existing:
                cid=int(existing["id"])
                conn.execute("""UPDATE contacts SET
                  first_name=CASE WHEN first_name='' THEN ? ELSE first_name END,
                  surname=CASE WHEN surname='' THEN ? ELSE surname END,
                  phone=CASE WHEN phone='' THEN ? ELSE phone END,email=CASE WHEN email='' THEN ? ELSE email END,
                  website_url=CASE WHEN website_url='' THEN ? ELSE website_url END,
                  sector=CASE WHEN sector='' THEN ? ELSE sector END,
                  source_urls_json=CASE WHEN source_urls_json='[]' THEN ? ELSE source_urls_json END,
                  social_links_json=CASE WHEN social_links_json='[]' THEN ? ELSE social_links_json END,
                  updated_at=? WHERE id=? AND tenant_id=?""",
                  (fields['first_name'],fields['surname'],fields['phone'],fields['email'],fields['website_url'],fields['sector'],sources,social,now,cid,tenant))
                return cid
            cur=conn.execute("""INSERT INTO contacts(tenant_id,business_name,first_name,surname,phone,email,location,website_url,
              social_links_json,sector,lead_status,website_status,source_urls_json,notes,audit_score,audit_pdf,retainer_name,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (tenant,name,fields['first_name'],fields['surname'],fields['phone'],fields['email'],location,fields['website_url'],social,
               fields['sector'],fields['lead_status'],fields['website_status'],sources,fields['notes'],fields['audit_score'],
               fields['audit_pdf'],fields['retainer_name'],now,now))
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        # Legacy UNIQUE(business_name, location) is global across tenants. Never update another tenant's row.
        raise ValueError("A legacy cross-tenant uniqueness constraint blocks this duplicate business/location; existing record was not changed") from exc


def _derive_town(address: str, towns_found: list[str] | None = None, fallback: str = "") -> str:
    address_text = (address or "").casefold()
    # A contradictory explicit locality in the address is kept as other for review.
    if "cape town" in address_text and "port alfred" in address_text:
        return "other"
    for town in ("Port Alfred", "Kenton-on-Sea", "Bathurst", "Alexandria"):
        if town.casefold() in address_text:
            return town
    found_text = " ".join(towns_found or []).casefold()
    for town in ("Port Alfred", "Kenton-on-Sea", "Bathurst", "Alexandria"):
        if town.casefold() in found_text:
            return town
    fallback_text = (fallback or "").casefold()
    for town in ("Port Alfred", "Kenton-on-Sea", "Bathurst", "Alexandria"):
        if town.casefold() in fallback_text:
            return town
    return "other"


def _dedupe_key_name(name: str) -> str:
    # Remove common directory suffixes without changing the CRM display name.
    value = str(name or "").casefold().split(",")[0].split("(")[0]
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def reconcile_legacy_owned_domains(db_path: str | Path = DEFAULT_DB) -> int:
    """Attach a Google listing to a legacy profile only on a unique exact official domain match."""
    merged=0
    with connect(db_path) as conn:
        legacy=conn.execute("SELECT * FROM contacts WHERE website_detail='unknown' AND website_url<>'' ORDER BY id").fetchall()
        for old in legacy:
            host=(urlparse(str(old["website_url"] or "")).hostname or "").lower().removeprefix("www.")
            if not host: continue
            matches=[]
            for row in conn.execute("SELECT * FROM contacts WHERE id<>? AND place_id IS NOT NULL AND place_id<>''",(old["id"],)).fetchall():
                other=(urlparse(str(row["website_url"] or "")).hostname or "").lower().removeprefix("www.")
                if other and other==host: matches.append(row)
            if len(matches)!=1: continue
            src=matches[0]
            safe_delete=(src["notes"]=="Imported from Google Maps search. Verify details before outreach."
                         and src["lead_status"]=="new" and src["pipeline_stage"]=="new"
                         and not src["outreach_allowed"] and not src["details_verified"]
                         and src["audit_score"] is None and not src["audit_pdf"]
                         and conn.execute("SELECT count(*) FROM audit_history WHERE contact_id=?",(src["id"],)).fetchone()[0]==0)
            if not safe_delete: continue
            urls=list(dict.fromkeys(json.loads(old["source_urls_json"] or "[]")+json.loads(src["source_urls_json"] or "[]")))
            socials=list(dict.fromkeys(json.loads(old["social_links_json"] or "[]")+json.loads(src["social_links_json"] or "[]")))
            conn.execute("DELETE FROM contacts WHERE id=?",(src["id"],))
            conn.execute("""UPDATE contacts SET phone=CASE WHEN phone='' THEN ? ELSE phone END,
              address=CASE WHEN address='' THEN ? ELSE address END,rating=COALESCE(rating,?),review_count=COALESCE(review_count,?),
              latitude=COALESCE(latitude,?),longitude=COALESCE(longitude,?),place_id=?,website_detail=?,website_status=?,
              found_via=CASE WHEN found_via='' THEN ? ELSE found_via END,sector=CASE WHEN sector='' THEN ? ELSE sector END,
              categories_json=CASE WHEN categories_json='[]' THEN ? ELSE categories_json END,source_urls_json=?,social_links_json=?,
              town=CASE WHEN town='other' THEN ? ELSE town END,updated_at=? WHERE id=?""",
              (src["phone"],src["address"],src["rating"],src["review_count"],src["latitude"],src["longitude"],src["place_id"],
               src["website_detail"],src["website_status"],src["found_via"],src["sector"],src["categories_json"],
               json.dumps(urls,ensure_ascii=False),json.dumps(socials,ensure_ascii=False),src["town"],_now(),old["id"]))
            merged+=1
    return merged


def import_sweep(path: str | Path, db_path: str | Path | None = None, tenant_id: str | None = None) -> dict:
    payload=json.loads(Path(path).read_text(encoding="utf-8"))
    return import_sweep_payload(payload,db_path,tenant_id)


def import_sweep_payload(payload: dict, db_path: str | Path | None = None, tenant_id: str | None = None) -> dict:
    load_env()
    db_path = db_path or env("CRM_DATABASE_PATH", str(DEFAULT_DB))
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    daily_cap=max(1,int(env("CRM_SWEEP_MAX_NEW_PER_TOWN_PER_DAY","50")))
    today=datetime.now(timezone.utc).date().isoformat()
    from security_schema import migrate
    migrate(db_path, tenant)
    leads = payload.get("leads", [])
    before = len(list_contacts(db_path, tenant_id=tenant))
    processed_ids, merged, skipped_cap = [], 0, 0
    with tenant_context(tenant):
        with connect(db_path) as conn:
            existing = conn.execute("SELECT * FROM contacts WHERE tenant_id=? ORDER BY id", (tenant,)).fetchall()
            for lead in leads:
                name = str(lead.get("business_name") or "").strip()
                if not name or not eligible_for_crm(conn, lead, tenant):
                    continue
                place_id = str(lead.get("place_id") or "").strip()
                phone = str(lead.get("phone") or "").strip()
                address = str(lead.get("address") or "").strip()
                if not phone and name.casefold() == "kh construction":
                    continue
                town = _derive_town(address, lead.get("towns_found") or [], payload.get("town", ""))
                location = address or town
                match = None
                if place_id:
                    match = conn.execute("SELECT * FROM contacts WHERE tenant_id=? AND place_id=?", (tenant, place_id)).fetchone()
                if match is None and phone:
                    match = conn.execute("SELECT * FROM contacts WHERE tenant_id=? AND lower(trim(business_name))=lower(trim(?)) AND phone=? LIMIT 1", (tenant, name, phone)).fetchone()
                sources = list(dict.fromkeys(lead.get("directory_or_profile_sources") or []))
                categories = lead.get("categories") or []
                website_detail = str(lead.get("website_detail") or "unknown")
                own_website = website_detail == "own_website_listed"
                website_url = str(lead.get("website_url") or "").strip() if own_website else ""
                social_urls = [u for u in sources if any(host in u.casefold() for host in ("facebook.com", "instagram.com", "linkedin.com", "tiktok.com"))]
                social_json = json.dumps(list(dict.fromkeys(social_urls)), ensure_ascii=False)
                now = _now()
                if match:
                    merged += 1
                    cid = match["id"]
                    conn.execute("""UPDATE contacts SET
                      phone=CASE WHEN phone='' THEN ? ELSE phone END,address=CASE WHEN address='' THEN ? ELSE address END,
                      rating=COALESCE(rating,?),review_count=COALESCE(review_count,?),latitude=COALESCE(latitude,?),longitude=COALESCE(longitude,?),
                      place_id=CASE WHEN place_id IS NULL OR place_id='' THEN ? ELSE place_id END,
                      website_url=CASE WHEN website_detail='unknown' AND ?<>'' THEN ? ELSE website_url END,
                      website_status=CASE WHEN website_detail IN ('','unknown') THEN ? ELSE website_status END,
                      social_links_json=CASE WHEN social_links_json='[]' THEN ? ELSE social_links_json END,
                      website_detail=CASE WHEN website_detail IN ('','unknown') THEN ? ELSE website_detail END,
                      found_via=CASE WHEN found_via='' THEN ? ELSE found_via END,sector=CASE WHEN sector='' THEN ? ELSE sector END,
                      categories_json=CASE WHEN categories_json='[]' THEN ? ELSE categories_json END,
                      town=CASE WHEN town='other' THEN ? ELSE town END,
                      source_urls_json=?,updated_at=? WHERE tenant_id=? AND id=?""",
                      (phone,address,lead.get('rating'),lead.get('review_count'),lead.get('latitude'),lead.get('longitude'),place_id,
                       website_url,website_url,str(lead.get('website_status') or 'no_website_surfaced_in_search'),social_json,website_detail,
                       str(lead.get('found_via') or ''),str(lead.get('sector') or ''),json.dumps(categories,ensure_ascii=False),town,
                       json.dumps(list(dict.fromkeys(json.loads(match['source_urls_json'] or '[]')+sources)),ensure_ascii=False),now,tenant,cid))
                else:
                    todays_new=conn.execute("SELECT count(*) FROM contacts WHERE tenant_id=? AND town=? AND sweep_imported_at LIKE ?",(tenant,town,today+'%')).fetchone()[0]
                    if todays_new>=daily_cap:
                        skipped_cap+=1
                        continue
                    conn.execute("""INSERT INTO contacts(tenant_id,business_name,phone,email,location,address,website_url,social_links_json,sector,
                      lead_status,website_status,source_urls_json,notes,retainer_name,rating,review_count,latitude,longitude,place_id,
                      website_detail,found_via,categories_json,town,outreach_allowed,sweep_imported_at,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)""", 
                      (tenant,name,phone,str(lead.get('email') or ''),location,address,website_url,social_json,str(lead.get('sector') or ''),
                       str(lead.get('website_status') or 'no_website_surfaced_in_search'),json.dumps(sources,ensure_ascii=False),
                       'Imported from public Google Maps results. Verify details before outreach.','',lead.get('rating'),lead.get('review_count'),
                       lead.get('latitude'),lead.get('longitude'),place_id,website_detail,str(lead.get('found_via') or ''),
                       json.dumps(categories,ensure_ascii=False),town,now,now,now))
                    cid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                processed_ids.append(cid)
    after = len(list_contacts(db_path, tenant_id=tenant))
    return {"source_leads":len(leads),"processed":len(processed_ids),"merged_existing":merged,"new_rows":after-before,"skipped_daily_cap":skipped_cap,
            "rows_before":before,"rows_after":after,"ids":processed_ids,"tenant_id":tenant,"database":str(Path(db_path).resolve())}


def list_contacts(db_path: str | Path = DEFAULT_DB, website_status: str = "", limit: int = 500,
                  town: str = "", website_detail: str = "", stage: str = "",
                  outreach_allowed: int | None = None, tenant_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM contacts"
    params: list[Any] = []
    filters = []
    for column, value in (("website_status", website_status), ("town", town),
                          ("website_detail", website_detail), ("pipeline_stage", stage)):
        if value:
            filters.append(f"{column}=?")
            params.append(value)
    if outreach_allowed is not None:
        filters.append("outreach_allowed=?")
        params.append(int(outreach_allowed))
    filters.append("tenant_id=?")
    params.append(tenant_id or env("CRM_TENANT_ID", "straight-up-digital"))
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY location, business_name LIMIT ?"
    params.append(max(1, min(int(limit), 5000)))
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["social_links"] = json.loads(item.pop("social_links_json"))
        item["source_urls"] = json.loads(item.pop("source_urls_json"))
        item["categories"] = json.loads(item.pop("categories_json"))
        result.append(item)
    return result


def export_csv(path: str | Path, db_path: str | Path = DEFAULT_DB, website_status: str = "", tenant_id: str | None = None) -> int:
    rows = list_contacts(db_path, website_status, tenant_id=tenant_id)
    if not rows:
        fields = ["id", "business_name", "first_name", "surname", "phone", "email", "location", "website_url",
                  "social_links", "sector", "lead_status", "website_status", "source_urls", "notes", "audit_score", "audit_pdf", "retainer_name"]
    else:
        fields = list(rows[0].keys())
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in row.items()})
    return len(rows)


def main() -> None:
    load_env()
    from security_schema import migrate
    migrate(DEFAULT_DB, env("CRM_TENANT_ID", "straight-up-digital"))
    parser = argparse.ArgumentParser(description="Local Straight Up Digital SQLite CRM")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    imp = sub.add_parser("import-sweep")
    imp.add_argument("json_file")
    ls = sub.add_parser("list")
    ls.add_argument("--website-status", default="")
    exp = sub.add_parser("export")
    exp.add_argument("csv_file")
    exp.add_argument("--website-status", default="")
    add = sub.add_parser("add")
    for flag in ("business-name", "first-name", "surname", "phone", "email", "location", "website-url", "sector", "website-status", "notes"):
        add.add_argument("--" + flag, default="")
    args = parser.parse_args()
    if args.command == "init":
        with connect(args.db):
            pass
        out = {"initialized": True, "database": str(Path(args.db).resolve())}
    elif args.command == "import-sweep":
        out = import_sweep(args.json_file, args.db)
    elif args.command == "list":
        out = list_contacts(args.db, args.website_status)
    elif args.command == "export":
        out = {"exported": export_csv(args.csv_file, args.db, args.website_status), "csv": str(Path(args.csv_file).resolve())}
    else:
        data = {k.replace("-", "_"): getattr(args, k.replace("-", "_")) for k in
                ("business-name", "first-name", "surname", "phone", "email", "location", "website-url", "sector", "website-status", "notes")}
        out = {"id": add_contact(data, args.db)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
