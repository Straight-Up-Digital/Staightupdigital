"""Single-worker durable audit queue for one-contact CRM audits."""
from __future__ import annotations
import json,queue,re,threading
from pathlib import Path
from config_loader import env
from security_schema import create_audit_job,update_audit_job,check_cost_limit
from crm import connect,get_contact,save_audit_result,save_contact_fields

_QUEUE=queue.Queue()
_LOCK=threading.Lock()
_STARTED=False

_BAD_MAIL=re.compile(r"(\.(png|jpe?g|gif|svg|webp)$|^(noreply|no-reply|donotreply|sentry|wixpress|example)|@(sentry|wixpress|example)\.)",re.I)

def _detected_details(audit:dict,contact:dict)->dict:
    """Pull email, phone and social links the audit found on the client's own website."""
    research=audit.get("website_research") or {}
    signals=research.get("business_signals") or {}
    out={}
    host=re.sub(r"^www\.","",(research.get("domain") or "").lower())
    emails=[e for e in signals.get("emails",[]) if not _BAD_MAIL.search(e)]
    emails.sort(key=lambda e:(0 if host and e.lower().endswith("@"+host) else 1))
    if emails and not (contact.get("email") or "").strip():
        out["email"]=emails[0]
    phones=signals.get("phone_candidates") or []
    google_phone=((audit.get("external_research") or {}).get("google_places") or {}).get("phone")
    if not (contact.get("phone") or "").strip():
        found=google_phone or (phones[0] if phones else "")
        if found:out["phone"]=str(found).strip()
    have=[u for u in (contact.get("social_links") or []) if u]
    seen={u.lower().rstrip("/") for u in have}
    new=[]
    for urls in (research.get("social_profiles") or {}).values():
        for u in urls:
            if u.lower().rstrip("/") not in seen:
                seen.add(u.lower().rstrip("/"));new.append(u)
    if new:out["social_links"]=have+new
    return out

def _backfill_contact(contact_id:int,audit:dict,db_path:Path,tenant_id:str):
    """Fill blank client fields from the audit. Never overwrites anything already saved."""
    try:
        current=get_contact(contact_id,db_path,tenant_id)
        if not current:return
        found=_detected_details(audit,current)
        if not found:return
        data=dict(current);data.update(found)
        save_contact_fields(contact_id,data,db_path,tenant_id)
        print(f"Audit job: saved from the website: {', '.join(found)}")
    except Exception as exc:
        print(f"Audit job: could not save detected details: {type(exc).__name__}: {exc}")

def _run_one(job_id:str,tenant_id:str,contact_id:int,db_path:Path):
    with connect(db_path) as conn:
        update_audit_job(conn,job_id,"processing",tenant_id=tenant_id)
        if not check_cost_limit(conn,float(env("AI_DAILY_COST_LIMIT_USD","1.00")),tenant_id):
            update_audit_job(conn,job_id,"failed","Daily AI cost limit reached",tenant_id)
            return
    contact=get_contact(contact_id,db_path,tenant_id)
    if not contact:
        with connect(db_path) as conn:update_audit_job(conn,job_id,"failed","Contact not found in tenant",tenant_id)
        return
    try:
        from business_auditor import run_audit,monthly_retainer_options,make_pdf
        from config_loader import prompt_config
        from storage import storage_backend
        prompt_bundle=prompt_config()
        town=(contact.get("town") or "").strip()
        location=town if town and town!="other" else (contact.get("address") or "")
        audit=run_audit(contact["website_url"],contact["business_name"],location,
                        contact.get("sector") or "",False,None,"",True,"",progress=lambda m:print(f"Audit job {job_id}: {m}"))
        audit["prompt_config_version"]=prompt_bundle.get("version")
        audit["prompt_policy_name"]=prompt_bundle.get("name")
        audit["package_suggestion"]["monthly_retainer_options"]=monthly_retainer_options(audit)
        pdf=Path(make_pdf(audit))
        store_dir=Path(env("CRM_LOCAL_STORAGE_DIR","reports/objects"))
        if not store_dir.is_absolute():store_dir=Path(__file__).resolve().parent/store_dir
        backend=storage_backend(str(store_dir))
        stored=backend.put(f"audits/{tenant_id}/{pdf.name}",pdf.read_bytes())
        audit["pdf_report_path"]=stored
        save_audit_result(contact_id,audit,db_path,tenant_id)
        _backfill_contact(contact_id,audit,db_path,tenant_id)
        with connect(db_path) as conn:update_audit_job(conn,job_id,"completed",tenant_id=tenant_id)
    except Exception as exc:
        message=f"{type(exc).__name__}: {exc}"
        with connect(db_path) as conn:update_audit_job(conn,job_id,"failed",message,tenant_id)

def _worker():
    while True:
        task=_QUEUE.get()
        try:
            if task is None:return
            _run_one(*task)
        except Exception as exc:
            # Never let one bad job kill the worker or leave the job stuck on "processing".
            print(f"Audit worker error: {type(exc).__name__}: {exc}")
            try:
                job_id,tenant_id,_cid,db_path=task
                with connect(db_path) as conn:update_audit_job(conn,job_id,"failed",f"{type(exc).__name__}: {exc}",tenant_id)
            except Exception as inner:
                print(f"Could not mark job failed: {type(inner).__name__}: {inner}")
        finally:_QUEUE.task_done()

def start_worker():
    global _STARTED
    with _LOCK:
        if not _STARTED:
            t=threading.Thread(target=_worker,name="crm-audit-worker",daemon=True);t.start();_STARTED=True

def enqueue_audit(contact_id:int,tenant_id:str,db_path:Path)->str:
    start_worker()
    with connect(db_path) as conn:
        contact=get_contact(contact_id,db_path,tenant_id)
        if not contact:raise LookupError("Contact not found in active tenant")
        if not contact.get("details_verified"):raise PermissionError("Verify business details before auditing")
        if contact.get("website_detail")!="own_website_listed" or not contact.get("website_url"):
            raise ValueError("An official business-owned website is required for audit")
        running=conn.execute("select count(*) from audit_jobs where tenant_id=? and contact_id=? and status in ('queued','processing')",(tenant_id,int(contact_id))).fetchone()[0]
        if running:raise RuntimeError("An audit for this business is already queued or processing")
        pending=conn.execute("select count(*) from audit_jobs where tenant_id=? and status in ('queued','processing')",(tenant_id,)).fetchone()[0]
        if pending>=50:raise RuntimeError("The tenant audit backlog is full (50 jobs); wait for jobs to finish")
        job_id=create_audit_job(conn,contact_id,tenant_id)
    _QUEUE.put((job_id,tenant_id,int(contact_id),Path(db_path)))
    return job_id

def job_status(job_id:str,tenant_id:str,db_path:Path)->dict|None:
    with connect(db_path) as conn:
        row=conn.execute("select job_id,status,error_log,created_at,updated_at from audit_jobs where job_id=? and tenant_id=?",(job_id,tenant_id)).fetchone()
    return dict(row) if row else None

def recover_queued(tenant_id:str,db_path:Path):
    """Requeue durable queued jobs on startup; never replay processing jobs blindly."""
    with connect(db_path) as conn:
        stale=conn.execute("select job_id from audit_jobs where tenant_id=? and status='processing'",(tenant_id,)).fetchall()
        for s in stale:update_audit_job(conn,s["job_id"],"failed","Interrupted by a restart. Run the audit again.",tenant_id)
        rows=conn.execute("select job_id,contact_id from audit_jobs where tenant_id=? and status='queued' order by created_at",(tenant_id,)).fetchall()
    start_worker()
    for row in rows:_QUEUE.put((row["job_id"],tenant_id,int(row["contact_id"]),Path(db_path)))
    return len(rows)
