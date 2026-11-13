"""Persistent per-trade sweep task queue; runner calls the existing sweep engine only after confirmation."""
from __future__ import annotations
import json,sqlite3,uuid
from datetime import datetime,timezone
from pathlib import Path
from security import active_tenant_id

def enqueue_trades(conn,tenant_id:str,town:str,trades:list[str],plan:dict)->list[str]:
 now=datetime.now(timezone.utc).isoformat(timespec='seconds');ids=[]
 for trade in trades:
  jid=uuid.uuid4().hex;payload=json.dumps({'town':town,'trade':trade,'plan':plan},ensure_ascii=False)
  conn.execute("insert into sweep_jobs(tenant_id,job_id,town,trade,status,payload_json,created_at,updated_at) values(?,?,?,?,'queued',?,?,?)",(tenant_id,jid,town,trade,payload,now,now));ids.append(jid)
 conn.commit();return ids

def list_jobs(conn,tenant_id:str|None=None,limit:int=100):
 tenant=tenant_id or active_tenant_id()
 return conn.execute('select * from sweep_jobs where tenant_id=? order by created_at desc limit?',(tenant,max(1,min(int(limit),1000)))).fetchall()

def update_job(conn,job_id:str,status:str,error_log:str='',result_path:str='',tenant_id:str|None=None):
 if status not in {'queued','processing','completed','failed'}:raise ValueError('invalid sweep job status')
 tenant=tenant_id or active_tenant_id();now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 cur=conn.execute('update sweep_jobs set status=?,error_log=?,result_path=?,updated_at=? where tenant_id=? and job_id=?',(status,error_log,result_path,now,tenant,job_id))
 if cur.rowcount!=1:raise LookupError('sweep job not found')
 conn.commit()
