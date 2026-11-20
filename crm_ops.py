"""Additive tenant-scoped operations for the Straight Up Digital local CRM.

Payments are not processed or verified by this module. Invoice PDFs may link to an
owner-configured hosted payment page; payment recording remains manual.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from config_loader import env
from security_schema import add_suppression
from crm import connect as _open_crm
from crm_pricing import monthly_recurring_revenue, pricing_from_config


ACTIVITY_TYPES = {"call", "whatsapp", "email", "meeting", "note", "other"}
PROJECT_STATUSES = {"waiting_content", "in_design", "in_build", "review", "live", "paused"}
RECURRING_TYPES = {"hosting", "retainer", "domain_renewal", "other"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _contact(conn, contact_id: int, tenant: str):
    row = conn.execute("SELECT * FROM contacts WHERE id=? AND tenant_id=?", (int(contact_id), tenant)).fetchone()
    if not row:
        raise LookupError("Contact not found in the active CRM")
    return row


def log_activity(contact_id: int, event_type: str, summary: str, outcome: str = "",
                 channel: str = "", direction: str = "outbound", db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    path = db_path or DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    event_type = str(event_type or "other").strip().lower()
    direction = str(direction or "outbound").strip().lower()
    if event_type not in ACTIVITY_TYPES or direction not in {"outbound", "inbound", "internal"}:
        raise ValueError("Choose a valid activity type and direction")
    summary, outcome = str(summary or "").strip()[:4000], str(outcome or "").strip()[:2000]
    if not summary:
        raise ValueError("Add a short activity summary")
    with _open_crm(path) as conn:
        _contact(conn, contact_id, tenant)
        cur = conn.execute("INSERT INTO crm_activities(tenant_id,contact_id,event_type,channel,direction,summary,outcome,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (tenant, int(contact_id), event_type, str(channel or "")[:100], direction, summary, outcome, now_utc()))
        conn.commit()
        return int(cur.lastrowid)


def list_activities(contact_id: int, db_path=None, tenant_id=None) -> list[dict]:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        conn.row_factory = sqlite3.Row
        _contact(conn, contact_id, tenant)
        return [dict(x) for x in conn.execute("SELECT * FROM crm_activities WHERE contact_id=? AND tenant_id=? ORDER BY created_at DESC,id DESC",
                                              (int(contact_id), tenant))]


def set_next_action_state(task_key: str, task_title: str, action: str, snooze_days: int = 7,
                          db_path=None, tenant_id=None) -> dict:
    """Update a reminder's state and append a tenant-scoped history event."""
    from crm import DEFAULT_DB

    key = str(task_key or "").strip()
    if not re.fullmatch(r"(?:followup|enquiry|audit|invoice|renewal|milestone):[A-Za-z0-9:_-]{1,180}", key):
        raise ValueError("Invalid next-action key")
    title = str(task_title or "").strip()[:250]
    if not title:
        raise ValueError("A short action title is required")
    action = str(action or "").strip().lower()
    if action == "complete":
        status, event, until = "completed", "completed", ""
    elif action == "snooze":
        try:
            days = int(snooze_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose a valid snooze period") from exc
        if days not in {1, 3, 7, 14, 30}:
            raise ValueError("Snooze period must be 1, 3, 7, 14 or 30 days")
        status, event = "open", "snoozed"
        until = (date.today() + timedelta(days=days)).isoformat()
    elif action == "reopen":
        status, event, until = "open", "reopened", ""
    else:
        raise ValueError("Choose complete, snooze or reopen")

    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    now = now_utc()
    with _open_crm(db_path or DEFAULT_DB) as conn:
        current = conn.execute("SELECT status,snoozed_until FROM crm_next_action_state WHERE tenant_id=? AND task_key=?",
                               (tenant, key)).fetchone()
        if action == "complete" and current and current["status"] == "completed":
            return {"task_key": key, "status": "completed", "snoozed_until": "", "unchanged": True}
        conn.execute("""INSERT INTO crm_next_action_state(tenant_id,task_key,status,snoozed_until,updated_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,task_key) DO UPDATE SET
                        status=excluded.status,snoozed_until=excluded.snoozed_until,updated_at=excluded.updated_at""",
                     (tenant, key, status, until, now))
        conn.execute("INSERT INTO crm_next_action_history(tenant_id,task_key,task_title,action,snoozed_until,created_at) VALUES(?,?,?,?,?,?)",
                     (tenant, key, title, event, until, now))
        conn.commit()
    return {"task_key": key, "status": status, "snoozed_until": until, "unchanged": False}


def list_next_action_history(db_path=None, tenant_id=None, limit: int = 50) -> list[dict]:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    limit = max(1, min(100, int(limit)))
    with _open_crm(db_path or DEFAULT_DB) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT task_key,task_title,action,snoozed_until,created_at FROM crm_next_action_history WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (tenant, limit),
        )]


def top_audit_finding(contact_id: int, db_path=None, tenant_id=None) -> str:
    from crm import audit_history, DEFAULT_DB
    history = audit_history(contact_id, db_path or DEFAULT_DB, tenant_id)
    if not history:
        return ""
    full = history[0].get("full_audit") or {}
    opportunities = full.get("opportunities") or []
    return str((opportunities[0] or {}).get("title") or "") if opportunities else ""


def personalised_templates(contact: dict, finding: str = "") -> dict[str, str]:
    name = str(contact.get("business_name") or "your business").strip()
    top = finding.strip() or "a few opportunities to improve how your website supports customers"
    try:
        from crm_tools import load_templates, render_template
        return {k: render_template(v, name, top, contact) for k, v in load_templates().items()}
    except Exception:
        pass
    return {
        "whatsapp_audit": f"Hi, this is St. John from Straight Up Digital. I had a look at {name}'s website and noticed {top}. I can explain what it means for customers and what to fix first. Would a quick chat suit you?",
        "email_audit_subject": f"A few website improvements for {name}",
        "email_audit_body": f"Hello,\n\nI reviewed {name}'s public website and noticed {top}. This can make it harder for customers to find information or take the next step.\n\nI can talk you through the finding and a practical fix. Would you prefer a call or a short meeting?\n\nRegards,\nSt. John Ritchie\nStraight Up Digital\nPort Alfred, Eastern Cape",
        "whatsapp_followup": f"Hi, just following up on my note about {name}. I can explain the main website improvement and answer any questions. Would a short call work for you?",
    }


def create_quote(contact_id: int, description: str, amount: float, status: str = "draft",
                 due_at: str = "", db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    status = status if status in {"draft", "sent", "accepted", "declined", "expired"} else "draft"
    amount = float(amount)
    if amount < 0 or amount > 1_000_000_000:
        raise ValueError("Quote amount is outside the allowed range")
    now = now_utc()
    with _open_crm(db_path or DEFAULT_DB) as conn:
        _contact(conn, contact_id, tenant)
        seq = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM crm_quotes WHERE tenant_id=?", (tenant,)).fetchone()[0]
        number = f"SUD-Q-{date.today():%Y%m}-{seq:04d}"
        cur = conn.execute("INSERT INTO crm_quotes(tenant_id,contact_id,quote_number,description,amount,status,sent_at,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tenant, int(contact_id), number, str(description or "").strip()[:2000], amount, status,
                            now if status == "sent" else "", str(due_at or ""), now, now))
        conn.commit()
        return int(cur.lastrowid)


def create_invoice(contact_id: int, description: str, amount: float, due_on: str,
                   quote_id: int | None = None, db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    amount = round(float(amount), 2)
    if amount <= 0 or amount > 1_000_000_000:
        raise ValueError("Invoice amount must be greater than zero")
    date.fromisoformat(due_on)
    now = now_utc()
    with _open_crm(db_path or DEFAULT_DB) as conn:
        _contact(conn, contact_id, tenant)
        if quote_id is not None and not conn.execute("SELECT 1 FROM crm_quotes WHERE id=? AND contact_id=? AND tenant_id=?", (quote_id, contact_id, tenant)).fetchone():
            raise LookupError("Quote not found for this client")
        seq = max(48, int(conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM crm_invoices WHERE tenant_id=?", (tenant,)).fetchone()[0]))
        while True:
            number = f"{seq:04d}"
            if not conn.execute("SELECT 1 FROM crm_invoices WHERE invoice_number=? AND tenant_id=?", (number, tenant)).fetchone():
                break
            seq += 1
        cur = conn.execute("INSERT INTO crm_invoices(tenant_id,contact_id,quote_id,invoice_number,description,amount,issued_on,due_on,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tenant, int(contact_id), quote_id, number, str(description or "").strip()[:2000], amount,
                            date.today().isoformat(), due_on, "issued", now))
        conn.commit()
        return int(cur.lastrowid)


def invoice_details(invoice_id: int, db_path=None, tenant_id=None) -> dict:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT i.*,c.business_name,c.first_name,c.surname,c.email,c.phone,c.address,c.town FROM crm_invoices i JOIN contacts c ON c.id=i.contact_id AND c.tenant_id=i.tenant_id WHERE i.id=? AND i.tenant_id=?", (int(invoice_id), tenant)).fetchone()
        if not row:
            raise LookupError("Invoice not found in the active CRM")
        data = dict(row)
        data["payments"] = [dict(x) for x in conn.execute("SELECT * FROM crm_payments WHERE invoice_id=? AND tenant_id=? ORDER BY paid_on", (int(invoice_id), tenant))]
    return data


def delete_invoice(invoice_id: int, db_path=None, tenant_id=None) -> int:
    """Delete an unpaid invoice and its generated PDF; keep all invoices with payments for the audit trail."""
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        row = conn.execute("SELECT contact_id,invoice_number,status FROM crm_invoices WHERE id=? AND tenant_id=?", (int(invoice_id), tenant)).fetchone()
        if not row:
            raise LookupError("Invoice not found in the active CRM")
        payments = int(conn.execute("SELECT COUNT(*) FROM crm_payments WHERE invoice_id=? AND tenant_id=?", (int(invoice_id), tenant)).fetchone()[0])
        if payments or row["status"] in {"paid", "part_paid"}:
            raise ValueError("Invoices with recorded payments cannot be deleted; keep the payment history intact")
        conn.execute("DELETE FROM crm_invoices WHERE id=? AND tenant_id=?", (int(invoice_id), tenant))
        conn.commit()
        contact_id, invoice_number = int(row["contact_id"]), str(row["invoice_number"])
    invoice_dir = (Path(__file__).resolve().parent / "reports" / "invoices").resolve()
    pdf_path = (invoice_dir / f"{invoice_number}.pdf").resolve()
    if pdf_path.is_relative_to(invoice_dir):
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError as exc:
            print("Could not remove deleted invoice PDF:", type(exc).__name__)
    return contact_id


def record_payment(contact_id: int, amount: float, paid_on: str, invoice_id: int | None = None,
                   method: str = "manual", reference: str = "", notes: str = "", db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    amount = round(float(amount), 2)
    if amount <= 0 or amount > 1_000_000_000:
        raise ValueError("Payment amount must be greater than zero")
    date.fromisoformat(paid_on)
    with _open_crm(db_path or DEFAULT_DB) as conn:
        _contact(conn, contact_id, tenant)
        if invoice_id is not None:
            invoice = conn.execute("SELECT amount,status FROM crm_invoices WHERE id=? AND contact_id=? AND tenant_id=?", (invoice_id, contact_id, tenant)).fetchone()
            if not invoice or invoice[1] == "void":
                raise LookupError("Valid invoice not found for this client")
            paid = float(conn.execute("SELECT COALESCE(SUM(amount),0) FROM crm_payments WHERE invoice_id=? AND tenant_id=?", (invoice_id, tenant)).fetchone()[0])
            if round(paid + amount, 2) > round(float(invoice[0]), 2):
                raise ValueError("Payment would exceed the invoice balance")
        cur = conn.execute("INSERT INTO crm_payments(tenant_id,contact_id,invoice_id,amount,paid_on,method,reference,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (tenant, int(contact_id), invoice_id, amount, paid_on, str(method or "manual")[:40], str(reference or "")[:200], str(notes or "")[:2000], now_utc()))
        if invoice_id is not None:
            paid = float(conn.execute("SELECT COALESCE(SUM(amount),0) FROM crm_payments WHERE invoice_id=? AND tenant_id=?", (invoice_id, tenant)).fetchone()[0])
            total = float(conn.execute("SELECT amount FROM crm_invoices WHERE id=? AND tenant_id=?", (invoice_id, tenant)).fetchone()[0])
            status = "paid" if round(paid, 2) >= round(total, 2) else "part_paid"
            conn.execute("UPDATE crm_invoices SET status=? WHERE id=? AND tenant_id=?", (status, invoice_id, tenant))
        conn.commit()
        return int(cur.lastrowid)


def save_recurring_item(contact_id: int, item_type: str, description: str, amount: float,
                        frequency: str, next_due_on: str, item_id: int | None = None,
                        db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    if item_type not in RECURRING_TYPES or frequency not in {"monthly", "annual"}:
        raise ValueError("Choose a supported recurring service and frequency")
    amount = round(float(amount), 2)
    if amount < 0 or amount > 1_000_000_000:
        raise ValueError("Recurring amount is outside the allowed range")
    if next_due_on:
        date.fromisoformat(next_due_on)
    with _open_crm(db_path or DEFAULT_DB) as conn:
        _contact(conn, contact_id, tenant)
        if item_id:
            cur = conn.execute("UPDATE crm_recurring_items SET item_type=?,description=?,amount=?,frequency=?,next_due_on=?,updated_at=? WHERE id=? AND contact_id=? AND tenant_id=?",
                               (item_type, str(description or "")[:500], amount, frequency, next_due_on, now_utc(), item_id, contact_id, tenant))
            if cur.rowcount != 1:
                raise LookupError("Recurring item not found")
            result = int(item_id)
        else:
            cur = conn.execute("INSERT INTO crm_recurring_items(tenant_id,contact_id,item_type,description,amount,frequency,next_due_on,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (tenant, int(contact_id), item_type, str(description or "")[:500], amount, frequency, next_due_on, now_utc(), now_utc()))
            result = int(cur.lastrowid)
        conn.commit()
        return result


def mark_recurring_paid(item_id: int, paid_on: str, db_path=None, tenant_id=None) -> None:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    date.fromisoformat(paid_on)
    with _open_crm(db_path or DEFAULT_DB) as conn:
        row=conn.execute("SELECT frequency,next_due_on FROM crm_recurring_items WHERE id=? AND tenant_id=? AND active=1",(int(item_id),tenant)).fetchone()
        if not row: raise LookupError("Active recurring item not found")
        next_due=str(row[1] or "")
        try: due=date.fromisoformat(next_due) if next_due else date.fromisoformat(paid_on)
        except ValueError: due=date.fromisoformat(paid_on)
        paid=date.fromisoformat(paid_on)
        if due<=paid: next_due=_add_months(due,1 if row[0]=="monthly" else 12).isoformat()
        cur = conn.execute("UPDATE crm_recurring_items SET last_paid_on=?,next_due_on=?,updated_at=? WHERE id=? AND tenant_id=? AND active=1", (paid_on,next_due,now_utc(),int(item_id),tenant))
        if cur.rowcount != 1:
            raise LookupError("Active recurring item not found")
        conn.commit()


def save_project(contact_id: int, fields: dict, db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    status = str(fields.get("status") or "waiting_content")
    if status not in PROJECT_STATUSES:
        raise ValueError("Choose a valid project status")
    for key in ("started_on", "go_live_on", "next_milestone_on"):
        if fields.get(key):
            date.fromisoformat(str(fields[key]))
    values = (status, str(fields.get("domain") or "")[:255], str(fields.get("hosting_notes") or "")[:4000],
              str(fields.get("project_notes") or "")[:4000], str(fields.get("started_on") or ""),
              str(fields.get("go_live_on") or ""), str(fields.get("next_milestone") or "")[:500],
              str(fields.get("next_milestone_on") or ""), now_utc())
    with _open_crm(db_path or DEFAULT_DB) as conn:
        _contact(conn, contact_id, tenant)
        conn.execute("INSERT INTO crm_projects(tenant_id,contact_id,status,domain,hosting_notes,project_notes,started_on,go_live_on,next_milestone,next_milestone_on,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,contact_id) DO UPDATE SET status=excluded.status,domain=excluded.domain,hosting_notes=excluded.hosting_notes,project_notes=excluded.project_notes,started_on=excluded.started_on,go_live_on=excluded.go_live_on,next_milestone=excluded.next_milestone,next_milestone_on=excluded.next_milestone_on,updated_at=excluded.updated_at",
                      (tenant, int(contact_id), *values))
        row = conn.execute("SELECT id FROM crm_projects WHERE tenant_id=? AND contact_id=?", (tenant, int(contact_id))).fetchone()
        conn.commit()
        return int(row[0])


def save_website_enquiry(external_id: str, fields: dict, received_at: str, db_path=None, tenant_id=None) -> tuple[int, int, bool]:
    """Idempotently import one site enquiry; returns (contact_id, enquiry_id, was_new)."""
    from crm import DEFAULT_DB, add_contact
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    ext = str(external_id or "").strip()
    if not ext or len(ext) > 200:
        raise ValueError("A stable website submission ID is required")
    received_at = str(received_at or now_utc())[:40]
    name = str(fields.get("business_name") or fields.get("businessName") or fields.get("company") or "").strip()
    contact_name = str(fields.get("name") or fields.get("contact_name") or "")[:300]
    email = str(fields.get("email") or "").strip()
    phone = str(fields.get("phone") or "").strip()
    message = str(fields.get("message") or fields.get("details") or "").strip()[:5000]
    form_type = str(fields.get("form_type") or fields.get("formType") or "contact")[:50]
    if not name:
        name = f"Website enquiry ({ext[:12]})"
    with _open_crm(db_path or DEFAULT_DB) as conn:
        existing = conn.execute("SELECT id,contact_id FROM crm_website_enquiries WHERE tenant_id=? AND external_id=?", (tenant, ext)).fetchone()
        if existing:
            return int(existing["contact_id"] or 0), int(existing["id"]), False
    contact_id = add_contact({"business_name": name, "first_name": fields.get("first_name") or fields.get("firstName") or "",
                              "surname": fields.get("surname") or "", "email": email, "phone": phone,
                              "location": fields.get("location") or fields.get("town") or "Not provided", "town": fields.get("town") or "other",
                              "website_url": fields.get("website") or fields.get("website_url") or fields.get("websiteUrl") or "",
                              "sector": fields.get("sector") or fields.get("service") or "",
                              "notes": "Website enquiry: " + message[:1000]}, db_path or DEFAULT_DB, tenant)
    with _open_crm(db_path or DEFAULT_DB) as conn:
        existing = conn.execute("SELECT id FROM crm_website_enquiries WHERE tenant_id=? AND external_id=?", (tenant, ext)).fetchone()
        if existing:
            return int(contact_id), int(existing[0]), False
        cur = conn.execute("INSERT INTO crm_website_enquiries(tenant_id,external_id,business_name,contact_name,email,phone,message,form_type,received_at,consent_given,consent_text_version,consent_timestamp,source,contact_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (tenant, ext, name, contact_name, email, phone, message, form_type, received_at,
                            int(bool(fields.get("consentGiven") or fields.get("consent_given"))),
                            str(fields.get("consentTextVersion") or fields.get("consent_text_version") or "")[:100],
                            str(fields.get("consentTimestamp") or fields.get("consent_timestamp") or "")[:40],
                            str(fields.get("source") or "website")[:200], contact_id))
        conn.execute("UPDATE contacts SET found_via='website',outreach_allowed=0,active_outreach=0,lead_status='new',pipeline_stage='new',updated_at=? WHERE id=? AND tenant_id=?",
                     (now_utc(), contact_id, tenant))
        conn.commit()
        enquiry_id = int(cur.lastrowid)
    log_activity(contact_id, "other", f"Website enquiry received ({form_type})", message[:1500], "Website form", "inbound", db_path, tenant)
    return contact_id, enquiry_id, True


def create_database_backup(db_path=None, backup_dir=None) -> Path:
    from crm import DEFAULT_DB
    source = Path(db_path or DEFAULT_DB).resolve()
    target_dir = Path(backup_dir or source.parent.parent / "backups").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"straight_up_digital_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    src_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(out)
    try:
        src_conn.backup(dst_conn)
        result = dst_conn.execute("PRAGMA integrity_check").fetchone()[0]
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()
    if result != "ok":
        out.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(f"Backup verification failed: {result}")
    return out


def erase_contact(contact_id: int, confirmation: str, db_path=None, tenant_id=None) -> dict:
    """Remove a lead and its private CRM artifacts, retaining only hashed DNC identifiers."""
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    if confirmation != "DELETE":
        raise ValueError('Type DELETE to confirm removal of this contact and associated data')
    path = Path(db_path or DEFAULT_DB)
    report_paths = []
    with _open_crm(path) as conn:
        published = conn.execute("SELECT client_report_url FROM audit_history WHERE contact_id=? AND tenant_id=? AND client_report_url<>''", (int(contact_id), tenant)).fetchall()
    if published:
        from customer_report_publisher import delete_published_report
        # Keep local PII intact until each shareable report is revoked successfully.
        for row in published:
            delete_published_report(row[0])
    with _open_crm(path) as conn:
        conn.row_factory = sqlite3.Row
        contact = conn.execute("SELECT * FROM contacts WHERE id=? AND tenant_id=?", (int(contact_id), tenant)).fetchone()
        if not contact:
            raise LookupError("Contact not found in the active CRM")
        # Retain hashes, never raw identifying values, so an opted-out person is not re-imported.
        for kind, value in (("email", contact["email"]), ("phone", contact["phone"]), ("place_id", contact["place_id"])):
            if value:
                add_suppression(conn, kind, str(value), "Erasure request; do not re-import", tenant)
        audits = conn.execute("SELECT pdf_path,json_path FROM audit_history WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant)).fetchall()
        for audit in audits:
            for raw in (audit["pdf_path"], audit["json_path"]):
                if raw:
                    candidate = Path(raw)
                    if not candidate.is_absolute():
                        candidate = path.parent.parent / candidate
                    try:
                        resolved = candidate.resolve()
                        if resolved.is_relative_to((path.parent.parent / "reports").resolve()):
                            report_paths.append(resolved)
                    except (OSError, ValueError):
                        pass
        conn.execute("DELETE FROM crm_activities WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_payments WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_invoices WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_quotes WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_recurring_items WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_projects WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM crm_website_enquiries WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM audit_jobs WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM audit_history WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.execute("DELETE FROM contacts WHERE id=? AND tenant_id=?", (int(contact_id), tenant))
        conn.commit()
    deleted = 0
    for candidate in set(report_paths):
        try:
            candidate.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass
    return {"deleted": True, "report_files_removed": deleted, "suppression_hashes_retained": True}


def sales_metrics(db_path=None, tenant_id=None) -> dict:
    from crm import DEFAULT_DB
    tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        conn.row_factory = sqlite3.Row
        contacts = [dict(r) for r in conn.execute("SELECT id,town,sector,pipeline_stage,quote_amount,monthly_fee,hosting_enabled,retainer_name,created_at FROM contacts WHERE tenant_id=?", (tenant,))]
        activities = [dict(r) for r in conn.execute("SELECT a.contact_id,a.created_at FROM crm_activities a WHERE a.tenant_id=? AND a.direction='outbound' AND a.event_type IN ('call','whatsapp','email','meeting') ORDER BY a.created_at", (tenant,))]
        audit_jobs = {r[0]: r[1] for r in conn.execute("SELECT contact_id,COUNT(*) FROM audit_history WHERE tenant_id=? GROUP BY contact_id", (tenant,))}
    total = len(contacts)
    stage = {s: sum(c["pipeline_stage"] == s for c in contacts) for s in ("new", "contacted", "quoted", "accepted", "live", "lost")}
    won = stage["accepted"] + stage["live"]
    town_stats, sector_stats = {}, {}
    for c in contacts:
        for bucket, key in ((town_stats, c.get("town") or "Unknown"), (sector_stats, c.get("sector") or "Unknown")):
            v = bucket.setdefault(key, {"leads": 0, "won": 0})
            v["leads"] += 1
            v["won"] += int(c.get("pipeline_stage") in ("accepted", "live"))
    forecast = sum(float(c.get("quote_amount") or 0) * (1.0 if c.get("pipeline_stage") in ("accepted", "live") else .35 if c.get("pipeline_stage") == "quoted" else .1 if c.get("pipeline_stage") == "contacted" else 0) for c in contacts)
    config_path = Path(__file__).resolve().with_name("auditor_config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configured_pricing = pricing_from_config(config)
    monthly = monthly_recurring_revenue(
        contacts, configured_pricing["hosting"], configured_pricing["retainers"]
    )
    first_by_contact = {}
    for a in activities:
        first_by_contact.setdefault(a["contact_id"], a["created_at"])
    hours = []
    by_id = {c["id"]: c for c in contacts}
    for cid, first in first_by_contact.items():
        try:
            created = datetime.fromisoformat(by_id[cid]["created_at"].replace("Z", "+00:00"))
            contacted = datetime.fromisoformat(first.replace("Z", "+00:00"))
            if created.tzinfo is None: created = created.replace(tzinfo=timezone.utc)
            if contacted.tzinfo is None: contacted = contacted.replace(tzinfo=timezone.utc)
            hours.append(max(0, (contacted - created).total_seconds() / 3600))
        except (KeyError, TypeError, ValueError):
            continue
    return {"total": total, "stages": stage, "won": won, "conversion_pct": round(won / total * 100, 1) if total else 0,
            "weighted_pipeline_zar": round(forecast, 2), "monthly_live_zar": round(monthly, 2),
            "median_first_contact_hours": round(sorted(hours)[len(hours)//2], 1) if hours else None,
            "towns": town_stats, "sectors": sector_stats,
            "audited_contacts": len(audit_jobs), "audits": sum(audit_jobs.values())}


def _pdf_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    assets = Path(__file__).resolve().parent / "assets" / "brand" / "fonts"
    for name, filename in (("SUDInter", "Inter-Regular.ttf"), ("SUDInter-Bold", "Inter-Bold.ttf"),
                           ("SUDMontserrat", "Montserrat-Bold.ttf")):
        font_path = assets / filename
        if font_path.is_file() and name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return ("SUDInter" if "SUDInter" in pdfmetrics.getRegisteredFontNames() else "Helvetica",
            "SUDInter-Bold" if "SUDInter-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold",
            "SUDMontserrat" if "SUDMontserrat" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold")

def create_invoice_pdf(invoice_id: int, output_dir=None, db_path=None, tenant_id=None) -> Path:
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    data = invoice_details(invoice_id, db_path, tenant_id)
    paid = round(sum(float(x["amount"]) for x in data["payments"]), 2)
    balance = max(0, round(float(data["amount"]) - paid, 2))
    base = Path(__file__).resolve().parent
    target_dir = Path(output_dir or base / "reports" / "invoices").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{data['invoice_number']}.pdf"
    font, bold, display = _pdf_fonts()
    green, dark, muted = colors.HexColor("#A8FF65"), colors.HexColor("#0A100C"), colors.HexColor("#667168")
    styles = getSampleStyleSheet()
    title = ParagraphStyle("InvoiceTitle", parent=styles["Title"], fontName=display, fontSize=23, leading=28, textColor=dark, alignment=TA_RIGHT)
    body = ParagraphStyle("InvoiceBody", parent=styles["BodyText"], fontName=font, fontSize=9.5, leading=14, textColor=dark)
    small = ParagraphStyle("InvoiceSmall", parent=body, fontSize=8.5, leading=12, textColor=muted)
    heading = ParagraphStyle("InvoiceHeading", parent=body, fontName=bold, fontSize=10.5, leading=14, textColor=dark)
    right = ParagraphStyle("InvoiceRight", parent=body, alignment=TA_RIGHT)
    # The "light" asset has dark artwork for use on white/light invoice paper.
    logo = base / "assets" / "brand" / "straight-up-digital-logo-light.png"
    brand_cell = [Image(str(logo), width=54*mm, height=15*mm, kind="proportional")] if logo.is_file() else [Paragraph("Straight Up Digital", heading)]
    brand_cell += [Paragraph("St. John Ritchie trading as Straight Up Digital", small),
                   Paragraph("Port Alfred, Eastern Cape, South Africa", small),
                   Paragraph("straightupdigital@zohomail.com &nbsp;|&nbsp; 062 065 3503", small)]
    story = [Table([[brand_cell, Paragraph("INVOICE", title)]], colWidths=[112*mm, 62*mm], style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("ALIGN",(1,0),(1,0),"RIGHT"),("BOTTOMPADDING",(0,0),(-1,-1),12)])),
             Spacer(1, 9*mm)]
    invoice_data = [[Paragraph("INVOICE DETAILS", heading), Paragraph("BILL TO", heading)],
                    [Paragraph(f"<b>Number:</b> {escape(str(data['invoice_number']))}<br/><b>Issued:</b> {escape(str(data['issued_on']))}<br/><b>Due:</b> {escape(str(data['due_on']))}", body),
                     Paragraph(f"<b>{escape(str(data['business_name']))}</b><br/>{escape(str(data.get('first_name','')))} {escape(str(data.get('surname','')))}<br/>{escape(str(data.get('email','')))}<br/>{escape(str(data.get('phone','')))}<br/>{escape(str(data.get('address') or data.get('town') or ''))}", body)]]
    story.append(Table(invoice_data, colWidths=[87*mm,87*mm], style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EEF4EA")),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#D9DED8")),("INNERGRID",(0,0),(-1,-1),.35,colors.HexColor("#D9DED8")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9)])))
    story += [Spacer(1, 10*mm)]
    rows = [[Paragraph("DESCRIPTION", heading), Paragraph("AMOUNT (ZAR)", ParagraphStyle("rhead", parent=heading, alignment=TA_RIGHT))],
            [Paragraph(escape(str(data["description"] or "Professional services")), body), Paragraph(f"R{float(data['amount']):,.2f}", right)]]
    story.append(Table(rows, colWidths=[126*mm,48*mm], style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EEF4EA")),("LINEBELOW",(0,0),(-1,0),.8,green),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#D9DED8")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)])))
    totals = [["Invoice total", f"R{float(data['amount']):,.2f}"], ["Payments recorded", f"R{paid:,.2f}"], ["Balance due", f"R{balance:,.2f}"]]
    paystack_url = str(env("PAYSTACK_PAYMENT_PAGE_URL", "") or "").strip()
    parsed_paystack = urlparse(paystack_url) if paystack_url else None
    paystack_host = (parsed_paystack.hostname or "").lower() if parsed_paystack else ""
    if not parsed_paystack or parsed_paystack.scheme != "https" or not parsed_paystack.path or not (paystack_host == "paystack.com" or paystack_host.endswith(".paystack.com")):
        paystack_url = ""
    payment_heading = Paragraph("PAYMENT OPTIONS - ONCE-OFF", heading)
    eft_details = Paragraph(
        "<b>Electronic funds transfer (EFT)</b><br/>Bank: Capitec Bank<br/>Account name: Straight Up Digital<br/>Account number: 2624863490<br/>Payment reference: " + escape(str(data["business_name"])) + " (client business name)", body)
    if paystack_url:
        button_style = ParagraphStyle("PaystackButton", parent=body, alignment=TA_LEFT, fontName=bold, fontSize=10, leading=14, textColor=colors.HexColor("#07100A"))
        button = Table([[Paragraph(f'<link href="{escape(paystack_url, quote=True)}"><font color="#07100A"><b>PAY ONLINE WITH PAYSTACK</b></font></link>', button_style)]],
                       colWidths=[64*mm], style=TableStyle([("BACKGROUND",(0,0),(-1,-1),green),("BOX",(0,0),(-1,-1),.6,colors.HexColor("#79B842")),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
        online_details = [button, Spacer(1,2*mm), Paragraph("Use the Paystack page instructions for the once-off amount. If asked for a payment reference, use the client business name shown on this invoice.", small)]
    else:
        online_details = [Paragraph("Pay online with Paystack", heading), Paragraph("Not enabled yet. Configure PAYSTACK_PAYMENT_PAGE_URL with your hosted Paystack page URL to show the payment button on every invoice.", small)]
    story += [Spacer(1,5*mm), Table(totals, colWidths=[126*mm,48*mm], style=TableStyle([("ALIGN",(1,0),(1,-1),"RIGHT"),("FONTNAME",(0,0),(-1,-2),font),("FONTNAME",(0,-1),(-1,-1),bold),("FONTSIZE",(0,0),(-1,-1),10),("TEXTCOLOR",(0,-1),(-1,-1),dark),("LINEABOVE",(0,-1),(-1,-1),1,green),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)])),
             Spacer(1,5*mm), payment_heading,
             Table([[eft_details, online_details]], colWidths=[87*mm,87*mm], style=TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#F5F8F3")),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#D9DED8")),("INNERGRID",(0,0),(-1,-1),.35,colors.HexColor("#D9DED8")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9)])),
             Spacer(1,5*mm), Paragraph("Please pay the balance shown above. Payments are recorded manually; a successful Paystack redirect alone is not confirmation that funds have cleared.", small),
             Spacer(1,3*mm), Paragraph("No VAT amount or VAT registration details are shown. This document is not represented as a tax invoice.", small)]
    doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=17*mm, bottomMargin=17*mm, title=f"Invoice {data['invoice_number']}", author="St. John Ritchie trading as Straight Up Digital")
    def decorate(canvas, _doc):
        canvas.saveState(); canvas.setFillColor(dark); canvas.rect(0,A4[1]-3*mm,A4[0],3*mm,fill=1,stroke=0); canvas.setFillColor(green); canvas.rect(18*mm,12*mm,22*mm,1.2*mm,fill=1,stroke=0); canvas.restoreState()
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return out

def create_monthly_report_pdf(contact_id: int, month: str, output_dir=None, db_path=None, tenant_id=None) -> Path:
    from calendar import monthrange
    from html import escape
    from crm import DEFAULT_DB, audit_history, get_contact
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    if not re.fullmatch(r"\d{4}-\d{2}", month): raise ValueError("Month must be YYYY-MM")
    year, mon = map(int, month.split("-")); start = f"{year:04d}-{mon:02d}-01"; end = f"{year:04d}-{mon:02d}-{monthrange(year,mon)[1]:02d}"
    base = Path(__file__).resolve().parent; db = db_path or DEFAULT_DB; tenant = tenant_id or env("CRM_TENANT_ID", "straight-up-digital")
    contact = get_contact(contact_id, db, tenant)
    if not contact: raise LookupError("Contact not found in the active CRM")
    history = audit_history(contact_id, db, tenant)
    period = [x for x in history if start <= str(x.get("audit_date", ""))[:10] <= end]
    if not period: raise ValueError("There is no saved audit in this month to report")
    latest = period[0]; prev = next((x for x in history if str(x.get("audit_date", ""))[:10] < start), None)
    score = latest.get("score"); old_score = prev.get("score") if prev else None
    delta = int(score)-int(old_score) if score is not None and old_score is not None else None
    scope = latest.get("scope_items", []) or []; done = sum(bool(x.get("done")) for x in scope)
    full = latest.get("full_audit") or {}; findings = (full.get("opportunities") or [])[:3]
    outdir = Path(output_dir or base / "reports" / "monthly").resolve(); outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"monthly-{int(contact_id)}-{month}.pdf"; font,bold,display = _pdf_fonts()
    ink=colors.HexColor("#172018"); green=colors.HexColor("#A8FF65"); muted=colors.HexColor("#637067")
    styles=getSampleStyleSheet(); title=ParagraphStyle("MonthlyTitle",parent=styles["Title"],fontName=display,fontSize=20,leading=25,textColor=ink,alignment=0)
    body=ParagraphStyle("MonthlyBody",parent=styles["BodyText"],fontName=font,fontSize=9.5,leading=14,textColor=ink); small=ParagraphStyle("MonthlySmall",parent=body,fontSize=8.5,leading=12,textColor=muted); head=ParagraphStyle("MonthlyHead",parent=body,fontName=bold,fontSize=10.5,leading=14)
    logo=base/"assets"/"brand"/"straight-up-digital-logo-dark.png"
    story=[Image(str(logo),width=52*mm,height=15*mm,kind="proportional")] if logo.is_file() else [Paragraph("Straight Up Digital",head)]
    story += [Spacer(1,5*mm),Paragraph("Monthly Digital Progress",title),Paragraph(f"{escape(contact['business_name'])} &nbsp;|&nbsp; {month}",body),Spacer(1,5*mm)]
    label=f"{int(score)}/100" if score is not None else "Not scored"; compare=(f"{int(old_score)}/100 before this month" if old_score is not None else "No earlier audit is available for comparison")
    change=(f"{delta:+d} points" if delta is not None else "Baseline established")
    story.append(Table([[Paragraph("LATEST REVIEW",head),Paragraph("CHANGE",head),Paragraph("FIXES TRACKED",head)],[Paragraph(f"{label}<br/><font color='#637067'>{str(latest.get('audit_date',''))[:10]}</font>",body),Paragraph(f"{change}<br/><font color='#637067'>{escape(compare)}</font>",body),Paragraph(f"{done} of {len(scope)} scope items complete",body)]],colWidths=[58*mm,58*mm,58*mm],style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EEF4EA")),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#D9DED8")),("INNERGRID",(0,0),(-1,-1),.35,colors.HexColor("#D9DED8")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)])))
    story += [Spacer(1,5*mm),Paragraph("What we are working on",head)]
    if findings:
        for f in findings:
            story.append(Paragraph(f"<b>{escape(str(f.get('title') or 'Improvement'))}</b><br/>{escape(str(f.get('action') or f.get('evidence') or 'Review this item with Straight Up Digital.'))}",body)); story.append(Spacer(1,2*mm))
    else:
        story.append(Paragraph("No new priority items were recorded in the saved audit for this period.",body))
    story += [Spacer(1,4*mm),Paragraph("Next steps",head),Paragraph("This progress summary is based on the Business Auditor review saved in the CRM. Scores are indicative snapshots, not a guarantee of search position, traffic, leads or revenue. Confirm any implementation scope and timing with St. John Ritchie.",small),Spacer(1,4*mm),Paragraph("St. John Ritchie trading as Straight Up Digital &nbsp;|&nbsp; Port Alfred, Eastern Cape, South Africa",small)]
    doc=SimpleDocTemplate(str(out),pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=16*mm,bottomMargin=16*mm,title=f"Monthly progress report - {contact['business_name']} - {month}",author="St. John Ritchie trading as Straight Up Digital")
    def decorate(canvas,_doc):
        canvas.saveState();canvas.setFillColor(ink);canvas.rect(0,A4[1]-3*mm,A4[0],3*mm,fill=1,stroke=0);canvas.setFillColor(green);canvas.rect(18*mm,12*mm,22*mm,1.2*mm,fill=1,stroke=0);canvas.restoreState()
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate);return out


def bulk_update_stage(contact_ids: list[int], stage: str, db_path=None, tenant_id=None) -> int:
    from crm import DEFAULT_DB
    tenant=tenant_id or env("CRM_TENANT_ID","straight-up-digital")
    allowed={"new","contacted","quoted","accepted","live","lost"}
    ids=list(dict.fromkeys(int(x) for x in contact_ids if str(x).isdigit()))[:250]
    if stage not in allowed or not ids: raise ValueError("Choose a valid stage and at least one lead")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        marks=','.join('?' for _ in ids)
        cur=conn.execute(f"UPDATE contacts SET pipeline_stage=?,updated_at=? WHERE tenant_id=? AND id IN ({marks})",(stage,now_utc(),tenant,*ids))
        conn.commit();return int(cur.rowcount)

def record_opt_out(contact_id:int, reason:str="Client asked not to be contacted", db_path=None, tenant_id=None) -> None:
    from crm import DEFAULT_DB
    tenant=tenant_id or env("CRM_TENANT_ID","straight-up-digital")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        row=_contact(conn,contact_id,tenant)
        for kind,value in (("email",row["email"]),("phone",row["phone"]),("place_id",row["place_id"])):
            if value: add_suppression(conn,kind,str(value),str(reason or "Do not contact")[:500],tenant)
        conn.execute("UPDATE contacts SET outreach_allowed=0,active_outreach=0,marketing_consent=0,updated_at=? WHERE id=? AND tenant_id=?",(now_utc(),int(contact_id),tenant))
        conn.commit()


def set_quote_status(quote_id:int,status:str,db_path=None,tenant_id=None)->None:
    from crm import DEFAULT_DB
    tenant=tenant_id or env("CRM_TENANT_ID","straight-up-digital")
    if status not in {"draft","sent","accepted","declined","expired"}: raise ValueError("Invalid quote status")
    with _open_crm(db_path or DEFAULT_DB) as conn:
        cur=conn.execute("UPDATE crm_quotes SET status=?,sent_at=CASE WHEN ?='sent' AND sent_at='' THEN ? ELSE sent_at END,updated_at=? WHERE id=? AND tenant_id=?",(status,status,now_utc(),now_utc(),int(quote_id),tenant))
        if cur.rowcount!=1: raise LookupError("Quote not found")
        conn.commit()

def _add_months(value:date,months:int)->date:
    index=value.month-1+months; year=value.year+index//12; month=index%12+1
    from calendar import monthrange
    return date(year,month,min(value.day,monthrange(year,month)[1]))
