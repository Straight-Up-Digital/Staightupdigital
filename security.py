"""Security primitives. Keys are environment-managed; never fall back to plaintext."""
from __future__ import annotations
import base64,contextvars,hashlib,hmac,os,random,re,time
from typing import Callable,TypeVar
from config_loader import env, load_env as load_environment
_ACTIVE_TENANT:contextvars.ContextVar[str|None]=contextvars.ContextVar('crm_tenant_id',default=None)
T=TypeVar('T')

def active_tenant_id()->str:
 v=_ACTIVE_TENANT.get() or env('CRM_TENANT_ID')
 if not v:raise RuntimeError('CRM_TENANT_ID is required')
 return v

class tenant_context:
 def __init__(self,tenant_id:str):
  if not str(tenant_id or '').strip():raise ValueError('tenant_id required')
  self.value=str(tenant_id).strip();self.token=None
 def __enter__(self):self.token=_ACTIVE_TENANT.set(self.value);return self.value
 def __exit__(self,*args):_ACTIVE_TENANT.reset(self.token)

def _aesgcm():
 try:from cryptography.hazmat.primitives.ciphers.aead import AESGCM
 except ImportError as e:raise RuntimeError('Install cryptography in .venv') from e
 key=env('CRM_ENCRYPTION_KEY_B64')
 if not key:raise RuntimeError('CRM_ENCRYPTION_KEY_B64 is required; refusing plaintext fallback')
 try:raw=base64.urlsafe_b64decode(key.encode('ascii'))
 except Exception as e:raise ValueError('CRM_ENCRYPTION_KEY_B64 must be urlsafe base64') from e
 if len(raw)!=32:raise ValueError('CRM_ENCRYPTION_KEY_B64 must decode to exactly 32 bytes for AES-256-GCM')
 return AESGCM(raw)

def encrypt_text(value:str|None)->str|None:
 if value in (None,''):return value
 import secrets
 nonce=secrets.token_bytes(12);cipher=_aesgcm().encrypt(nonce,str(value).encode('utf-8'),None)
 return 'enc:v1:'+base64.urlsafe_b64encode(nonce+cipher).decode('ascii')

def decrypt_text(value:str|None)->str|None:
 if value in (None,''):return value
 if not str(value).startswith('enc:v1:'):raise ValueError('Value is not an AES-GCM v1 ciphertext')
 blob=base64.urlsafe_b64decode(str(value)[7:].encode('ascii'))
 return _aesgcm().decrypt(blob[:12],blob[12:],None).decode('utf-8')

def _suppression_hash(kind:str,normalized:str)->str:
 secret=env('CRM_SUPPRESSION_HMAC_KEY')
 if not secret:raise RuntimeError('CRM_SUPPRESSION_HMAC_KEY is required for suppression matching')
 if not normalized:raise ValueError('empty suppression identity')
 digest=hmac.new(secret.encode(),f'{kind}:{normalized}'.encode(),hashlib.sha256).hexdigest()
 return f'{kind}:{digest}'

def _legacy_suppression_hash(kind:str,value:str)->str:
 normalized=' '.join(str(value or '').casefold().split())
 return _suppression_hash(kind,normalized)

def suppression_hash(kind:str,value:str)->str:
 normalized=' '.join(str(value or '').casefold().split())
 if kind=='phone':
  digits=re.sub(r'\D','',normalized)
  if digits:
   normalized='0'+digits[2:] if digits.startswith('27') else digits
 return _suppression_hash(kind,normalized)

def retry_call(fn:Callable[[],T],attempts:int=4,base_delay:float=.5,max_delay:float=8,sleep:Callable[[float],None]=time.sleep)->T:
 attempts=max(1,int(attempts))
 for i in range(attempts):
  try:
   response=fn();status=getattr(response,'status_code',None)
   if status not in (408,425,429,500,502,503,504) or i==attempts-1:return response
  except Exception:
   if i==attempts-1:raise
  sleep(min(max_delay,base_delay*(2**i))*(.8+random.random()*.4))
 raise RuntimeError('retry attempts exhausted')
