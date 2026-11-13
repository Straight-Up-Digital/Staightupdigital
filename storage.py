"""Local-first storage abstraction for PDFs and generated assets."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Protocol
from config_loader import env

class Storage(Protocol):
 def put(self,key:str,data:bytes)->str: ...
 def get(self,key:str)->bytes: ...

class LocalStorage:
 def __init__(self,root:str|Path):self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
 def _path(self,key:str)->Path:
  p=(self.root/key).resolve()
  if not p.is_relative_to(self.root):raise ValueError('storage key escapes configured root')
  return p
 def put(self,key:str,data:bytes)->str:
  p=self._path(key);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return str(p)
 def get(self,key:str)->bytes:return self._path(key).read_bytes()

def storage_backend(root:str|Path|None=None)->Storage:
 backend=env('CRM_STORAGE_BACKEND','local').lower()
 if backend!='local':raise RuntimeError(f'{backend} backend is not implemented. Local storage remains the only active backend.')
 return LocalStorage(root or env('CRM_LOCAL_STORAGE_DIR','reports/objects'))
