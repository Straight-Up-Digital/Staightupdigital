"""Conservative CSV review/import helpers for the private local CRM."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from config_loader import env
from crm import connect


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def preview_csv(text: str, db_path, tenant_id: str) -> list[dict]:
    if len(text.encode("utf-8")) > 250_000:
        raise ValueError("CSV preview is limited to 250 KB")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must include a header row")
    aliases = {
        "business_name": ("business_name", "business", "company", "company_name", "name_of_business"),
        "email": ("email", "email_address"), "phone": ("phone", "phone_number", "mobile"),
        "website_url": ("website_url", "website", "url", "domain"), "town": ("town", "city", "locality"),
        "location": ("location", "address", "town", "city"), "sector": ("sector", "trade", "industry", "category"),
        "first_name": ("first_name", "contact_first_name"), "surname": ("surname", "last_name", "contact_surname"),
    }
    fieldmap = {str(name).strip().casefold(): name for name in reader.fieldnames}
    def pick(row, key):
        for alias in aliases[key]:
            actual = fieldmap.get(alias)
            if actual:
                return str(row.get(actual) or "").strip()[:500]
        return ""
    rows = []
    with connect(db_path) as conn:
        for idx, raw in enumerate(reader):
            if idx >= 500:
                raise ValueError("CSV review is limited to 500 rows at a time")
            data = {k: pick(raw, k) for k in aliases}
            if not data["business_name"]:
                continue
            if data["website_url"] and not data["website_url"].startswith(("https://", "http://")):
                data["website_url"] = "https://" + data["website_url"]
            if data["town"] and not data["location"]:
                data["location"] = data["town"]
            host = (urlparse(data["website_url"]).hostname or "").casefold().removeprefix("www.")
            phone_digits = re.sub(r"\D", "", data["phone"])
            candidates = {}
            if data["email"]:
                for r in conn.execute("SELECT id,business_name,town,email,phone,website_url,location FROM contacts WHERE tenant_id=? AND lower(email)=lower(?)", (tenant_id, data["email"])):
                    candidates[int(r["id"])] = dict(r)
            if phone_digits:
                for r in conn.execute("SELECT id,business_name,town,email,phone,website_url,location FROM contacts WHERE tenant_id=?", (tenant_id,)):
                    if re.sub(r"\D", "", r["phone"] or "") == phone_digits:
                        candidates[int(r["id"])] = dict(r)
            if host:
                for r in conn.execute("SELECT id,business_name,town,email,phone,website_url,location FROM contacts WHERE tenant_id=?", (tenant_id,)):
                    old_host = (urlparse(r["website_url"] or "").hostname or "").casefold().removeprefix("www.")
                    if old_host == host:
                        candidates[int(r["id"])] = dict(r)
            for r in conn.execute("SELECT id,business_name,town,email,phone,website_url,location FROM contacts WHERE tenant_id=?", (tenant_id,)):
                if _key(r["business_name"]) == _key(data["business_name"]) and _key(r["town"] or r["location"]) == _key(data["town"] or data["location"]):
                    candidates[int(r["id"])] = dict(r)
            rows.append({"row": idx, "data": data, "duplicates": list(candidates.values())})
    return rows


def commit_csv(rows: list[dict], actions: dict[str, str], db_path, tenant_id: str) -> dict:
    added = merged = skipped = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        for item in rows:
            idx = str(item["row"])
            action = str(actions.get(idx, "skip"))
            data = item["data"]
            if action == "skip":
                skipped += 1
                continue
            if action == "new":
                try:
                    conn.execute("INSERT INTO contacts(tenant_id,business_name,location,website_url,email,phone,sector,town,found_via,outreach_allowed,marketing_consent,details_verified,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,0,0,?,?,?)",
                                 (tenant_id, data["business_name"], data["location"] or data["town"], data["website_url"], data["email"], data["phone"], data["sector"], data["town"] or "other", "csv_import", "Imported from CSV; details unverified.", now, now))
                    added += 1
                except Exception:
                    skipped += 1
                continue
            if action.startswith("merge:"):
                try:
                    cid = int(action.split(":", 1)[1])
                except ValueError:
                    skipped += 1
                    continue
                if not any(int(x["id"]) == cid for x in item["duplicates"]):
                    skipped += 1
                    continue
                existing = conn.execute("SELECT id FROM contacts WHERE id=? AND tenant_id=?", (cid, tenant_id)).fetchone()
                if not existing:
                    skipped += 1
                    continue
                # Fill blank values only. A duplicate match never overwrites verified client details.
                mapping = {"first_name":"first_name", "surname":"surname", "phone":"phone", "email":"email", "website_url":"website_url", "sector":"sector", "town":"town", "location":"location"}
                changed = False
                for source, target in mapping.items():
                    value = data.get(source) or ""
                    if value:
                        cur = conn.execute(f"UPDATE contacts SET {target}=?,updated_at=? WHERE id=? AND tenant_id=? AND COALESCE({target},'')=''", (value, now, cid, tenant_id))
                        changed |= bool(cur.rowcount)
                if changed:
                    merged += 1
                else:
                    skipped += 1
        conn.commit()
    return {"added": added, "merged": merged, "skipped": skipped}
