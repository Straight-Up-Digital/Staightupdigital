"""Compliance gate for importing an already-produced town sweep; performs no network searches."""
from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from config_loader import env,load_env
from security_schema import eligible_for_crm,migrate

ROOT=Path(__file__).resolve().parent
DB=Path(env('CRM_DATABASE_PATH','reports/straight_up_digital.sqlite3'))
if not DB.is_absolute():DB=ROOT/DB

def filter_payload(payload:dict,db_path:Path=DB,tenant_id:str|None=None)->tuple[dict,int]:
 tenant=tenant_id or env('CRM_TENANT_ID')
 if not tenant:raise RuntimeError('CRM_TENANT_ID required')
 migrate(db_path,default_tenant=tenant)
 conn=sqlite3.connect(db_path)
 try:
  kept=[];skipped=0
  for lead in payload.get('leads',[]):
   if eligible_for_crm(conn,lead,tenant):kept.append(lead)
   else:skipped+=1
  result=dict(payload);result['leads']=kept
  result['suppression_skipped']=skipped
  result['suppression_note']='Matched leads were omitted before CRM import.'
  return result,skipped
 finally:conn.close()

def import_existing_sweep(path:str|Path,db_path:Path=DB,tenant_id:str|None=None)->dict:
 payload=json.loads(Path(path).read_text(encoding='utf-8'));filtered,skipped=filter_payload(payload,db_path,tenant_id)
 # Reuse existing importer; never invokes SerpApi or town_sweep.run_sweep.
 result=import_sweep_payload(filtered,db_path,tenant_id)
 result['suppression_skipped']=skipped
 return result

def import_sweep_payload(payload:dict,db_path:Path,tenant_id:str|None=None):
 from crm import import_sweep_payload as crm_import_payload
 return crm_import_payload(payload,db_path,tenant_id)

def main():
 load_env();ap=argparse.ArgumentParser(description='Import an existing sweep with suppression checks; no searches run')
 ap.add_argument('sweep_json');ap.add_argument('--tenant-id',default='');ap.add_argument('--db',default=str(DB))
 a=ap.parse_args();print(json.dumps(import_existing_sweep(a.sweep_json,Path(a.db),a.tenant_id or None),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
