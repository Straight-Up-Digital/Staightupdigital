"""Central environment and JSON asset loader. Process environment wins over .env."""
from __future__ import annotations
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def load_env(env_path: str|Path|None=None) -> None:
    path=Path(env_path) if env_path else ROOT/'.env'
    if not path.is_file(): return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key,value=line.split('=',1);key=key.strip();value=value.strip()
        if value[:1] in ('"',"'") and value[-1:]==value[:1]: value=value[1:-1]
        if key and key not in os.environ: os.environ[key]=value

def env(name: str, default: str='') -> str:
    load_env()
    if name not in os.environ and name=='SERPAPI_API_KEY':
        key_path=ROOT/'Serp Api Key.txt'
        if key_path.is_file():
            value=key_path.read_text(encoding='utf-8').strip()
            if value:os.environ[name]=value
    return os.environ.get(name,default).strip()

def json_asset(relative_path: str, default=None):
    path=ROOT/relative_path
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):
        if default is not None:return default
        raise

def prompt_config() -> dict:
    return json_asset('assets/prompts/audit_system.json',{})

def pdf_template() -> dict:
    return json_asset('assets/templates/pdf_brand.json',{})
