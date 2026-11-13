"""Additive tenant, suppression, and retention helpers. Existing tables are never dropped."""
from __future__ import annotations
import sqlite3
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Iterable
from security import active_tenant_id,suppression_hash
CONTACT_COLUMNS={"tenant_id":"TEXT NOT NULL DEFAULT 'straight-up-digital'","marketing_consent":"INTEGER NOT NULL DEFAULT 0","consent_recorded_at":"TEXT NOT NULL DEFAULT ''","sanitized_at":"TEXT NOT NULL DEFAULT ''","email_enc":"TEXT NOT NULL DEFAULT ''","phone_enc":"TEXT NOT NULL DEFAULT ''","lifecycle_status":"TEXT NOT NULL DEFAULT 'Scraped'","extracted_at":"TEXT NOT NULL DEFAULT ''","last_contacted_at":"TEXT NOT NULL DEFAULT ''","active_outreach":"INTEGER NOT NULL DEFAULT 0"}
AUDIT_COLUMNS={"tenant_id":"TEXT NOT NULL DEFAULT 'straight-up-digital'"}

def migrate(db_path:str|Path,default_tenant:str='straight-up-digital')->dict:
 conn=sqlite3.connect(db_path)
 try:
  tables={r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
  if 'contacts' not in tables:raise RuntimeError('contacts table is missing')
  if 'audit_history' not in tables:
   conn.execute("""CREATE TABLE audit_history(id INTEGER PRIMARY KEY AUTOINCREMENT,contact_id INTEGER NOT NULL,audit_date TEXT NOT NULL,score INTEGER,coverage INTEGER,confidence TEXT NOT NULL DEFAULT '',google_status TEXT NOT NULL DEFAULT '',google_rating REAL,google_review_count INTEGER,quote_amount REAL,pdf_path TEXT NOT NULL DEFAULT '',json_path TEXT NOT NULL DEFAULT '',summary_json TEXT NOT NULL DEFAULT '{}',full_audit_json TEXT NOT NULL DEFAULT '{}',scope_items_json TEXT NOT NULL DEFAULT '[]',tenant_id TEXT NOT NULL DEFAULT 'straight-up-digital',FOREIGN KEY(contact_id) REFERENCES contacts(id))""")
  added={"contacts":[],"audit_history":[]}
  for table,schema in (("contacts",CONTACT_COLUMNS),("audit_history",AUDIT_COLUMNS)):
   have={r[1] for r in conn.execute(f'pragma table_info({table})')}
   for name,decl in schema.items():
    if name not in have:
     decl=decl.replace("'straight-up-digital'","'"+default_tenant.replace("'","''")+"'")
     conn.execute(f'alter table {table} add column {name} {decl}');added[table].append(name)
  contact_cols={r[1] for r in conn.execute('pragma table_info(contacts)')}
  for col in ('place_id','phone','email'):
   if col in contact_cols: conn.execute(f'create index if not exists idx_contacts_tenant_{col} on contacts(tenant_id,{col})')
  conn.execute("""create table if not exists suppression_list(id integer primary key autoincrement,tenant_id text not null,identity_hash text not null,identity_type text not null,reason text not null default '',created_at text not null,unique(tenant_id,identity_hash))""")
  conn.execute('create index if not exists idx_suppression_tenant_hash on suppression_list(tenant_id,identity_hash)')
  conn.execute("create table if not exists crm_settings(tenant_id text not null,setting_key text not null,setting_json text not null default '{}',updated_at text not null,primary key(tenant_id,setting_key))")
  conn.execute('create table if not exists audit_jobs(id integer primary key autoincrement,tenant_id text not null,contact_id integer not null,job_id text not null unique,status text not null check(status in (\'queued\',\'processing\',\'completed\',\'failed\')),error_log text not null default \'\',created_at text not null,updated_at text not null,foreign key(contact_id) references contacts(id))')
  conn.execute('create index if not exists idx_audit_jobs_tenant_status on audit_jobs(tenant_id,status,created_at)')
  conn.execute("create table if not exists sweep_jobs(id integer primary key autoincrement,tenant_id text not null,job_id text not null unique,town text not null,trade text not null,status text not null check(status in ('queued','processing','completed','failed')),payload_json text not null default '{}',error_log text not null default '',result_path text not null default '',created_at text not null,updated_at text not null)")
  conn.execute('create index if not exists idx_sweep_jobs_tenant_status on sweep_jobs(tenant_id,status,created_at)')
  conn.execute("""create table if not exists api_logs(id integer primary key autoincrement,tenant_id text not null,user_id text not null default '',provider text not null,model_used text not null default '',prompt_tokens integer not null default 0,completion_tokens integer not null default 0,estimated_cost_usd real not null default 0,created_at text not null,metadata_json text not null default '{}')""")
  conn.execute('create index if not exists idx_api_logs_tenant_date on api_logs(tenant_id,created_at)')
  conn.commit();return {"tables":sorted(tables|{'suppression_list','audit_jobs','api_logs'}),"columns_added":added,"default_tenant":default_tenant}
 finally:conn.close()

def suppression_match(conn,identities:Iterable[tuple[str,str]],tenant_id:str|None=None):
 tenant=tenant_id or active_tenant_id()
 for kind,value in identities:
  if not str(value or '').strip():continue
  hashed=suppression_hash(kind,str(value))
  row=conn.execute('select identity_type,reason from suppression_list where tenant_id=? and identity_hash=? limit 1',(tenant,hashed)).fetchone()
  if row:return {"identity_type":row[0],"reason":row[1]}
 return None

def add_suppression(conn,kind:str,value:str,reason:str='',tenant_id:str|None=None):
 tenant=tenant_id or active_tenant_id();hashed=suppression_hash(kind,value)
 conn.execute('insert or ignore into suppression_list(tenant_id,identity_hash,identity_type,reason,created_at) values(?,?,?,?,?)',(tenant,hashed,kind,reason,datetime.now(timezone.utc).isoformat(timespec='seconds')))

def eligible_for_crm(conn,lead:dict,tenant_id:str|None=None)->bool:
 keys=[(k,str(lead[k])) for k in ('place_id','phone','email') if lead.get(k)]
 return suppression_match(conn,keys,tenant_id) is None

def add_sweep_rows(conn,tenant_id:str,town:str,trades:list[str],plan:dict)->list[str]:
 import uuid,json
 now=datetime.now(timezone.utc).isoformat(timespec='seconds');ids=[]
 for trade in trades:
  jid=uuid.uuid4().hex
  conn.execute("insert into sweep_jobs(tenant_id,job_id,town,trade,status,payload_json,created_at,updated_at) values(?,?,?,?,'queued',?,?,?)",(tenant_id,jid,town,trade,json.dumps(plan,ensure_ascii=False),now,now));ids.append(jid)
 conn.commit();return ids

def update_sweep_row(conn,tenant_id:str,job_id:str,status:str,error_log:str='',result_path:str='')->None:
 if status not in {'queued','processing','completed','failed'}:raise ValueError('invalid sweep job status')
 now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 cur=conn.execute('update sweep_jobs set status=?,error_log=?,result_path=?,updated_at=? where tenant_id=? and job_id=?',(status,error_log,result_path,now,tenant_id,job_id))
 if cur.rowcount!=1:raise LookupError('sweep job not found for tenant')
 conn.commit()

def sanitize_stale_lifecycle(conn,older_than_days:int=90,tenant_id:str|None=None,dry_run:bool=True)->dict:
 tenant=tenant_id or active_tenant_id();cutoff=(datetime.now(timezone.utc)-timedelta(days=int(older_than_days))).isoformat(timespec='seconds')
 rows=conn.execute("select id from contacts where tenant_id=? and lifecycle_status in ('Scraped','Awaiting_Outreach') and active_outreach=0 and extracted_at<>'' and extracted_at<? and sanitized_at=''",(tenant,cutoff)).fetchall()
 ids=[r[0] for r in rows]
 if ids and not dry_run:
  marks=','.join('?' for _ in ids);now=datetime.now(timezone.utc).isoformat(timespec='seconds')
  conn.execute(f"update contacts set first_name='',surname='',phone='',email='',phone_enc='',email_enc='',social_links_json='[]',notes='[sanitized: stale unconsented lead]',sanitized_at=?,lifecycle_status='Suppressed' where tenant_id=? and id in ({marks})",[now,tenant,*ids]);conn.commit()
 return {"tenant_id":tenant,"cutoff":cutoff,"older_than_days":int(older_than_days),"count":len(ids),"ids":ids,"dry_run":bool(dry_run)}

def sanitize_unconsented_leads(conn,older_than_days:int=90,tenant_id:str|None=None,dry_run:bool=True)->dict:
 tenant=tenant_id or active_tenant_id();cutoff=(datetime.now(timezone.utc)-timedelta(days=int(older_than_days))).isoformat(timespec='seconds')
 ids=[r[0] for r in conn.execute("select id from contacts where tenant_id=? and marketing_consent=0 and created_at<? and sanitized_at=''",(tenant,cutoff))]
 if ids and not dry_run:
  marks=','.join('?' for _ in ids);now=datetime.now(timezone.utc).isoformat(timespec='seconds')
  conn.execute(f"update contacts set first_name='',surname='',phone='',email='',phone_enc='',email_enc='',social_links_json='[]',notes='[sanitized: unconsented lead older than retention window]',sanitized_at=? where tenant_id=? and id in ({marks})",[now,tenant,*ids]);conn.commit()
 return {"tenant_id":tenant,"cutoff":cutoff,"older_than_days":int(older_than_days),"count":len(ids),"ids":ids,"dry_run":bool(dry_run)}

def tenant_query(conn,table:str,tenant_id:str|None=None,columns:str='*',order_by:str='id')->list:
 if table not in {'contacts','audit_history','audit_jobs','api_logs'}:raise ValueError('table not tenant-scoped/allowed')
 tenant=tenant_id or active_tenant_id()
 return conn.execute(f'select {columns} from {table} where tenant_id=? order by {order_by}',(tenant,)).fetchall()

def create_audit_job(conn,contact_id:int,tenant_id:str|None=None)->str:
 import uuid
 tenant=tenant_id or active_tenant_id();job=uuid.uuid4().hex;now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 row=conn.execute('select id from contacts where id=? and tenant_id=?',(int(contact_id),tenant)).fetchone()
 if not row:raise LookupError('Contact not found in active tenant')
 conn.execute("insert into audit_jobs(tenant_id,contact_id,job_id,status,created_at,updated_at) values(?,?,?,'queued',?,?)",(tenant,int(contact_id),job,now,now));conn.commit();return job

def add_api_usage(conn,provider:str,model_used:str,prompt_tokens:int,completion_tokens:int,estimated_cost_usd:float,user_id:str='',tenant_id:str|None=None,metadata:dict|None=None):
 tenant=tenant_id or active_tenant_id();now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 conn.execute('insert into api_logs(tenant_id,user_id,provider,model_used,prompt_tokens,completion_tokens,estimated_cost_usd,created_at,metadata_json) values(?,?,?,?,?,?,?,?,?)',(tenant,user_id,provider,model_used,int(prompt_tokens),int(completion_tokens),float(estimated_cost_usd),now,__import__('json').dumps(metadata or {},ensure_ascii=False)));conn.commit()

def daily_cost(conn,tenant_id:str|None=None,user_id:str='') -> float:
 tenant=tenant_id or active_tenant_id();day=datetime.now(timezone.utc).date().isoformat();sql='select coalesce(sum(estimated_cost_usd),0) from api_logs where tenant_id=? and created_at like ?';args=[tenant,day+'%']
 if user_id:sql+=' and user_id=?';args.append(user_id)
 return float(conn.execute(sql,args).fetchone()[0])

def check_cost_limit(conn,limit_usd:float,tenant_id:str|None=None,user_id:str='')->bool:
 return daily_cost(conn,tenant_id,user_id)<float(limit_usd)

def count_for_tenant(conn,table:str,tenant_id:str|None=None)->int:
 if table not in {'contacts','audit_history','audit_jobs','api_logs'}:raise ValueError('unsupported table')
 tenant=tenant_id or active_tenant_id()
 return int(conn.execute(f'select count(*) from {table} where tenant_id=?',(tenant,)).fetchone()[0])

def start_audit_job(conn,contact_id:int,tenant_id:str|None=None)->str:
 return create_audit_job(conn,contact_id,tenant_id)

def update_audit_job(conn,job_id:str,status:str,error_log:str='',tenant_id:str|None=None)->None:
 if status not in {'queued','processing','completed','failed'}:raise ValueError('invalid audit status')
 tenant=tenant_id or active_tenant_id();now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 cur=conn.execute('update audit_jobs set status=?,error_log=?,updated_at=? where job_id=? and tenant_id=?',(status,error_log,now,job_id,tenant))
 if cur.rowcount!=1:raise LookupError('Audit job not found for active tenant')
 conn.commit()
