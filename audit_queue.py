"""Single-worker durable audit queue for one-contact CRM audits."""
from __future__ import annotations
import json,queue,threading
from pathlib import Path
from config_loader import env
from security_schema import create_audit_job,update_audit_job,check_cost_limit
from crm import connect,get_contact,save_audit_result

_QUEUE=queue.Queue()
_LOCK=threading.Lock()
_STARTED=False

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
        running=conn.execute("select count(*) from audit_jobs where tenant_id=? and status in ('queued','processing')",(tenant_id,)).fetchone()[0]
        if running:raise RuntimeError("An audit is already queued or processing for this tenant")
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
