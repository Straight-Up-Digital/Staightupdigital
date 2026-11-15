"""Local-only CRM UI and single-contact audit workflow."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from crm import DEFAULT_DB, audit_history, connect, get_contact, list_contacts, save_audit_result, save_contact_fields, update_scope_item
from config_loader import env, load_env
from security import tenant_context
from security_schema import add_suppression, count_for_tenant, migrate

HOST = "127.0.0.1"
PORT = int(os.environ.get("CRM_PORT", "8765"))
PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "auditor_config.json"
FINDER_DIR = PROJECT_DIR / "reports" / "finder"
RANK_DIR = PROJECT_DIR / "reports" / "rankings"
BRAND_DIR = PROJECT_DIR / "assets" / "brand"
BRAND_FONT_DIR = BRAND_DIR / "fonts"
BRAND_FONT_FILES = {name.lower(): BRAND_FONT_DIR / name for name in (
    "Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf",
    "Montserrat-Regular.ttf", "Montserrat-SemiBold.ttf", "Montserrat-Bold.ttf",
)}
LOCKUP = BRAND_DIR / "straight-up-digital-logo-dark.png"
LEAD_STAGES = ["new", "contacted", "quoted", "accepted", "live", "lost"]
LEAD_STATUSES = ["new", "contacted", "qualified", "proposal_sent", "active_client", "not_interested", "do_not_contact"]
WEBSITE_STATUSES = ["website_found_in_search_unverified", "no_website_surfaced_in_search", "unknown"]
WEBSITE_DETAILS = ["own_website_listed", "social_page_only", "directory_page_only", "no_website_listed", "unknown"]
QUOTE_TYPES = ["", "website build", "audit fixes", "retainer", "once-off edit"]
TOWNS = ["Port Alfred", "Kenton-on-Sea", "Bathurst", "Alexandria", "other"]
FINDER_SOURCE = "facebook_directory_search"
AUDIT_LOCK = threading.Lock()
RANK_LOCK = threading.Lock()
FINDER_LOCK = threading.Lock()
FINDER_JOB = {"state": "idle", "town": "", "lines": [], "summary": "", "error": ""}

STAGE_LABEL = {"new": "New", "contacted": "Contacted", "quoted": "Quoted", "accepted": "Accepted", "live": "Live", "lost": "Lost"}
WEB_LABEL = {"own_website_listed": ("Own website", "info"), "social_page_only": ("Social page only", "warn"),
             "directory_page_only": ("Directory only", "warn"), "no_website_listed": ("No website", "accent"),
             "unknown": ("Not checked", "")}


def h(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


PLATFORMS = [
    ("facebook", "Facebook", ("facebook.com", "fb.com", "fb.me")),
    ("instagram", "Instagram", ("instagram.com",)),
    ("linkedin", "LinkedIn", ("linkedin.com",)),
    ("tiktok", "TikTok", ("tiktok.com",)),
    ("youtube", "YouTube", ("youtube.com", "youtu.be")),
    ("x", "X (Twitter)", ("x.com", "twitter.com")),
    ("whatsapp", "WhatsApp", ("wa.me", "whatsapp.com")),
]


def safe_url(value) -> str:
    """Return a clean http(s) URL, or '' if it is not one. Blocks javascript:, mailto: and similar."""
    v = str(value or "").strip()
    if not v or re.search(r"\s", v):
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v) and not re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(:\d+)?(/|$)", v):
            return ""
        v = "https://" + v
    try:
        u = urlparse(v)
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not u.netloc or "." not in u.netloc:
        return ""
    return v


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for key, _label, domains in PLATFORMS:
        if any(host == d or host.endswith("." + d) for d in domains):
            return key
    return ""


def split_socials(urls) -> tuple[dict, list]:
    """First link per platform fills its own field. Extras and unknown sites go to 'other' so nothing is lost."""
    by_platform, other = {}, []
    for raw in urls or []:
        u = safe_url(raw)
        if not u:
            if str(raw).strip():
                other.append(str(raw).strip())
            continue
        key = platform_of(u)
        if key and key not in by_platform:
            by_platform[key] = u
        else:
            other.append(u)
    return by_platform, other


def money(value) -> str:
    try:
        return f"R{float(value):,.0f}"
    except (TypeError, ValueError):
        return "R0"


def config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def db_path() -> Path:
    configured = config().get("crm", {}).get("database_path", str(DEFAULT_DB))
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_DIR / path


def tenant_id() -> str:
    return env("CRM_TENANT_ID", "straight-up-digital")


def pricing() -> dict:
    cfg = config()
    pp = cfg.get("proposal_pricing", {})
    offer = cfg.get("website_offer", {})
    return {
        "build": pp.get("website_build_once_off_zar", offer.get("build_once_off_zar", 1500)),
        "pages": pp.get("website_build_max_pages", 5),
        "hosting": pp.get("website_hosting_monthly_zar", offer.get("hosting_monthly_zar", 99)),
        "point": pp.get("point_rate_zar", pp.get("audit_missing_point_zar", 40)),
        "edits": pp.get("once_off_edits", {}),
        "retainers": cfg.get("monthly_retainers", []),
    }


# ---------------------------------------------------------------- look and feel

CSS = """
@font-face{font-family:Inter;src:url('/brand-font/inter-regular.ttf') format('truetype');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:Inter;src:url('/brand-font/inter-semibold.ttf') format('truetype');font-weight:600;font-style:normal;font-display:swap}
@font-face{font-family:Inter;src:url('/brand-font/inter-bold.ttf') format('truetype');font-weight:700;font-style:normal;font-display:swap}
@font-face{font-family:Montserrat;src:url('/brand-font/montserrat-regular.ttf') format('truetype');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:Montserrat;src:url('/brand-font/montserrat-semibold.ttf') format('truetype');font-weight:600;font-style:normal;font-display:swap}
@font-face{font-family:Montserrat;src:url('/brand-font/montserrat-bold.ttf') format('truetype');font-weight:700;font-style:normal;font-display:swap}
:root{
  color-scheme:dark;
  --ink:#E8EDE8;--ink-2:#C3CBC4;--muted:#8A968E;--bg:#060807;--panel:#0D1310;--panel-2:#121A15;--line:#1E2923;--line-2:#17201B;
  --signal:#A8FF65;--signal-d:#79B842;--signal-soft:rgba(168,255,101,.12);
  --ok:#A8FF65;--ok-soft:rgba(121,184,66,.16);--warn:#F2C14E;--warn-soft:rgba(224,168,0,.15);--bad:#FF8A7A;--bad-soft:rgba(255,107,91,.14);--info:#8FB4FF;--info-soft:rgba(99,150,255,.15);
  --rail:#040605;--field:#09100C;--field-line:#26332B;
  --display:"Montserrat","Segoe UI",system-ui,sans-serif;
  --body:"Inter","Segoe UI",system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:radial-gradient(900px 480px at 85% -8%,rgba(168,255,101,.07),transparent 62%),radial-gradient(700px 420px at -5% 40%,rgba(80,140,255,.05),transparent 60%),var(--bg);background-attachment:fixed;color:var(--ink);font:14px/1.5 var(--body);font-variant-numeric:tabular-nums}
a{color:var(--ink);text-decoration-color:#3b4a40;text-underline-offset:2px}
a:hover{text-decoration-color:var(--signal)}
h1,h2,h3{font-family:var(--display);margin:0;font-weight:600;letter-spacing:-.01em;color:#F4F7F4}
h1{font-size:28px;line-height:1.15}
h2{font-size:16px}
h3{font-size:14px}
.app{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}
.rail{background:linear-gradient(180deg,#070A08,var(--rail));border-right:1px solid var(--line-2);color:#c9cec8;position:sticky;top:0;height:100vh;padding:22px 14px 16px;display:flex;flex-direction:column;gap:26px}
.logo{width:100%;max-width:200px;height:auto;display:block;padding:0 4px}
.wordmark{font-family:var(--display);font-weight:700;font-size:19px;color:#fff;padding:0 8px;letter-spacing:-.01em}
.nav{display:flex;flex-direction:column;gap:3px}
.nav a{display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:9px;color:#9fa9a2;text-decoration:none;font-weight:600}
.nav a:hover{background:#101712;color:#fff}
.nav a.on{background:linear-gradient(90deg,rgba(168,255,101,.16),rgba(168,255,101,.04));color:#F2F4EF;box-shadow:inset 3px 0 0 var(--signal)}
.nav a.on .ic{color:var(--signal)}
.ic{width:18px;height:18px;flex:none}
.rail-foot{margin-top:auto;font-size:11.5px;color:#6d7971;padding:12px;line-height:1.55;border:1px solid var(--line-2);border-radius:12px;background:rgba(255,255,255,.015)}
.rail-foot b{color:var(--signal);font-weight:700;letter-spacing:.08em}
.main{padding:28px 34px 70px;max-width:1600px;width:100%;min-width:0}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:22px;flex-wrap:wrap}
.head p{margin:5px 0 0;color:var(--muted);max-width:66ch}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.flash{padding:11px 14px;border-radius:10px;margin-bottom:18px;font-weight:600;background:var(--ok-soft);color:var(--ok);border:1px solid rgba(121,184,66,.35)}
.flash.warn{background:var(--warn-soft);color:var(--warn);border-color:rgba(224,168,0,.4)}
.panel{background:linear-gradient(180deg,var(--panel-2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:18px;box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 12px 30px rgba(0,0,0,.28)}
.panel>h2,.panel-head h2{margin-bottom:12px}
.panel-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:12px}
.panel-head h2{margin:0}
.panel-head span{color:var(--muted);font-size:13px}
.note{color:var(--muted);font-size:13px;margin:0 0 12px;max-width:72ch}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:9px 15px;border-radius:9px;border:1px solid var(--field-line);background:#111915;color:var(--ink);font:inherit;font-weight:650;cursor:pointer;text-decoration:none;line-height:1.2;transition:background .15s,border-color .15s,transform .05s}
.btn:hover{background:#18221C;border-color:#3A4A40}
.btn:active{transform:translateY(1px)}
.btn.primary{background:var(--signal);border-color:var(--signal);color:#07100A;box-shadow:0 0 0 0 rgba(168,255,101,.0),0 6px 18px rgba(168,255,101,.18)}
.btn.primary:hover{background:#BDFF8A;border-color:#BDFF8A}
.btn.dark{background:#1B2620;border-color:#34443A;color:#fff}
.btn.dark:hover{background:#243229}
.btn.sm{padding:5px 10px;font-size:13px}
.btn.block{width:100%}
.btn:disabled{opacity:.4;cursor:not-allowed}
:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
input,select,textarea{font:inherit;padding:9px 11px;border:1px solid var(--field-line);border-radius:9px;background:var(--field);color:var(--ink);max-width:100%}
input::placeholder,textarea::placeholder{color:#5f6c64}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--signal-d);box-shadow:0 0 0 3px rgba(168,255,101,.14)}
input[readonly]{background:#0C120F;color:var(--muted)}
input[type=checkbox]{accent-color:var(--signal)}
option{background:#0D1310;color:var(--ink)}
textarea{width:100%;min-height:84px;resize:vertical}
.pill{display:inline-block;padding:1px 10px;border-radius:999px;font-size:12px;font-weight:650;background:rgba(255,255,255,.07);color:#C3CBC4;white-space:nowrap;line-height:1.7}
.pill.ok{background:var(--ok-soft);color:var(--ok)}
.pill.warn{background:var(--warn-soft);color:var(--warn)}
.pill.bad{background:var(--bad-soft);color:var(--bad)}
.pill.info{background:var(--info-soft);color:var(--info)}
.pill.accent{background:var(--signal-soft);color:var(--signal)}
.pill.dark{background:#F2F4EF;color:#080A09}
.muted{color:var(--muted);overflow-wrap:anywhere}
.small{font-size:12.5px}
.avatar{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;font:700 13px var(--display);flex:none;background:#18211B;color:#B4BEB6;border:1px solid var(--line)}
.avatar.t1{background:rgba(99,150,255,.14);color:#8FB4FF;border-color:rgba(99,150,255,.25)}.avatar.t2{background:rgba(121,184,66,.14);color:#A8FF65;border-color:rgba(121,184,66,.28)}.avatar.t3{background:rgba(224,168,0,.14);color:#F2C14E;border-color:rgba(224,168,0,.28)}.avatar.t4{background:rgba(255,107,91,.13);color:#FF8A7A;border-color:rgba(255,107,91,.26)}
.avatar.big{width:58px;height:58px;border-radius:14px;font-size:19px}

/* score dial and KPI tiles */
.dial{--p:0;--c:var(--signal-d);--s:84px;position:relative;width:var(--s);height:var(--s);border-radius:50%;flex:none;display:grid;place-items:center;background:conic-gradient(var(--c) calc(var(--p)*1%),#1B2520 0)}
.dial::before{content:"";position:absolute;inset:calc(var(--s)*.11);border-radius:50%;background:var(--panel)}
.dial b{position:relative;display:block;font:700 calc(var(--s)*.31)/1 var(--display);color:#fff;text-align:center}
.dial b small{display:block;margin-top:2px;font:500 calc(var(--s)*.12)/1 var(--body);color:var(--muted)}
.dial.sm{--s:46px}.dial.sm b{font-size:15px}.dial.sm b small{display:none}
.dial.good{--c:#A8FF65}.dial.mid{--c:#F2C14E}.dial.low{--c:#FF7A6A}.dial.none{--c:#3A463E}
.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:18px}
.kpi{position:relative;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 18px;border-radius:14px;border:1px solid var(--line);background:linear-gradient(180deg,var(--panel-2),var(--panel));box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 10px 26px rgba(0,0,0,.25);text-decoration:none;color:var(--ink);min-width:0}
.kpi:hover{border-color:#34443A}
.kpi::after{content:"";position:absolute;left:18px;right:18px;top:0;height:2px;border-radius:0 0 2px 2px;background:var(--signal);opacity:.75}
.kpi.blue::after{background:#6d98d6}.kpi.amber::after{background:#e8a91d}.kpi.mint::after{background:#13a07a}
.kpi .lab{font-size:11.5px;font-weight:650;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.kpi .num{font:700 32px/1.1 var(--display);color:#fff;margin-top:6px;letter-spacing:-.02em}
.kpi .sub{font-size:12.5px;color:var(--muted);margin-top:3px}
.kpi .dial{--s:64px}

.hero{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(300px,1fr);gap:18px;margin-bottom:18px;align-items:start}
.hero .panel{margin:0}
.today-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 14px;align-items:center;padding:11px 0;border-top:1px solid var(--line-2)}
.today-row:first-of-type{border-top:0}
.today-row b a{text-decoration:none}
.empty{padding:14px 0;color:var(--muted)}
.mrr{background:radial-gradient(420px 200px at 100% 0%,rgba(168,255,101,.18),transparent 60%),linear-gradient(180deg,#101A13,#0A100C);border-color:#2A3A2F}
.mrr .big{font:700 44px/1 var(--display);letter-spacing:-.02em;color:var(--signal)}
.mrr .big small{font-size:16px;font-weight:500;color:var(--muted);margin-left:6px}
.mrr p{color:var(--muted);margin:8px 0 0}
.finder-links{display:flex;flex-direction:column;gap:10px;margin-top:6px}
.finder-links form{display:flex;gap:8px;flex-wrap:wrap}
.finder-links input{flex:1;min-width:120px}

.funnel{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--line-2);margin:2px 0 14px}
.funnel .seg{display:block;height:100%}
.seg-new{background:#66726B}.seg-contacted{background:#6d98d6}.seg-quoted{background:#e8a91d}.seg-accepted{background:var(--signal)}.seg-live{background:#13a07a}.seg-lost{background:#c0605a}
.stages{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
.stage{display:block;text-decoration:none;padding:10px 12px;border-radius:10px;border:1px solid var(--line-2);background:rgba(255,255,255,.015)}
.stage:hover{background:rgba(255,255,255,.04);border-color:#34443A}
.stage b{display:block;font:700 26px/1.1 var(--display);letter-spacing:-.01em;color:#fff}
.stage span{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px}
.stage span i{width:8px;height:8px;border-radius:50%;display:inline-block}

.chips{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.chip{display:inline-flex;gap:7px;align-items:center;padding:5px 12px;border:1px solid var(--line);background:#0E1511;border-radius:999px;text-decoration:none;font-weight:600;font-size:13px;color:var(--ink-2)}
.chip b{font-weight:700;color:#fff}
.chip:hover{border-color:#3A4A40}
.chip.on{background:var(--signal);border-color:var(--signal);color:#07100A}
.chip.on b{color:#07100A}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.filters input[type=search]{flex:1;min-width:220px}

.tbl-wrap{overflow:auto;background:linear-gradient(180deg,var(--panel-2),var(--panel));border:1px solid var(--line);border-radius:14px;max-height:72vh;box-shadow:0 12px 30px rgba(0,0,0,.28)}
table{border-collapse:collapse;width:100%;min-width:980px}
th{position:sticky;top:0;z-index:1;background:#0A100C;text-align:left;font-size:11.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:12px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px 14px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tbody tr:hover{background:rgba(168,255,101,.035)}
tbody tr:last-child td{border-bottom:0}
.biz{display:flex;gap:11px;align-items:center;min-width:220px}
.biz a{font-weight:650;text-decoration:none;color:#fff}
.biz a:hover{text-decoration:underline;text-decoration-color:var(--signal)}
.due{color:var(--bad);font-weight:650}
.stars{color:#F2C14E}

.profile{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:20px;align-items:start}
.profile>*{min-width:0}
.aside{position:sticky;top:18px}
.phead{display:flex;gap:16px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
.phead .who{flex:1;min-width:240px}
.phead .tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.phead .score{display:flex;align-items:center;gap:14px;padding:12px 16px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,var(--panel-2),var(--panel))}
.phead .score .dial{--s:78px}
.phead .score .lab{font-size:11.5px;font-weight:650;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.phead .score .q{font:700 20px/1.2 var(--display);color:var(--signal);margin-top:3px}
.crumb{color:var(--muted);margin-bottom:12px;font-size:13px}
.crumb a{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:13px 14px}
.field label.lab{display:block;font-weight:600;margin-bottom:4px;font-size:13px;color:var(--ink-2)}
.field input,.field select,.field textarea{width:100%}
.wide{grid-column:1/-1}
.panel input[type=checkbox]{width:auto;padding:0;flex:none}
.nw{white-space:nowrap}
.row-actions{display:flex;gap:6px;flex-wrap:nowrap}
.checks{display:flex;flex-direction:column;gap:8px;grid-column:1/-1;padding:12px 14px;background:rgba(255,255,255,.025);border-radius:10px;border:1px solid var(--line-2)}
.checks label{display:flex;gap:9px;align-items:flex-start;font-weight:500}
.checks input{margin-top:3px}
.savebar{position:sticky;bottom:0;background:rgba(6,8,7,.9);backdrop-filter:blur(8px);padding:12px 0;margin-top:4px;display:flex;justify-content:flex-end;gap:10px;border-top:1px solid var(--line)}
.stack{display:flex;flex-direction:column;gap:18px}
.stack>.panel{margin:0}
.rule{border:0;border-top:1px solid var(--line-2);margin:14px 0}
.rank-form{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
.rank-form>*{min-width:0;width:100%}
.rank-table{width:100%;min-width:0;font-size:13px}
.rank-table th{position:static;padding:6px 8px;background:transparent;font-size:11px}
.rank-table td{padding:7px 8px}
.banner{padding:11px 14px;border-radius:10px;margin-bottom:16px;font-weight:600;background:var(--info-soft);color:var(--info);border:1px solid rgba(99,150,255,.32)}
.banner.bad{background:var(--bad-soft);color:var(--bad);border-color:rgba(255,107,91,.35)}
.banner.ok{background:var(--ok-soft);color:var(--ok);border-color:rgba(121,184,66,.35)}
.history{border-top:1px solid var(--line-2);padding:14px 0}
.history:first-of-type{border-top:0}
.history summary{cursor:pointer;font-weight:650;margin-top:10px}
.history .scope{padding:9px 10px;border-top:1px solid var(--line-2)}
.history .scope p{margin:3px 0 3px 26px;color:var(--ink-2)}
details>summary{color:var(--ink)}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#08100B;border:1px solid var(--line-2);color:var(--ink-2);padding:12px;border-radius:9px;font-size:12px}
.src-links a{display:block;overflow-wrap:anywhere;margin:5px 0}
.job{display:flex;gap:10px;align-items:flex-start}
.dot{width:10px;height:10px;border-radius:50%;background:var(--signal);margin-top:6px;flex:none;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.log{font:12px/1.5 Consolas,ui-monospace,monospace;color:var(--ink-2);margin:8px 0 0;background:#08100B;border:1px solid var(--line-2);border-radius:9px;padding:10px 12px;max-height:150px;overflow:auto}
.srcs{display:flex;gap:5px;flex-wrap:wrap}

@media (max-width:1300px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:1100px){.profile{grid-template-columns:minmax(0,1fr)}.aside{position:static}.hero{grid-template-columns:1fr}}
@media (max-width:860px){
  .app{grid-template-columns:minmax(0,1fr)}
  .rail{position:static;height:auto;flex-direction:row;align-items:center;flex-wrap:wrap;padding:12px 14px;gap:12px;border-right:0;border-bottom:1px solid var(--line-2)}
  .logo{max-width:130px}.nav{flex-direction:row;flex-wrap:nowrap;overflow-x:auto;max-width:100%}.nav a{white-space:nowrap}.rail-foot{display:none}
  .main{padding:20px 16px 60px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .stages{grid-template-columns:repeat(3,minmax(0,1fr))}
}
"""

JS = """
document.addEventListener('change',function(e){
  var t=e.target;
  if(t&&t.matches&&t.matches('[data-all]')){
    var f=t.closest('form');
    f.querySelectorAll('input[name="pick"]').forEach(function(c){c.checked=t.checked;});
  }
  if(t&&t.matches&&t.matches('[data-csv-file]')){
    var file=t.files&&t.files[0];
    if(!file)return;
    if(file.size>250000){window.alert('This CRM accepts CSV files up to 250 KB.');t.value='';return;}
    var reader=new FileReader();
    reader.onload=function(){var box=document.getElementById('csv-text');if(box)box.value=String(reader.result||'');};
    reader.onerror=function(){window.alert('The selected CSV could not be read.');};
    reader.readAsText(file);
  }
});
"""

ICONS = {
    "leads": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
    "notlisted": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/>',
    "audited": '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-4"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    "privacy": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    "bulk": '<path d="M4 6h16M4 12h16M4 18h16"/><path d="m7 4-3 2 3 2M17 10l3 2-3 2M7 16l-3 2 3 2"/>',
    "finance": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
    "queue": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "projects": '<path d="M3 7h7l2 2h9v10H3z"/><path d="M3 7V5h7l2 2"/>',
    "insights": '<path d="M3 3v18h18"/><path d="m7 14 4-4 4 3 5-7"/>',
}


def icon(name: str) -> str:
    return ('<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def layout(title: str, body: str, active: str = "leads", message: str = "", refresh: int = 0) -> bytes:
    cfg = config()
    if LOCKUP.is_file():
        brand = '<img class="logo" src="/brand-logo" alt="Straight Up Digital">'
    else:
        brand = f'<div class="wordmark">{h(cfg.get("agency_name", "Straight Up Digital"))}</div>'
    nav_items = [("leads", "/", "Leads"), ("audited", "/audited", "Audited"), ("queue", "/queue", "Audit queue"), ("finance", "/finance", "Sales & billing"), ("projects", "/projects", "Projects"), ("insights", "/insights", "Insights"), ("bulk", "/bulk", "Bulk actions"), ("privacy", "/privacy", "Privacy"), ("shield", "/suppression", "Do not contact"), ("notlisted", "/not-listed", "Not listed")]
    nav = "".join(f'<a href="{href}" class="{"on" if key == active or (key == "shield" and active == "suppression") else ""}">{icon(key)}{label}</a>'
                  for key, href, label in nav_items)
    flash = ""
    if message:
        warn = any(w in message.lower() for w in ("fail", "error", "not ", "required", "verify", "refused", "missing", "only ", "already", "expired"))
        flash = f'<div class="flash{" warn" if warn else ""}" role="status">{h(message)}</div>'
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<link rel="icon" type="image/png" href="/brand-favicon"><title>{h(title)} | Straight Up Digital Business Auditor CRM</title>{meta}<style>{CSS}</style></head><body><div class="app">'
           f'<aside class="rail">{brand}<nav class="nav">{nav}</nav>'
           f'<div class="rail-foot"><b>BUSINESS AUDITOR</b><br>INTELLIGENCE. VISIBILITY. GROWTH.<br>{HOST}:{PORT}<br>{h(tenant_id())}</div></aside>'
           f'<main class="main">{flash}{body}</main></div><script>{JS}</script></body></html>')
    return doc.encode("utf-8")


def avatar(name: str, big: bool = False) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    initials = "".join(w[0] for w in words[:2]).upper() or "?"
    tint = int(hashlib.md5((name or "").encode("utf-8")).hexdigest(), 16) % 5
    return f'<span class="avatar t{tint}{" big" if big else ""}" aria-hidden="true">{h(initials)}</span>'


def dial(score, small: str = "/100", tone: str = "", sm: bool = False) -> str:
    """Round score gauge drawn in CSS. No score shows a grey ring."""
    try:
        value = max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        return f'<span class="dial none{" sm" if sm else ""}" style="--p:0" aria-label="No score"><b>n/a</b></span>'
    tone = tone or ("good" if value >= 75 else "mid" if value >= 50 else "low")
    return f'<span class="dial {tone}{" sm" if sm else ""}" style="--p:{value}" role="img" aria-label="Score {value} out of 100"><b>{value}<small>{h(small)}</small></b></span>'


def stage_pill(stage: str) -> str:
    cls = {"new": "", "contacted": "info", "quoted": "warn", "accepted": "accent", "live": "ok", "lost": "bad"}.get(stage, "")
    return f'<span class="pill {cls}">{h(STAGE_LABEL.get(stage, str(stage or "new").title()))}</span>'


def web_pill(detail: str) -> str:
    label, cls = WEB_LABEL.get(detail or "unknown", WEB_LABEL["unknown"])
    return f'<span class="pill {cls}">{h(label)}</span>'


def _select(name: str, values: list[str], selected: str, labels=None, attrs="") -> str:
    labels = labels or {}
    options = [f'<option value="{h(v)}"{" selected" if v == selected else ""}>{h(labels.get(v, v.replace("_", " ") or "Choose one"))}</option>' for v in values]
    return f'<select name="{h(name)}" {attrs}>' + "".join(options) + "</select>"


def _stars(rating, reviews) -> str:
    if rating is None:
        return '<span class="muted">None</span>'
    count = f' <span class="muted">({h(reviews)})</span>' if reviews is not None else ""
    return f'<span class="stars">&#9733;</span> {h(rating)}{count}'


# ---------------------------------------------------------------- leads page

def _funnel(contacts: list[dict]) -> str:
    counts = {s: sum(1 for c in contacts if c.get("pipeline_stage") == s) for s in LEAD_STAGES}
    total = max(sum(counts.values()), 1)
    bar = "".join(f'<i class="seg seg-{s}" style="width:{counts[s] / total * 100:.2f}%" title="{h(STAGE_LABEL[s])}: {counts[s]}"></i>'
                  for s in LEAD_STAGES if counts[s])
    cells = "".join(f'<a class="stage" href="/?stage={s}"><b>{counts[s]}</b><span><i class="seg-{s}"></i>{h(STAGE_LABEL[s])}</span></a>' for s in LEAD_STAGES)
    return f'<div class="funnel" aria-hidden="true">{bar}</div><div class="stages">{cells}</div>'


def _today_panel(contacts: list[dict]) -> str:
    today = date.today().isoformat()
    due = sorted((c for c in contacts if c.get("next_follow_up_date") and c["next_follow_up_date"] <= today
                  and c.get("pipeline_stage") not in ("live", "lost")), key=lambda c: c["next_follow_up_date"])
    rows = []
    for c in due[:8]:
        overdue = c["next_follow_up_date"] < today
        when = f'<span class="due">Overdue since {h(c["next_follow_up_date"])}</span>' if overdue else '<span class="pill accent">Due today</span>'
        rows.append(f'<div class="today-row"><div><b><a href="/contact/{c["id"]}">{h(c["business_name"])}</a></b>'
                    f'<div class="muted small">{h(c.get("phone") or "No phone saved")}</div></div><div>{when}</div></div>')
    more = f'<p class="muted small">And {len(due) - 8} more.</p>' if len(due) > 8 else ""
    body = "".join(rows) or '<div class="empty">Nothing is due. Open a lead and set a follow-up date to see it here.</div>'
    return f'<section class="panel"><div class="panel-head"><h2>Follow up today</h2><span>{len(due)} waiting</span></div>{body}{more}</section>'


def _contact_rows(contacts: list[dict]) -> str:
    today = date.today().isoformat()
    rows = []
    for c in contacts:
        maps = f'https://www.google.com/maps/search/?api=1&query={quote(c.get("business_name", "") + " " + c.get("address", ""))}'
        badge = ' <span class="pill dark">Not on Maps</span>' if c.get("found_via") == FINDER_SOURCE else ""
        follow = c.get("next_follow_up_date") or ""
        if follow and follow < today and c.get("pipeline_stage") not in ("live", "lost"):
            follow_html = f'<span class="due">{h(follow)}</span>'
        else:
            follow_html = h(follow) if follow else '<span class="muted">None set</span>'
        contact = h(c.get("phone")) or '<span class="muted">None</span>'
        if c.get("email"):
            contact += f'<div class="muted small">{h(c.get("email"))}</div>'
        rows.append(
            f'<tr><td><div class="biz">{avatar(c["business_name"])}<div><a href="/contact/{c["id"]}">{h(c["business_name"])}</a>{badge}'
            f'<div class="muted small">{h(c.get("sector"))}</div></div></div></td>'
            f'<td class="nw">{h(c.get("town"))}</td><td class="nw">{contact}</td><td>{web_pill(c.get("website_detail"))}</td>'
            f'<td class="nw">{_stars(c.get("rating"), c.get("review_count"))}</td><td>{stage_pill(c.get("pipeline_stage"))}</td>'
            f'<td class="nw">{follow_html}</td><td><div class="row-actions"><a class="btn sm" href="/contact/{c["id"]}">Open</a>'
            f'<a class="btn sm" target="_blank" rel="noreferrer" href="{h(maps)}">Maps</a></div></td></tr>')
    return "".join(rows) or '<tr><td colspan="8"><div class="empty">No leads match these filters.</div></td></tr>'


def main_page(params: dict[str, str], message: str = "") -> bytes:
    tenant = tenant_id()
    p = pricing()
    all_contacts = list_contacts(db_path(), tenant_id=tenant, limit=5000)
    filters = {k: params.get(k, "") for k in ("q", "website_status", "website_detail", "town", "stage")}
    src = params.get("src", "")
    outreach = params.get("outreach_allowed", "")
    contacts = list_contacts(db_path(), website_status=filters["website_status"], town=filters["town"],
                             website_detail=filters["website_detail"], stage=filters["stage"],
                             outreach_allowed=int(outreach) if outreach in ("0", "1") else None,
                             tenant_id=tenant, limit=5000)
    if src == "not_listed":
        contacts = [c for c in contacts if c.get("found_via") == FINDER_SOURCE]
    q = filters["q"].casefold()
    if q:
        contacts = [c for c in contacts if q in " ".join(str(c.get(k) or "") for k in
                    ("business_name", "first_name", "surname", "phone", "email", "address", "website_url", "sector", "notes")).casefold()]
    mrr = sum(float(c.get("monthly_fee") or 0) + (p["hosting"] if c.get("hosting_enabled") else 0)
              for c in all_contacts if c.get("pipeline_stage") == "live")
    live = sum(1 for c in all_contacts if c.get("pipeline_stage") == "live")

    counts = {s: sum(1 for c in all_contacts if c.get("website_detail") == s) for s in WEBSITE_DETAILS}
    not_listed_n = sum(1 for c in all_contacts if c.get("found_via") == FINDER_SOURCE)
    chip_defs = [("All", "/", len(all_contacts), not any(filters.values()) and not src)]
    for key, label in (("own_website_listed", "Own website"), ("no_website_listed", "No website"),
                       ("social_page_only", "Social page only"), ("directory_page_only", "Directory only")):
        chip_defs.append((label, f"/?website_detail={key}", counts[key], filters["website_detail"] == key and not src))
    chip_defs.append(("Not on Google Maps", "/?src=not_listed", not_listed_n, src == "not_listed"))
    chips = "".join(f'<a class="chip{" on" if on else ""}" href="{href}">{h(label)} <b>{n}</b></a>' for label, href, n, on in chip_defs)

    csv_params = "&".join(f"{k}={quote(v)}" for k, v in filters.items() if v)
    town_opts = '<option value="">All towns</option>' + "".join(f'<option value="{h(t)}"{" selected" if filters["town"] == t else ""}>{h(t)}</option>' for t in TOWNS)
    stage_opts = '<option value="">All stages</option>' + "".join(f'<option value="{h(t)}"{" selected" if filters["stage"] == t else ""}>{h(STAGE_LABEL[t])}</option>' for t in LEAD_STAGES)
    detail_opts = '<option value="">Any web presence</option>' + "".join(
        f'<option value="{h(t)}"{" selected" if filters["website_detail"] == t else ""}>{h(WEB_LABEL[t][0])}</option>' for t in WEBSITE_DETAILS)
    outreach_opts = (f'<option value="">Outreach: any</option><option value="1"{" selected" if outreach == "1" else ""}>Outreach allowed</option>'
                     f'<option value="0"{" selected" if outreach == "0" else ""}>Outreach not allowed</option>')
    hidden_src = f'<input type="hidden" name="src" value="{h(src)}">' if src else ""
    edit_prices = "".join(f'<li>{h(k.replace("_", " ").capitalize())}: {money(v)}</li>' for k, v in p["edits"].items())
    retainer_list = "".join(f'<li>{h(x.get("name"))}: {money(x.get("price_zar_month"))} a month</li>' for x in p["retainers"])

    today_iso = date.today().isoformat()
    due_n = sum(1 for c in all_contacts if c.get("next_follow_up_date") and c["next_follow_up_date"] <= today_iso
                and c.get("pipeline_stage") not in ("live", "lost"))
    verified_n = sum(1 for c in all_contacts if c.get("details_verified"))
    verified_pct = round(verified_n / len(all_contacts) * 100) if all_contacts else 0
    build_n, audit_n = counts["no_website_listed"], counts["own_website_listed"]
    kpis = (
        f'<div class="kpis">'
        f'<a class="kpi" href="/"><div><div class="lab">Total leads</div><div class="num">{len(all_contacts)}</div><div class="sub">{live} live client{"s" if live != 1 else ""}</div></div></a>'
        f'<a class="kpi blue" href="/?website_detail=no_website_listed"><div><div class="lab">Build leads</div><div class="num">{build_n}</div><div class="sub">No website listed &middot; {money(build_n * p["build"])} potential</div></div></a>'
        f'<a class="kpi amber" href="/?website_detail=own_website_listed"><div><div class="lab">Audit leads</div><div class="num">{audit_n}</div><div class="sub">Own website listed</div></div></a>'
        f'<div class="kpi mint"><div><div class="lab">Checked by you</div><div class="num">{verified_n}</div><div class="sub">of {len(all_contacts)} verified</div></div>{dial(verified_pct, "%", "good")}</div>'
        f'<div class="kpi"><div><div class="lab">Follow up</div><div class="num">{due_n}</div><div class="sub">due or overdue</div></div></div>'
        f'</div>')
    body = f'''
<div class="head"><div><h1>Leads</h1><p>Check public listings before you contact anyone. A search that finds no website does not prove the business has none.</p></div>
<div class="actions"><a class="btn" href="/export.csv?{csv_params}">Export CSV</a></div></div>
{kpis}
<div class="hero">{_today_panel(all_contacts)}
<div class="stack"><section class="panel mrr"><h3 style="color:var(--muted);font-weight:500">Monthly recurring income</h3>
<div class="big">{money(mrr)}<small>a month</small></div><p>From {live} live client{"s" if live != 1 else ""}, including hosting.</p></section>
<section class="panel"><h2>Find more leads</h2><div class="finder-links">
<form method="get" action="/sweep/plan"><input name="town" required placeholder="Town, for example Kenton-on-Sea" aria-label="Town"><input name="max_searches" type="number" min="1" max="50" value="12" style="width:72px" aria-label="Most searches"><button class="btn dark">Plan Google Maps search</button></form>
<p class="note" style="margin:0">Planning is free. Nothing is searched until you confirm the count.</p>
<a class="btn" href="/not-listed">Find businesses not on Google Maps</a></div></section></div></div>
<section class="panel"><div class="panel-head"><h2>Pipeline</h2><span>{len(all_contacts)} leads in total</span></div>{_funnel(all_contacts)}</section>
<div class="chips">{chips}</div>
<form class="filters" method="get">{hidden_src}<input type="search" name="q" value="{h(filters["q"])}" placeholder="Search business, contact, phone, address" aria-label="Search leads">
<select name="website_detail" aria-label="Web presence">{detail_opts}</select><select name="town" aria-label="Town">{town_opts}</select>
<select name="stage" aria-label="Stage">{stage_opts}</select><select name="outreach_allowed" aria-label="Outreach">{outreach_opts}</select>
<button class="btn dark">Filter</button><a class="btn" href="/">Clear</a></form>
<div class="muted small" style="margin-bottom:8px">Showing {len(contacts)} of {len(all_contacts)}</div>
<div class="tbl-wrap"><table><thead><tr><th>Business</th><th>Town</th><th>Contact</th><th>Web presence</th><th>Google rating</th><th>Stage</th><th>Follow up</th><th></th></tr></thead>
<tbody>{_contact_rows(contacts)}</tbody></table></div>
<details class="panel" style="margin-top:18px"><summary style="cursor:pointer;font-weight:650">Pricing reference</summary>
<p style="margin:12px 0 4px">Website build up to {p["pages"]} pages: <b>{money(p["build"])}</b> once off. Hosting: <b>{money(p["hosting"])}</b> a month. Audit fixes: <b>{money(p["point"])}</b> per missing point.</p>
{f'<h3 style="margin:12px 0 4px">Monthly retainers</h3><ul>{retainer_list}</ul>' if retainer_list else ''}
{f'<h3 style="margin:12px 0 4px">Once-off edits</h3><ul>{edit_prices}</ul>' if edit_prices else ''}</details>
<p class="muted small">Database: {h(db_path())}</p>'''
    return layout("Leads", body, "leads", message)


# ---------------------------------------------------------------- profile page

def rank_history(contact_id: int) -> list[dict]:
    out = []
    try:
        for path in sorted(RANK_DIR.glob(f"rank-contact-{int(contact_id)}-*.json"), reverse=True)[:3]:
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _rank_pill(position) -> str:
    if not position:
        return '<span class="pill">Not found</span>'
    cls = "ok" if position <= 3 else ("info" if position <= 10 else "warn")
    return f'<span class="pill {cls}">#{int(position)}</span>'


def _rank_block(contact_id: int) -> str:
    runs = rank_history(contact_id)
    if not runs:
        return '<p class="note" style="margin:12px 0 0">No ranking check yet. Run one to see where this business shows up when a customer searches.</p>'
    latest = runs[0]
    rows = "".join(
        f'<tr><td>{h(r.get("keyword"))}<div class="muted small">{h(r.get("match_type") or "no match")}</div></td>'
        f'<td>{_rank_pill(r.get("position"))}</td><td>{("#" + str(r["map_pack_position"])) if r.get("map_pack_position") else "<span class=muted>None</span>"}</td></tr>'
        for r in latest.get("results", []))
    when = h(str(latest.get("generated_at", ""))[:16].replace("T", " "))
    kind = "Google (South Africa)" if latest.get("source") == "serpapi" else "Free search, indicative only"
    err = f'<p class="note" style="color:var(--bad)">Some searches failed: {h("; ".join(latest.get("errors", []))[:200])}</p>' if latest.get("errors") else ""
    return (f'<hr class="rule"><h3>Latest check</h3><p class="note" style="margin:2px 0 8px">{when}. {h(kind)}. This is one search on one day, so say "when I searched on {when[:10]}".</p>'
            f'<table class="rank-table"><thead><tr><th>Search</th><th>Position</th><th>Map pack</th></tr></thead><tbody>{rows}</tbody></table>{err}')


def _audit_job_banner(contact_id: int) -> tuple[str, int]:
    try:
        with connect(db_path()) as conn:
            row = conn.execute("select job_id,status,error_log from audit_jobs where contact_id=? and tenant_id=? order by created_at desc limit 1",
                               (int(contact_id), tenant_id())).fetchone()
    except Exception:
        return "", 0
    if not row:
        return "", 0
    status = row["status"]
    if status in ("queued", "processing"):
        return ('<div class="banner"><span class="job"><span class="dot"></span><span>An audit is running for this business. This page refreshes by itself. '
                'It can take a few minutes.</span></span></div>'), 6
    if status == "failed" and row["error_log"]:
        return f'<div class="banner bad">The last audit failed: {h(str(row["error_log"])[:300])}</div>', 0
    return "", 0


def _client_report_panel(item: dict) -> str:
    """Preview the narrowly scoped customer view and require an owner review."""
    from customer_report import to_customer_report

    try:
        report = to_customer_report(item.get("full_audit") or {})
    except (TypeError, ValueError):
        return '<div class="banner bad">This audit does not yet have a publishable customer report. Check its business website and findings.</div>'

    finding_cards = []
    for finding in report["findings"]:
        finding_cards.append(
            '<article class="scope"><b>' + h(finding["priority"] + ": " + finding["title"]) + '</b>'
            + '<p><strong>What we found:</strong> ' + h(finding["whatWeFound"]) + '</p>'
            + '<p><strong>Why it matters:</strong> ' + h(finding["whyItMatters"]) + '</p>'
            + '<p><strong>What to do next:</strong> ' + h(finding["whatToDo"]) + '</p></article>'
        )
    findings_html = "".join(finding_cards) or '<p class="muted">No automated improvement items were produced. Review the full audit manually before publishing.</p>'
    report_url = str(item.get("client_report_url") or "")
    published = (
        '<div class="banner ok"><strong>Online customer report published</strong><p><a class="btn sm" target="_blank" rel="noreferrer" href="'
        + h(report_url) + '">Open customer report</a></p><p class="muted">Anyone with this private link can view it. It is designed to expire after 30 days. Publishing an updated copy creates a new link; the previous link remains active until its expiry.</p></div>'
        if report_url else ""
    )
    return (
        '<section class="panel"><h3>Customer-friendly online report</h3>'
        '<p class="note">Review this version before publishing. It omits internal scores, pricing, sales notes and the full audit. The customer sees the observed issue, why it may matter and a practical fix.</p>'
        + published
        + '<details><summary>Preview what the customer will see</summary><h4>' + h(report["businessName"]) + '</h4>'
        + '<p>' + h(report["summary"]) + '</p>' + findings_html
        + '<p class="muted">' + h(report["scopeNote"]) + '</p></details>'
        + '<form method="post" action="/publish-report/' + str(int(item["id"])) + '" class="actions" style="margin-top:12px">'
        + '<label><input type="checkbox" name="reviewed" value="yes" required> I reviewed the customer wording and checked the findings for accuracy.</label>'
        + '<button class="btn primary" type="submit">' + ('Publish updated report link' if report_url else 'Publish secure online report') + '</button></form></section>'
    )


def _activity_panel(contact: dict, histories: list[dict]) -> str:
    from crm_ops import list_activities, personalised_templates, top_audit_finding
    try:
        events = list_activities(contact["id"], db_path(), tenant_id())
        finding = top_audit_finding(contact["id"], db_path(), tenant_id())
    except Exception:
        events, finding = [], ""
    templates = personalised_templates(contact, finding)
    phone = re.sub(r"\D", "", str(contact.get("phone") or ""))
    if phone.startswith("0"):
        phone = "27" + phone[1:]
    wa = f'https://wa.me/{phone}?text={quote(templates["whatsapp_audit"])}' if phone else ""
    mail = "mailto:" + quote(str(contact.get("email") or "")) + "?subject=" + quote(templates["email_audit_subject"]) + "&body=" + quote(templates["email_audit_body"]) if contact.get("email") else ""
    drafts = ('<a class="btn sm primary" target="_blank" rel="noreferrer" href="' + h(wa) + '">Open WhatsApp draft</a>' if wa else '<span class="pill warn">Add a phone number for WhatsApp</span>')
    drafts += (' <a class="btn sm" href="' + h(mail) + '">Open email draft</a>' if mail else '')
    rows = []
    for event in events:
        outcome = f'<p class="muted"><b>Outcome:</b> {h(event["outcome"])}</p>' if event["outcome"] else ""
        rows.append('<div class="history"><div class="actions"><span class="pill accent">' + h(event["event_type"].title()) + '</span><span class="pill">' + h(event["channel"] or "Channel not set") + '</span><span class="pill">' + h(event["direction"].title()) + '</span><span class="muted small">' + h(event["created_at"][:16].replace("T", " ")) + '</span></div><p>' + h(event["summary"]) + '</p>' + outcome + '</div>')
    timeline = "".join(rows) or '<p class="muted">No calls or messages logged yet. Record each conversation here.</p>'
    type_opts = _select("event_type", ["call", "whatsapp", "email", "meeting", "note", "other"], "call", {"call":"Call", "whatsapp":"WhatsApp", "email":"Email", "meeting":"In person / meeting", "note":"Note", "other":"Other"})
    return f'''<section class="panel"><div class="panel-head"><h2>Contact timeline</h2><span>{len(events)} logged</span></div>
<p class="note">Use a ready-made draft as a starting point, then review and send it yourself. The audit finding is pulled from the latest saved audit.</p><div class="actions">{drafts}</div>
<details style="margin-top:12px"><summary>Message templates</summary><p class="small"><b>WhatsApp audit opener:</b> {h(templates["whatsapp_audit"])}</p><p class="small"><b>Follow-up:</b> {h(templates["whatsapp_followup"])}</p><p class="small"><b>Email subject:</b> {h(templates["email_audit_subject"])}</p></details>
<form method="post" action="/activity/add" class="grid" style="margin-top:14px"><input type="hidden" name="contact_id" value="{contact["id"]}">
<div class="field">{type_opts}</div><div class="field"><label class="lab">Direction</label><select name="direction"><option value="outbound">I contacted them</option><option value="inbound">They contacted me</option><option value="internal">Internal note</option></select></div><div class="field"><label class="lab">Channel</label><input name="channel" placeholder="Phone, WhatsApp, email, in person"></div>
<div class="field wide"><label class="lab">What happened?</label><textarea name="summary" required maxlength="4000" placeholder="What you said, sent, or discussed"></textarea></div>
<div class="field wide"><label class="lab">Reply / outcome / next step</label><textarea name="outcome" maxlength="2000" placeholder="What they replied and agreed to do"></textarea></div>
<div class="actions wide"><button class="btn primary" type="submit">Add to timeline</button></div></form><hr class="rule">{timeline}</section>'''


def _finance_panel(contact: dict) -> str:
    from crm import connect
    tenant = tenant_id()
    with connect(db_path()) as conn:
        quotes = [dict(r) for r in conn.execute("SELECT * FROM crm_quotes WHERE contact_id=? AND tenant_id=? ORDER BY created_at DESC", (contact["id"], tenant))]
        invoices = [dict(r) for r in conn.execute("SELECT i.*,COALESCE(SUM(p.amount),0) paid FROM crm_invoices i LEFT JOIN crm_payments p ON p.invoice_id=i.id AND p.tenant_id=i.tenant_id WHERE i.contact_id=? AND i.tenant_id=? GROUP BY i.id ORDER BY i.issued_on DESC", (contact["id"], tenant))]
        recurring = [dict(r) for r in conn.execute("SELECT * FROM crm_recurring_items WHERE contact_id=? AND tenant_id=? AND active=1 ORDER BY next_due_on", (contact["id"], tenant))]
    quote_rows = []
    for q in quotes:
        status = _select("status", ["draft", "sent", "accepted", "declined", "expired"], q["status"])
        quote_rows.append(f'<tr><td>{h(q["quote_number"])}</td><td>{h(q["description"])}</td><td>{money(q["amount"])}</td><td><form method=post action="/quote/status"><input type=hidden name=quote_id value="{q["id"]}">{status}<button class="btn sm">Save</button></form></td></tr>')
    invoice_rows = []
    for inv in invoices:
        balance = max(0, float(inv["amount"]) - float(inv["paid"]))
        invoice_rows.append(f'<tr><td>{h(inv["invoice_number"])}</td><td>{h(inv["description"])}</td><td>{money(inv["amount"])}</td><td>{money(balance)} <span class="pill {"ok" if balance == 0 else "warn"}">{h(inv["status"])}</span></td><td><a class="btn sm" href="/invoice/{inv["id"]}/pdf">Branded PDF</a></td></tr>')
    inv_opts = '<option value="">No invoice (general payment)</option>' + ''.join(f'<option value="{r["id"]}">{h(r["invoice_number"])} — {money(max(0,float(r["amount"])-float(r["paid"])))}</option>' for r in invoices if r["status"] != "paid")
    rec_rows=[]
    for item in recurring:
        current_month = date.today().strftime("%Y-%m")
        paid = str(item.get("last_paid_on") or "").startswith(current_month)
        rec_rows.append(f'<tr><td>{h(item["description"] or item["item_type"].replace("_"," ").title())}</td><td>{money(item["amount"])} / {h(item["frequency"])}</td><td>{h(item["next_due_on"] or "Not set")}</td><td><span class="pill {"ok" if paid else "warn"}">{"Paid this month" if paid else "Not marked paid"}</span></td><td><form method=post action="/recurring/paid"><input type=hidden name=item_id value="{item["id"]}"><button class="btn sm" type=submit>Mark paid today</button></form></td></tr>')
    quote_table = '<div class="tbl-wrap"><table style="min-width:680px"><thead><tr><th>Quote</th><th>Scope</th><th>Amount</th><th>Status</th></tr></thead><tbody>' + (''.join(quote_rows) or '<tr><td colspan=4>No quotes recorded.</td></tr>') + '</tbody></table></div>'
    invoice_table = '<div class="tbl-wrap"><table style="min-width:720px"><thead><tr><th>Invoice</th><th>Scope</th><th>Total</th><th>Balance</th><th></th></tr></thead><tbody>' + (''.join(invoice_rows) or '<tr><td colspan=5>No invoices yet.</td></tr>') + '</tbody></table></div>'
    recurring_table = '<div class="tbl-wrap"><table style="min-width:760px"><thead><tr><th>Service</th><th>Charge</th><th>Next due</th><th>This month</th><th></th></tr></thead><tbody>' + (''.join(rec_rows) or '<tr><td colspan=5>No recurring charges or renewals tracked.</td></tr>') + '</tbody></table></div>'
    return f'''<section class="panel"><h2>Quotes, invoices & payments</h2><p class="note">Manual record-keeping only. No money is collected, and no payment is initiated by this CRM.</p><h3>Quotes</h3>{quote_table}
<form method="post" action="/quote/create" class="filters" style="margin-top:10px"><input type=hidden name=contact_id value="{contact["id"]}"><input name=description required placeholder="Quote description"><input name=amount type=number min="0" step="0.01" required placeholder="Amount (R)"><input name=due_at type=date aria-label="Quote expiry"><button class="btn">Create quote</button></form>
<h3 style="margin-top:16px">Invoices and recorded payments</h3>{invoice_table}
<form method="post" action="/invoice/create" class="filters" style="margin-top:10px"><input type=hidden name=contact_id value="{contact["id"]}"><input name=description required placeholder="Invoice description"><input name=amount type=number min="0.01" step="0.01" required placeholder="Amount (R)"><input name=due_on type=date required aria-label="Due date"><button class="btn primary">Create branded invoice</button></form>
<form method="post" action="/payment/add" class="filters" style="margin-top:8px"><input type=hidden name=contact_id value="{contact["id"]}"><select name=invoice_id>{inv_opts}</select><input name=amount type=number min="0.01" step="0.01" required placeholder="Amount received (R)"><input name=paid_on type=date value="{date.today().isoformat()}" required><input name=reference placeholder="Bank / payment reference"><button class="btn">Record payment</button></form>
<h3 style="margin-top:16px">Monthly fees and domain / hosting renewals</h3>{recurring_table}
<form method="post" action="/recurring/save" class="filters" style="margin-top:10px"><input type=hidden name=contact_id value="{contact["id"]}"><select name=item_type><option value=hosting>Hosting</option><option value=retainer>Retainer</option><option value=domain_renewal>Domain renewal</option><option value=other>Other</option></select><input name=description placeholder="Service / domain"><input name=amount type=number min="0" step="0.01" required placeholder="Amount (R)"><select name=frequency><option value=monthly>Monthly</option><option value=annual>Annual</option></select><input name=next_due_on type=date aria-label="Next due"><button class="btn">Track recurring item</button></form></section>'''


def _project_panel(contact: dict) -> str:
    from crm import connect
    with connect(db_path()) as conn:
        row = conn.execute("SELECT * FROM crm_projects WHERE contact_id=? AND tenant_id=?", (contact["id"], tenant_id())).fetchone()
    project = dict(row) if row else {}
    opts = _select("status", ["waiting_content", "in_design", "in_build", "review", "live", "paused"], project.get("status", "waiting_content"),
                   {"waiting_content":"Waiting for content", "in_design":"In design", "in_build":"In build", "review":"Client review", "live":"Live", "paused":"Paused"})
    return f'''<section class="panel"><h2>Client project</h2><p class="note">Keep delivery state and renewal notes separate from the sales stage. Do not store passwords here; use a password manager.</p>
<form method="post" action="/project/save" class="grid"><input type=hidden name=contact_id value="{contact["id"]}"><div class=field><label class=lab>Website build status</label>{opts}</div>
<div class=field><label class=lab>Domain</label><input name=domain value="{h(project.get("domain",""))}" placeholder="example.co.za"></div>
<div class=field><label class=lab>Started</label><input type=date name=started_on value="{h(project.get("started_on",""))}"></div><div class=field><label class=lab>Go-live date</label><input type=date name=go_live_on value="{h(project.get("go_live_on",""))}"></div>
<div class=field><label class=lab>Next milestone</label><input name=next_milestone value="{h(project.get("next_milestone",""))}" placeholder="Content hand-off / design review"></div><div class=field><label class=lab>Milestone date</label><input type=date name=next_milestone_on value="{h(project.get("next_milestone_on",""))}"></div>
<div class="field wide"><label class=lab>Hosting / access hand-off notes (no passwords)</label><textarea name=hosting_notes maxlength=4000>{h(project.get("hosting_notes",""))}</textarea></div><div class="field wide"><label class=lab>Project notes</label><textarea name=project_notes maxlength=4000>{h(project.get("project_notes",""))}</textarea></div><div class="actions wide"><button class="btn primary">Save project</button></div></form></section>'''


def profile_page(contact_id: int, message: str = "") -> tuple[bytes, int]:
    tenant = tenant_id()
    c = get_contact(contact_id, db_path(), tenant)
    if not c:
        return b"Customer not found", 0
    p = pricing()
    histories = audit_history(contact_id, db_path(), tenant)
    is_verified = bool(c.get("details_verified"))
    website_allowed = c.get("website_detail") == "own_website_listed" and bool(c.get("website_url"))
    banner, refresh = _audit_job_banner(contact_id)

    def field(label, name, value="", kind="text", extra="", wide=False):
        return (f'<div class="field{" wide" if wide else ""}"><label class="lab" for="{h(name)}">{h(label)}</label>'
                f'<input id="{h(name)}" name="{h(name)}" type="{kind}" value="{h(value)}" {extra}></div>')

    def select_field(label, name, options, selected, labels=None):
        return f'<div class="field"><label class="lab">{h(label)}</label>{_select(name, options, selected, labels)}</div>'

    soc_map, soc_other = split_socials(c.get("social_links", []))
    soc_rows = []
    for key, label, _d in PLATFORMS:
        val = soc_map.get(key, "")
        open_link = (f'<a class="btn sm" target="_blank" rel="noreferrer" href="{h(val)}">Open</a>' if val else "")
        soc_rows.append(f'<div class="field"><label class="lab" for="social_{key}">{h(label)}</label>'
                        f'<div class="actions"><input id="social_{key}" name="social_{key}" type="url" placeholder="https://" value="{h(val)}" style="flex:1;min-width:0">{open_link}</div></div>')
    social_panel = (f'<section class="panel"><h2>Social media</h2><div class="grid">{"".join(soc_rows)}'
                    f'<div class="field wide"><label class="lab" for="social_other">Other links, one per line</label>'
                    f'<textarea id="social_other" name="social_other">{h(chr(10).join(soc_other))}</textarea></div></div></section>')
    soc_chips = "".join(
        f'<a class="chip" target="_blank" rel="noreferrer" href="{h(soc_map[k])}"><b>{h(lbl)}</b></a>'
        for k, lbl, _d in PLATFORMS if k in soc_map)
    soc_chips += "".join(
        f'<a class="chip" target="_blank" rel="noreferrer" href="{h(u)}"><b>{h(urlparse(u).hostname or "Link")}</b></a>'
        for u in (safe_url(x) for x in soc_other) if u)
    category_values = c.get("categories", [])
    category_text = ", ".join(category_values) if isinstance(category_values, list) else str(category_values or "")
    web_labels = {k: v[0] for k, v in WEB_LABEL.items()}
    stage_labels = dict(STAGE_LABEL)
    retainer_options = "".join(f'<option value="{h(x.get("name"))}">' for x in p["retainers"])

    history_html = []
    for history_index, item in enumerate(histories):
        previous = histories[history_index + 1] if history_index + 1 < len(histories) else None
        try:
            change = int(item["score"]) - int(previous["score"]) if previous and item.get("score") is not None and previous.get("score") is not None else None
        except (TypeError, ValueError):
            change = None
        movement = (f'<span class="pill {"ok" if change >= 0 else "warn"}">{change:+d} points vs previous</span>' if change is not None else '<span class="pill">Baseline</span>')
        report_month = str(item.get("audit_date", ""))[:7]
        pdf_link = (f'<a class="btn sm" href="/pdf/{item["id"]}">Download PDF</a>'
                    if item.get("pdf_path") and Path(item["pdf_path"]).is_file() else '<span class="pill warn">PDF missing</span>')
        scope_rows = []
        for step in item.get("scope_items", []):
            scope_rows.append(
                f'<form class="scope" method="post" action="/scope/{item["id"]}/{quote(step["id"])}"><label>'
                f'<input type="checkbox" name="done" value="1"{" checked" if step.get("done") else ""} onchange="this.form.submit()"> '
                f'<b>{h(step["step"])}. {h(step["title"])}</b></label><p>Evidence: {h(step["evidence"])}</p><p>Action: {h(step["action"])}</p></form>')
        done = sum(bool(x.get("done")) for x in item.get("scope_items", []))
        total = len(item.get("scope_items", []))
        client_report_panel = _client_report_panel(item)
        history_html.append(
            f'<div class="history" id="scope-{item["id"]}"><div class="actions"><b>{h(str(item["audit_date"])[:16].replace("T", " "))}</b>'
            f'<span class="pill info">Score {h(item["score"])}/100</span>{movement}<span class="pill">{h(item["confidence"])} confidence</span>'
            f'<span class="pill accent">Quote {money(item["quote_amount"])}</span>{pdf_link}'
            f'<form method="post" action="/monthly/report"><input type="hidden" name="contact_id" value="{c["id"]}"><input type="hidden" name="month" value="{h(report_month)}"><button class="btn sm">Monthly summary</button></form></div>'
            f'<details open><summary>Scope of Work: {done} of {total} done</summary>{"".join(scope_rows) or "<p class=muted>No action steps were generated.</p>"}</details>'
            f'<details><summary>Audit results and evidence</summary><pre>{h(json.dumps(item["summary"], indent=2, ensure_ascii=False))}</pre></details>'
            f'{client_report_panel}</div>')

    audit_disabled = "" if is_verified and website_allowed else "disabled"
    audit_note = ("This record is verified and has its own website. It is ready to audit." if not audit_disabled
                  else "To audit, tick the verified box, set the website category to Own website, add the website address and save.")
    sow_button = (f'<a class="btn block" href="#scope-{histories[0]["id"]}">Open Scope of Work</a>' if histories else '')
    maps_url = f'https://www.google.com/maps/search/?api=1&query={quote(c["business_name"] + " " + c.get("address", ""))}'
    site_link = f'<a class="btn sm" target="_blank" rel="noreferrer" href="{h(c["website_url"])}">Visit website</a>' if c.get("website_url") else ""
    tags = (f'{stage_pill(c.get("pipeline_stage"))}{web_pill(c.get("website_detail"))}'
            f'<span class="pill {"ok" if is_verified else "warn"}">{"Verified" if is_verified else "Not verified"}</span>'
            f'<span class="pill {"ok" if c.get("outreach_allowed") else ""}">{"Outreach allowed" if c.get("outreach_allowed") else "Outreach off"}</span>'
            + ('<span class="pill dark">Not on Maps</span>' if c.get("found_via") == FINDER_SOURCE else ""))
    loc_bits = [x for x in (c.get("town"), c.get("sector")) if x]
    score_block = ""
    if histories:
        latest = histories[0]
        score_block = (f'<div class="score">{dial(latest.get("score"))}<div><div class="lab">Latest audit</div>'
                       f'<div class="q">{money(latest.get("quote_amount"))}</div>'
                       f'<div class="muted small">{h(str(latest.get("audit_date"))[:10])}</div></div></div>')

    name_attrs = 'readonly title="The name is fixed once a lead is created"'
    form = f'''<form method="post" action="/profile/save"><input type="hidden" name="id" value="{c["id"]}">
<section class="panel"><h2>Business and contact</h2><div class="grid">
{field("Business name", "business_name", c["business_name"], "text", name_attrs)}{field("Contact first name", "first_name", c["first_name"])}{field("Surname", "surname", c["surname"])}
{field("Phone", "phone", c["phone"], "tel")}{field("Email", "email", c["email"], "email")}{select_field("Town", "town", TOWNS, c["town"])}
{field("Address", "address", c["address"])}{field("Sector", "sector", c["sector"])}{field("Google categories", "categories", category_text)}</div></section>
<section class="panel"><h2>Web presence</h2><div class="grid">
{field("Official business website", "website_url", c["website_url"], "url")}{select_field("Website category", "website_detail", WEBSITE_DETAILS, c["website_detail"], web_labels)}
{select_field("Website status", "website_status", WEBSITE_STATUSES, c["website_status"])}
{field("Google rating", "rating", c["rating"], "number", 'step="0.1"')}{field("Google review count", "review_count", c["review_count"], "number", 'min="0"')}{field("Place ID", "place_id", c["place_id"])}
{field("Latitude", "latitude", c["latitude"], "number", 'step="any"')}{field("Longitude", "longitude", c["longitude"], "number", 'step="any"')}</div></section>
{social_panel}
<section class="panel"><h2>Checks and permission</h2><div class="grid"><div class="checks">
<label><input type="checkbox" name="details_verified" value="1"{" checked" if is_verified else ""}> I checked these business details and the official website myself</label>
<label><input type="checkbox" name="outreach_allowed" value="1"{" checked" if c.get("outreach_allowed") else ""}> Outreach is allowed after my checks</label>
<label><input type="checkbox" name="marketing_consent" value="1"{" checked" if c.get("marketing_consent") else ""}> Marketing consent is recorded</label></div>
{field("Last contacted", "last_contacted_at", c.get("last_contacted_at"))}{select_field("Lead lifecycle", "lifecycle_status", ["Scraped", "Awaiting_Outreach", "Contacted", "Suppressed"], c.get("lifecycle_status", "Scraped"))}</div></section>
<section class="panel"><h2>Sales and follow-up</h2><div class="grid">
{select_field("Pipeline stage", "pipeline_stage", LEAD_STAGES, c.get("pipeline_stage", "new"), stage_labels)}{field("Next follow-up date", "next_follow_up_date", c.get("next_follow_up_date"), "date")}
{field("One-off quote (R)", "quote_amount", c.get("quote_amount"), "number", 'min="0" step="0.01"')}{select_field("Quote type", "quote_type", QUOTE_TYPES, c.get("quote_type", ""))}
{field("Monthly fee (R)", "monthly_fee", c.get("monthly_fee"), "number", 'min="0" step="0.01"')}
<div class="field"><label class="lab">Hosting</label><label style="display:flex;gap:8px;align-items:center;padding:8px 0"><input type="checkbox" name="hosting_enabled" value="1"{" checked" if c.get("hosting_enabled") else ""}> Hosting is active ({money(p["hosting"])} a month)</label></div>
{field("Retainer", "retainer_name", c.get("retainer_name"), "text", 'list="retainers"')}<datalist id="retainers">{retainer_options}</datalist>{field("Lead status", "lead_status", c.get("lead_status"))}
<div class="field wide"><label class="lab" for="notes">Notes</label><textarea id="notes" name="notes">{h(c.get("notes"))}</textarea></div></div></section>
<div class="savebar"><button class="btn primary" type="submit">Save profile</button></div></form>'''

    aside = f'''<aside class="aside"><section class="panel"><h2>Audit and rankings</h2>
<p class="note">{h(audit_note)}</p>
<form method="post" action="/audit/{c["id"]}" onsubmit="return confirm('Run one live audit for this verified business now? This crawls the website and creates a PDF.');"><button class="btn primary block" type="submit" {audit_disabled}>Audit Business</button></form>
{f'<div style="margin-top:8px">{sow_button}</div>' if sow_button else ''}
<hr class="rule">
<form class="rank-form" method="post" action="/rank/{c["id"]}" onsubmit="return confirmRank(this);">
<label class="lab small muted" for="rk_source">Where to check</label>
<select id="rk_source" name="source"><option value="serpapi">Google (uses about 3 searches)</option><option value="ddgs">Free search (indicative)</option></select>
<label class="lab small muted" for="rk_kw">Searches to check (optional, separate with semicolons)</label>
<input id="rk_kw" name="keywords" placeholder="plumber {h(c.get("town") or "your town")}; best plumber {h(c.get("town") or "your town")}">
<button class="btn dark block" type="submit">Check rankings</button></form>
{_rank_block(c["id"])}
</section>
<section class="panel"><h2>Quick links</h2><div class="actions"><a class="btn sm" target="_blank" rel="noreferrer" href="{h(maps_url)}">Google Maps search</a>{site_link}</div></section></aside>
<script>function confirmRank(f){{var s=f.source.value;return confirm(s==='serpapi'?'Check where this business ranks on Google? This uses about 3 of your monthly SerpApi searches (fewer if the results are saved from the last 7 days).':'Run the free ranking check? The results are indicative only.');}}</script>'''

    sources = "".join(f'<p><a target="_blank" rel="noreferrer" href="{h(u)}">{h(u)}</a></p>' for u in c.get("source_urls", [])) or '<p class="muted">No source links saved.</p>'
    meta_line = " &middot; ".join(h(x) for x in loc_bits)
    activity_panel = _activity_panel(c, histories)
    finance_panel = _finance_panel(c)
    project_panel = _project_panel(c)
    current_month = date.today().strftime("%Y-%m")
    privacy_panel = f'''<section class="panel"><h2>Privacy &amp; record safety</h2><p class="note">Source recorded: <b>{h(c.get("found_via") or "not recorded")}</b>. Outreach permission is currently <b>{"allowed after owner checks" if c.get("outreach_allowed") else "off"}</b>.</p><form method="post" action="/privacy/opt-out"><input type="hidden" name="contact_id" value="{c["id"]}"><input type="hidden" name="reason" value="Client requested no further contact"><button class="btn">Record opt-out / do not contact</button></form><details style="margin-top:14px"><summary>Erase this contact and its saved reports</summary><p class="note">This permanently deletes the CRM record, timeline, finance and project records, audits, and associated report files. Only hashed do-not-contact identifiers remain. Make a backup first if required.</p><form method="post" action="/privacy/erase"><input type="hidden" name="contact_id" value="{c["id"]}"><label class="lab">Type DELETE to confirm</label><input name="confirmation" required autocomplete="off"><button class="btn" style="margin-top:8px">Permanently erase</button></form></details></section>'''
    monthly_panel = f'''<section class="panel"><h2>Monthly client progress</h2><p class="note">Creates a concise branded PDF from a saved audit in the selected month. It includes score movement and priority work; it does not run a new audit.</p><form method="post" action="/monthly/report" class="filters"><input type="hidden" name="contact_id" value="{c["id"]}"><input type="month" name="month" value="{current_month}" required><button class="btn">Generate monthly report</button></form></section>'''
    body = f'''<div class="crumb"><a href="/">Leads</a> / {h(c["business_name"])}</div>{banner}
<div class="phead">{avatar(c["business_name"], True)}<div class="who"><h1>{h(c["business_name"])}</h1><div class="muted">{meta_line}</div><div class="tags">{tags}</div>{f'<div class="chips" style="margin:10px 0 0">{soc_chips}</div>' if soc_chips else ''}</div>{score_block}</div>
<div class="profile"><div>{form}
{activity_panel}{finance_panel}{project_panel}{monthly_panel}{privacy_panel}
<section class="panel"><div class="panel-head"><h2>Audit history</h2><span>{len(histories)} on record</span></div>{"".join(history_html) or '<p class="muted">No audits yet. Verify the business and run one from the panel on the right.</p>'}</section>
<section class="panel src-links"><h2>Source links</h2>{sources}</section></div>{aside}</div>'''
    return layout(c["business_name"], body, "leads", message, refresh), refresh


# ---------------------------------------------------------------- audited businesses

def audited_page(params: dict[str, str], message: str = "") -> bytes:
    tenant = tenant_id()
    q = (params.get("q") or "").strip().casefold()
    band = params.get("band", "")
    sort = params.get("sort", "recent")
    rows_data = []
    for c in list_contacts(db_path(), tenant_id=tenant, limit=5000):
        try:
            hist = audit_history(c["id"], db_path(), tenant)
        except Exception:
            hist = []
        if not hist:
            continue
        latest = hist[0]
        try:
            score = float(latest.get("score"))
        except (TypeError, ValueError):
            score = None
        steps = latest.get("scope_items", []) or []
        rows_data.append({"c": c, "latest": latest, "count": len(hist), "score": score,
                          "done": sum(bool(x.get("done")) for x in steps), "total": len(steps)})
    everything = list(rows_data)
    if q:
        rows_data = [r for r in rows_data if q in " ".join(str(r["c"].get(k) or "") for k in ("business_name", "town", "sector", "website_url")).casefold()]
    bands = {"high": lambda s: s is not None and s >= 75, "mid": lambda s: s is not None and 50 <= s < 75, "low": lambda s: s is not None and s < 50}
    if band in bands:
        rows_data = [r for r in rows_data if bands[band](r["score"])]
    if sort == "score_low":
        rows_data.sort(key=lambda r: (r["score"] is None, r["score"] if r["score"] is not None else 0))
    elif sort == "score_high":
        rows_data.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
    elif sort == "quote":
        rows_data.sort(key=lambda r: -float(r["latest"].get("quote_amount") or 0))
    else:
        rows_data.sort(key=lambda r: str(r["latest"].get("audit_date") or ""), reverse=True)

    scored = [r["score"] for r in everything if r["score"] is not None]
    avg = round(sum(scored) / len(scored)) if scored else None
    quote_total = sum(float(r["latest"].get("quote_amount") or 0) for r in everything)
    steps_total = sum(r["total"] for r in everything)
    steps_done = sum(r["done"] for r in everything)
    kpis = (f'<div class="kpis">'
            f'<div class="kpi"><div><div class="lab">Businesses audited</div><div class="num">{len(everything)}</div><div class="sub">latest audit counted</div></div></div>'
            f'<div class="kpi blue"><div><div class="lab">Average score</div><div class="num">{avg if avg is not None else "n/a"}</div><div class="sub">out of 100</div></div>{dial(avg)}</div>'
            f'<div class="kpi amber"><div><div class="lab">Quoted from audits</div><div class="num">{money(quote_total)}</div><div class="sub">once-off, latest quotes</div></div></div>'
            f'<div class="kpi mint"><div><div class="lab">Scope done</div><div class="num">{steps_done}<small class="muted" style="font-size:15px"> / {steps_total}</small></div><div class="sub">action steps ticked off</div></div></div>'
            f'</div>')
    kpis = kpis.replace('class="kpis"', 'class="kpis" style="grid-template-columns:repeat(4,minmax(0,1fr))"')

    def chip(label, key, value, n):
        on = (params.get(key, "") == value) if value else not params.get("band")
        keep = {k: v for k, v in params.items() if k in ("q", "sort") and v}
        keep.update({key: value} if value else {})
        qs = "&".join(f"{k}={quote(v)}" for k, v in keep.items())
        return f'<a class="chip{" on" if on else ""}" href="/audited{"?" + qs if qs else ""}">{h(label)} <b>{n}</b></a>'
    counts = {k: sum(1 for r in everything if f(r["score"])) for k, f in bands.items()}
    chips = (chip("All", "band", "", len(everything)) + chip("75 and above", "band", "high", counts["high"])
             + chip("50 to 74", "band", "mid", counts["mid"]) + chip("Under 50", "band", "low", counts["low"]))
    sort_opts = "".join(f'<option value="{v}"{" selected" if sort == v else ""}>{h(label)}</option>' for v, label in
                        (("recent", "Most recent"), ("score_low", "Lowest score first"), ("score_high", "Highest score first"), ("quote", "Biggest quote first")))

    rows = []
    for r in rows_data:
        c, latest = r["c"], r["latest"]
        pdf = (f'<a class="btn sm" href="/pdf/{latest["id"]}">PDF</a>'
               if latest.get("pdf_path") and Path(str(latest["pdf_path"])).is_file() else '<span class="pill warn">PDF missing</span>')
        pct = f'{r["done"]} of {r["total"]}' if r["total"] else "No steps"
        more = f' <span class="muted small">({r["count"]} audits)</span>' if r["count"] > 1 else ""
        rows.append(
            f'<tr><td><div class="biz">{avatar(c["business_name"])}<div><a href="/contact/{c["id"]}">{h(c["business_name"])}</a>'
            f'<div class="muted small">{h(c.get("town"))} &middot; {h(c.get("sector"))}</div></div></div></td>'
            f'<td>{dial(r["score"], sm=True)}</td>'
            f'<td class="nw"><b>{money(latest.get("quote_amount"))}</b></td>'
            f'<td class="nw">{h(str(latest.get("audit_date"))[:10])}{more}</td>'
            f'<td class="nw">{h(pct)}</td><td>{stage_pill(c.get("pipeline_stage"))}</td>'
            f'<td><div class="row-actions">{pdf}<a class="btn sm" href="/contact/{c["id"]}#scope-{latest["id"]}">Scope of Work</a>'
            f'<a class="btn sm" href="/contact/{c["id"]}">Open</a></div></td></tr>')
    table = ('<div class="tbl-wrap"><table><thead><tr><th>Business</th><th>Score</th><th>Quote</th><th>Audited</th><th>Scope</th><th>Stage</th><th></th></tr></thead>'
             f'<tbody>{"".join(rows) or "<tr><td colspan=7><div class=empty>No audited businesses match. Run an audit from a lead profile and it will appear here.</div></td></tr>"}</tbody></table></div>')
    hidden = f'<input type="hidden" name="band" value="{h(band)}">' if band else ""
    body = f'''<div class="head"><div><h1>Audited</h1><p>Every business that has an audit on record. Open one to see its scope of work, download the PDF or follow up.</p></div></div>
{kpis}
<div class="chips">{chips}</div>
<form class="filters" method="get" action="/audited">{hidden}<input type="search" name="q" value="{h(params.get("q", ""))}" placeholder="Search audited businesses" aria-label="Search">
<select name="sort" aria-label="Sort">{sort_opts}</select><button class="btn dark">Apply</button><a class="btn" href="/audited">Clear</a></form>
<div class="muted small" style="margin-bottom:8px">Showing {len(rows_data)} of {len(everything)}</div>{table}'''
    return layout("Audited", body, "audited", message)


# ---------------------------------------------------------------- not listed

def _lead_key(name: str) -> str:
    return hashlib.sha1(re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip().encode("utf-8")).hexdigest()[:12]


def _dismissed() -> set:
    try:
        return set(json.loads((FINDER_DIR / "dismissed.json").read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_dismissed(keys: set) -> None:
    FINDER_DIR.mkdir(parents=True, exist_ok=True)
    (FINDER_DIR / "dismissed.json").write_text(json.dumps(sorted(keys)), encoding="utf-8")


def finder_leads() -> list[dict]:
    """Leads from every finder run that are not dismissed and not already in the CRM."""
    try:
        import find_unlisted as finder
    except Exception:
        return []
    crm = finder.load_crm(db_path(), tenant_id())
    skip = _dismissed()
    seen, out = set(), []
    try:
        files = sorted(FINDER_DIR.glob("finder-*.json"), reverse=True)
    except Exception:
        files = []
    for path in files:
        try:
            leads = json.loads(path.read_text(encoding="utf-8")).get("leads", [])
        except Exception:
            continue
        for lead in leads:
            key = _lead_key(lead.get("business_name"))
            if key in seen or key in skip:
                continue
            seen.add(key)
            urls = [u.strip() for u in str(lead.get("listing_url", "")).split("|") if u.strip()]
            probe = dict(lead)
            probe["listing_url"] = next((u for u in urls if finder.source_type(u) == "facebook"), urls[0] if urls else "")
            probe["source_type"] = "facebook" if any(finder.source_type(u) == "facebook" for u in urls) else "directory"
            if finder.already_in_crm(probe, crm):
                continue
            out.append({**lead, "key": key, "urls": urls})
    return out


def _source_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host.endswith("facebook.com"):
        return "Facebook"
    return host.split(".")[0].capitalize() if host else "Link"


def not_listed_page(message: str = "") -> bytes:
    job = dict(FINDER_JOB)
    refresh = 4 if job["state"] == "running" else 0
    if job["state"] == "running":
        lines = "<br>".join(h(x) for x in job["lines"][-8:])
        job_html = (f'<div class="banner"><span class="job"><span class="dot"></span><span>Searching {h(job["town"])}. This takes a couple of minutes and the page refreshes by itself.</span></span></div>'
                    f'<div class="log">{lines}</div>')
    elif job["state"] == "done":
        job_html = f'<div class="banner">{h(job["summary"])}</div>'
    elif job["state"] == "error":
        job_html = f'<div class="banner bad">The search stopped: {h(job["error"])}</div>'
    else:
        job_html = ""

    leads = finder_leads()
    rows = []
    for lead in leads:
        srcs = "".join(f'<a class="pill {"info" if _source_label(u) == "Facebook" else ""}" target="_blank" rel="noreferrer" href="{h(u)}">{h(_source_label(u))}</a>' for u in lead["urls"])
        conf = lead.get("confidence", "low")
        cls = {"high": "ok", "medium": "info"}.get(conf, "")
        rows.append(
            f'<tr><td><input type="checkbox" name="pick" value="{h(lead["key"])}" aria-label="Select {h(lead.get("business_name"))}"></td>'
            f'<td><div class="biz">{avatar(lead.get("business_name", ""))}<div><b>{h(lead.get("business_name"))}</b>'
            f'<div class="muted small">{h(str(lead.get("snippet", ""))[:110])}</div></div></div></td>'
            f'<td>{h(lead.get("trade"))}</td><td><div class="srcs">{srcs}</div></td>'
            f'<td>{h(lead.get("phone")) or "<span class=muted>None found</span>"}</td><td><span class="pill {cls}">{h(conf.title())} match</span></td></tr>')
    if rows:
        review = (f'<form method="post" action="/finder/add"><div class="actions" style="margin-bottom:10px">'
                  f'<button class="btn primary" type="submit">Add selected to CRM</button>'
                  f'<button class="btn" type="submit" formaction="/finder/dismiss" onclick="return confirm(\'Dismiss the selected leads? They will stop showing here.\');">Dismiss selected</button></div>'
                  f'<div class="tbl-wrap"><table><thead><tr><th><input type="checkbox" data-all aria-label="Select all"></th><th>Business</th><th>Trade</th><th>Found on</th><th>Phone</th><th>Match</th></tr></thead>'
                  f'<tbody>{"".join(rows)}</tbody></table></div></form>')
    else:
        review = '<div class="empty">Nothing to review yet. Enter a town above to search Facebook and business directories.</div>'

    body = f'''<div class="head"><div><h1>Not listed</h1>
<p>Businesses that show up on Facebook or in directories but may have no Google Maps listing. They are often the easiest to help.</p></div></div>
{job_html}
<section class="panel"><h2>Find businesses</h2>
<p class="note">This searches the web for Facebook pages and directory entries. It does not use your SerpApi searches. Leads you add start with outreach off and details not verified.</p>
<form class="filters" method="post" action="/finder/run" onsubmit="return confirm('Search for businesses in this town? It takes a couple of minutes and uses none of your SerpApi searches.');">
<input name="town" required placeholder="Town, for example Port Alfred" aria-label="Town"><input name="trades" placeholder="Trades, optional: plumber, electrician" aria-label="Trades">
<button class="btn dark" type="submit"{" disabled" if job["state"] == "running" else ""}>Find businesses</button></form></section>
<section class="panel"><div class="panel-head"><h2>To review</h2><span>{len(leads)} lead{"s" if len(leads) != 1 else ""}</span></div>
<p class="note">Open each link and check the page is a real, active business in your area before you add it. Direct marketing is covered by POPIA, so offer an opt-out when you make contact.</p>{review}</section>'''
    return layout("Not listed", body, "notlisted", message, refresh)


def _finder_worker(town: str, trades: list[str]) -> None:
    job = FINDER_JOB
    def progress(msg):
        job["lines"].append(str(msg))
        del job["lines"][:-40]
    try:
        import find_unlisted as finder
        res = finder.run_finder(town, trades, db=db_path(), tenant=tenant_id(), progress=progress)
        job["summary"] = f"Search finished for {town}. {len(res['leads'])} new to review, {res['skipped']} already in your CRM."
        if res["errors"] and not res["leads"]:
            job["state"], job["error"] = "error", res["errors"][0][:240]
        else:
            job["state"] = "done"
    except Exception as exc:
        job["state"], job["error"] = "error", f"{type(exc).__name__}: {exc}"


def start_finder(town: str, trades: list[str]) -> str:
    with FINDER_LOCK:
        if FINDER_JOB["state"] == "running":
            return "A search is already running. Wait for it to finish."
        FINDER_JOB.update({"state": "running", "town": town, "lines": [], "summary": "", "error": ""})
    threading.Thread(target=_finder_worker, args=(town, trades), daemon=True, name="finder").start()
    return f"Search started for {town}."


def lead_to_payload(lead: dict) -> dict:
    try:
        import find_unlisted as finder
        has_fb = any(finder.source_type(u) == "facebook" for u in lead["urls"])
    except Exception:
        has_fb = any("facebook.com" in u for u in lead["urls"])
    return {"town": lead.get("town", ""), "leads": [{
        "business_name": lead.get("business_name", ""), "sector": lead.get("trade", ""), "categories": [],
        "website_status": "no_website_surfaced_in_search",
        "website_detail": "social_page_only" if has_fb else "directory_page_only", "website_url": "",
        "directory_or_profile_sources": lead["urls"], "phone": lead.get("phone", ""), "address": "",
        "rating": None, "review_count": None, "latitude": None, "longitude": None, "place_id": "",
        "towns_found": [lead.get("town", "")], "found_via": FINDER_SOURCE}]}


# ---------------------------------------------------------------- other pages

def sweep_plan_page(town: str, max_searches: int, server) -> bytes:
    from town_sweep import DEFAULT_TRADES, cache_get
    plan = []
    for trade in DEFAULT_TRADES:
        cached = cache_get(f"{trade}|{town}|0".lower()) is not None
        plan.append({"trade": trade, "cached": cached, "quota_units": 0 if cached else 1})
    units = sum(x["quota_units"] for x in plan)
    planned = min(units, max_searches)
    token = uuid.uuid4().hex
    server.sweep_plans[token] = {"town": town, "max_searches": max_searches, "planned_units": planned}
    trs = "".join(f'<tr><td>{h(x["trade"])}</td><td>{"Saved, free" if x["cached"] else "New search"}</td><td>{x["quota_units"]}</td></tr>' for x in plan)
    body = f'''<div class="head"><div><h1>Plan for {h(town)}</h1><p>No searches have been made. About {planned} of your monthly SerpApi searches would be used.</p></div></div>
<section class="panel"><div class="tbl-wrap"><table><thead><tr><th>Trade</th><th>Source</th><th>Searches</th></tr></thead><tbody>{trs}</tbody></table></div>
<form method="post" action="/sweep/run" class="actions" style="margin-top:14px"><input type="hidden" name="token" value="{token}">
<button class="btn primary" type="submit">Confirm and run {planned} search{"es" if planned != 1 else ""}</button><a class="btn" href="/">Cancel</a></form></section>'''
    return layout("Plan search", body, "leads")


def suppression_page(message: str = "") -> bytes:
    tenant = tenant_id()
    with tenant_context(tenant):
        with connect(db_path()) as conn:
            rows = conn.execute("select identity_type,reason,created_at from suppression_list where tenant_id=? order by created_at desc", (tenant,)).fetchall()
    listing = "".join(f'<tr><td>{h(r["identity_type"])}</td><td>{h(r["reason"]) or "<span class=muted>No reason saved</span>"}</td><td>{h(r["created_at"])}</td></tr>' for r in rows)
    body = f'''<div class="head"><div><h1>Do not contact</h1><p>Anyone added here is skipped when leads are imported. Add an email, a phone number or a Google place ID.</p></div></div>
<section class="panel"><form class="filters" method="post" action="/suppression/add"><select name="kind" aria-label="Type"><option value="email">Email</option><option value="phone">Phone</option><option value="place_id">Place ID</option></select>
<input name="value" required placeholder="Email, phone or place ID" aria-label="Value"><input name="reason" placeholder="Reason, optional" aria-label="Reason"><button class="btn dark" type="submit">Add to list</button></form></section>
<div class="tbl-wrap"><table style="min-width:0"><thead><tr><th>Type</th><th>Reason</th><th>Added</th></tr></thead><tbody>{listing or '<tr><td colspan="3"><div class="empty">Nobody is on the list yet.</div></td></tr>'}</tbody></table></div>'''
    return layout("Do not contact", body, "suppression", message)


# ---------------------------------------------------------------- server

def _redirect(self, location: str):
    self.send_response(303)
    self.send_header("Location", location)
    self.end_headers()


def _read_form(self) -> dict:
    size = min(int(self.headers.get("Content-Length", "0") or 0), 300_000)
    return parse_qs(self.rfile.read(size).decode("utf-8", "replace"), keep_blank_values=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "StraightUpCRM/3.0"

    def log_message(self, fmt, *args):
        print("CRM:", fmt % args)

    def send_body(self, status, body, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        params = {k: v[-1] for k, v in q.items()}
        path = parsed.path
        if path == "/":
            return self.send_body(200, main_page(params, params.get("message", "")))
        if path == "/health":
            return self.send_body(200, b'{"status":"ok"}', "application/json; charset=utf-8")
        if path == "/audited":
            return self.send_body(200, audited_page(params, params.get("message", "")))
        if path in ("/queue", "/finance", "/projects", "/insights", "/bulk", "/privacy"):
            import crm_pages
            pages = {"/queue": crm_pages.audit_queue_page, "/finance": crm_pages.finance_page,
                     "/projects": crm_pages.projects_page, "/insights": crm_pages.insights_page,
                     "/bulk": crm_pages.bulk_page, "/privacy": crm_pages.compliance_page}
            return self.send_body(200, pages[path](params.get("message", "")))
        if path == "/not-listed":
            return self.send_body(200, not_listed_page(params.get("message", "")))
        if path == "/job-status":
            from audit_queue import job_status
            job = job_status(params.get("job_id", ""), tenant_id(), db_path())
            if not job:
                return self.send_body(404, b'{"error":"job not found"}', "application/json; charset=utf-8")
            return self.send_body(200, json.dumps(job).encode("utf-8"), "application/json; charset=utf-8")
        if path == "/sweep/plan":
            town = params.get("town", "").strip()
            if not town:
                return self.send_body(400, b"Town required", "text/plain; charset=utf-8")
            try:
                max_searches = max(1, min(50, int(params.get("max_searches", "12"))))
            except ValueError:
                max_searches = 12
            return self.send_body(200, sweep_plan_page(town, max_searches, self.server))
        if path == "/suppression":
            return self.send_body(200, suppression_page(params.get("message", "")))
        if path == "/brand-logo":
            logo = Path(config().get("crm_logo_path") or str(LOCKUP))
            if not logo.is_absolute():
                logo = PROJECT_DIR / logo
            if not logo.is_file():
                logo = LOCKUP
            if logo.is_file():
                return self.send_body(200, logo.read_bytes(), "image/png")
            return self.send_body(404, b"Logo not found", "text/plain; charset=utf-8")
        if path == "/brand-favicon":
            favicon = BRAND_DIR / "favicon-256.png"
            if favicon.is_file():
                return self.send_body(200, favicon.read_bytes(), "image/png")
            return self.send_body(404, b"Favicon not found", "text/plain; charset=utf-8")
        if path.startswith("/brand-font/"):
            font = BRAND_FONT_FILES.get(path.rsplit("/", 1)[-1].lower())
            if font and font.is_file():
                return self.send_body(200, font.read_bytes(), "font/ttf")
            return self.send_body(404, b"Font not found", "text/plain; charset=utf-8")
        if path.startswith("/contact/"):
            try:
                body, _refresh = profile_page(int(path.rsplit("/", 1)[1]), params.get("message", ""))
                return self.send_body(200 if body != b"Customer not found" else 404, body)
            except ValueError:
                return self.send_body(400, b"Invalid contact id", "text/plain; charset=utf-8")
        if path.startswith("/pdf/"):
            try:
                hid = int(path.rsplit("/", 1)[1])
                tenant = tenant_id()
                with connect(db_path()) as conn:
                    row = conn.execute(
                        """SELECT h.pdf_path
                           FROM audit_history AS h
                           JOIN contacts AS c ON c.id = h.contact_id
                           WHERE h.id = ? AND h.tenant_id = ? AND c.tenant_id = ?""",
                        (hid, tenant, tenant),
                    ).fetchone()
                pdf = Path(row["pdf_path"]).resolve() if row else None
                reports = (PROJECT_DIR / "reports").resolve()
                if pdf and pdf.is_relative_to(reports) and pdf.is_file():
                    return self.send_body(200, pdf.read_bytes(), "application/pdf")
                return self.send_body(404, b"PDF not found", "text/plain; charset=utf-8")
            except (ValueError, OSError):
                return self.send_body(400, b"Invalid report id", "text/plain; charset=utf-8")
        if path.startswith("/invoice/") and path.endswith("/pdf"):
            try:
                invoice_id = int(path.strip("/").split("/")[1])
                from crm_ops import create_invoice_pdf
                pdf = create_invoice_pdf(invoice_id, db_path=db_path(), tenant_id=tenant_id())
                if pdf.is_file() and pdf.is_relative_to((PROJECT_DIR / "reports").resolve()):
                    return self.send_body(200, pdf.read_bytes(), "application/pdf")
                return self.send_body(404, b"Invoice not found", "text/plain; charset=utf-8")
            except (ValueError, LookupError):
                return self.send_body(404, b"Invoice not found", "text/plain; charset=utf-8")
            except Exception as exc:
                print("Invoice PDF generation failed:", type(exc).__name__, str(exc))
                return self.send_body(500, b"Invoice PDF could not be generated", "text/plain; charset=utf-8")
        if path.startswith("/monthly/"):
            try:
                _, _, cid, month = path.split("/", 3)
                from crm_ops import create_monthly_report_pdf
                pdf = create_monthly_report_pdf(int(cid), month, db_path=db_path(), tenant_id=tenant_id())
                if pdf.is_file() and pdf.is_relative_to((PROJECT_DIR / "reports").resolve()):
                    return self.send_body(200, pdf.read_bytes(), "application/pdf")
                return self.send_body(404, b"Report not found", "text/plain; charset=utf-8")
            except (ValueError, LookupError):
                return self.send_body(404, b"Monthly report not available for that period", "text/plain; charset=utf-8")
        if path == "/export.csv":
            rows = list_contacts(db_path(), website_status=params.get("website_status", ""), town=params.get("town", ""),
                                 website_detail=params.get("website_detail", ""), stage=params.get("stage", ""),
                                 outreach_allowed=int(params["outreach_allowed"]) if params.get("outreach_allowed") in ("0", "1") else None,
                                 tenant_id=tenant_id(), limit=5000)
            buff = io.StringIO(newline="")
            fields = list(rows[0].keys()) if rows else ["id", "business_name", "phone", "address", "town", "website_url", "website_detail"]
            w = csv.DictWriter(buff, fieldnames=fields)
            w.writeheader()
            for row in rows:
                w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in row.items()})
            return self.send_body(200, buff.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
        return self.send_body(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_body(403, b"Local only", "text/plain; charset=utf-8")
        origin = self.headers.get("Origin")
        if origin and origin != "null" and (urlparse(origin).hostname or "") not in ("127.0.0.1", "localhost", "::1"):
            return self.send_body(403, b"Blocked: this request came from another website", "text/plain; charset=utf-8")
        try:
            body_size = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            return self.send_body(400, b"Invalid request size", "text/plain; charset=utf-8")
        if body_size < 0 or body_size > 300_000:
            return self.send_body(413, b"Request too large", "text/plain; charset=utf-8")
        form = _read_form(self)
        data = {k: v[-1].strip() for k, v in form.items()}
        path = urlparse(self.path).path
        tenant = tenant_id()

        if path.startswith("/publish-report/"):
            origin = self.headers.get("Origin", "")
            parsed_origin = urlparse(origin)
            request_host = (self.headers.get("Host", "") or "").lower()
            if (not origin or origin == "null" or parsed_origin.scheme != "http"
                    or parsed_origin.netloc.lower() != request_host
                    or parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1")):
                return self.send_body(403, b"Report publishing requires a same-origin local CRM request", "text/plain; charset=utf-8")
            try:
                history_id = int(path.rsplit("/", 1)[1])
                if data.get("reviewed") != "yes":
                    raise ValueError("Review and confirm the customer version before publishing")
                with connect(db_path()) as conn:
                    row = conn.execute(
                        "SELECT contact_id,full_audit_json FROM audit_history WHERE id=? AND tenant_id=?",
                        (history_id, tenant),
                    ).fetchone()
                if not row:
                    return self.send_body(404, b"Audit history not found", "text/plain; charset=utf-8")
                from customer_report import to_customer_report
                from customer_report_publisher import publish_customer_report
                client_report = to_customer_report(json.loads(row["full_audit_json"] or "{}"))
                published = publish_customer_report(client_report)
                from crm import save_client_report_url
                save_client_report_url(history_id, published["url"], db_path(), tenant)
                return _redirect(self, f"/contact/{int(row['contact_id'])}?message=Customer+report+published+and+ready+to+share")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return _redirect(self, f"/contact/{history_id if 'history_id' in locals() else 0}?message=" + quote(str(exc)[:180]))
            except Exception as exc:
                message = str(exc) if isinstance(exc, RuntimeError) else "Report publishing failed. Check the local token and Netlify configuration."
                return _redirect(self, f"/contact/{history_id if 'history_id' in locals() else 0}?message=" + quote(message[:180]))

        if path.startswith("/scope/"):
            try:
                parts = path.strip("/").split("/")
                hid, item_id = int(parts[1]), parts[2]
                update_scope_item(hid, item_id, data.get("done") == "1", db_path())
                with connect(db_path()) as conn:
                    row = conn.execute("SELECT contact_id FROM audit_history WHERE id=?", (hid,)).fetchone()
                if not row:
                    return self.send_body(404, b"Audit history missing", "text/plain; charset=utf-8")
                return _redirect(self, f"/contact/{row['contact_id']}?message=Scope+item+saved")
            except Exception as exc:
                return self.send_body(400, h(exc).encode(), "text/plain; charset=utf-8")

        if path == "/suppression/add":
            try:
                kind = data.get("kind", "").lower()
                value = data.get("value", "")
                if kind not in ("email", "phone", "place_id") or not value:
                    return self.send_body(400, b"Valid suppression type and value required", "text/plain; charset=utf-8")
                with tenant_context(env("CRM_TENANT_ID")):
                    with connect(db_path()) as conn:
                        add_suppression(conn, kind, value, data.get("reason", ""))
                return _redirect(self, "/suppression?message=Added+to+the+do+not+contact+list")
            except Exception as exc:
                return self.send_body(400, h(exc).encode(), "text/plain; charset=utf-8")

        if path == "/sweep/run":
            plan = self.server.sweep_plans.pop(data.get("token", ""), None)
            if not plan:
                return self.send_body(400, b"Search confirmation expired or missing", "text/plain; charset=utf-8")
            job_ids = []
            try:
                from town_sweep import DEFAULT_TRADES, run_sweep
                from sweep_queue import enqueue_trades, update_job
                with tenant_context(tenant):
                    with connect(db_path()) as conn:
                        job_ids = enqueue_trades(conn, tenant, plan["town"], DEFAULT_TRADES, plan)
                    result = run_sweep(plan["town"], DEFAULT_TRADES, plan["max_searches"], 1, False, False, progress=print)
                    from sweep_compliance import import_sweep_payload
                    imported = import_sweep_payload(result, db_path(), tenant)
                    for jid in job_ids:
                        with connect(db_path()) as conn:
                            update_job(conn, jid, "completed", result_path=result.get("paths", {}).get("json_path", ""), tenant_id=tenant)
                return _redirect(self, "/?message=" + quote(f"Search complete: {imported.get('processed', 0)} imported, {imported.get('suppression_skipped', 0)} skipped as do not contact"))
            except BaseException as exc:
                print("Confirmed city sweep failed:", type(exc).__name__, str(exc))
                for jid in job_ids:
                    try:
                        from sweep_queue import update_job
                        with connect(db_path()) as conn:
                            update_job(conn, jid, "failed", f"{type(exc).__name__}: {exc}", tenant_id=tenant)
                    except Exception:
                        pass
                if isinstance(exc, (KeyboardInterrupt,)):
                    raise
                return _redirect(self, "/?message=" + quote(f"Search failed: {exc}"[:200]))

        if path == "/finder/run":
            town = data.get("town", "")
            if not town:
                return _redirect(self, "/not-listed?message=Enter+a+town+first")
            trades = [t.strip() for t in data.get("trades", "").split(",") if t.strip()]
            return _redirect(self, "/not-listed?message=" + quote(start_finder(town, trades)))

        if path in ("/finder/add", "/finder/dismiss"):
            picks = [k for k in form.get("pick", []) if k]
            if not picks:
                return _redirect(self, "/not-listed?message=Select+at+least+one+lead+first")
            available = {x["key"]: x for x in finder_leads()}
            if path == "/finder/dismiss":
                _save_dismissed(_dismissed() | {k for k in picks if k in available})
                return _redirect(self, "/not-listed?message=" + quote(f"Dismissed {len([k for k in picks if k in available])} lead(s)"))
            added = skipped = failed = 0
            last_error = ""
            for key in picks:
                lead = available.get(key)
                if not lead:
                    continue
                try:
                    from sweep_compliance import import_sweep_payload
                    with tenant_context(tenant):
                        res = import_sweep_payload(lead_to_payload(lead), db_path(), tenant)
                    if res.get("processed", 0):
                        added += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    failed += 1
                    last_error = f"{type(exc).__name__}: {exc}"
                    print("Add from Not listed failed:", last_error)
            msg = f"Added {added} to the CRM."
            if skipped:
                msg += f" {skipped} skipped (do not contact list or already saved)."
            if failed:
                msg += f" {failed} failed: {last_error[:120]}"
            return _redirect(self, "/not-listed?message=" + quote(msg))

        if path.startswith("/rank/"):
            try:
                cid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_body(400, b"Invalid contact id", "text/plain; charset=utf-8")
            if not get_contact(cid, db_path(), tenant):
                return self.send_body(404, b"Contact not found", "text/plain; charset=utf-8")
            if not RANK_LOCK.acquire(blocking=False):
                return _redirect(self, f"/contact/{cid}?message=A+ranking+check+is+already+running")
            try:
                import rank_check as rc
                business = (rc.load_contacts([cid], db_path(), tenant) or [None])[0]
                if not business:
                    return _redirect(self, f"/contact/{cid}?message=Contact+not+found")
                source = "ddgs" if data.get("source") == "ddgs" else "serpapi"
                keywords = [k.strip() for k in data.get("keywords", "").split(";") if k.strip()][:5]
                result = rc.run_for_business(business, keywords or None, source=source)
                ranked = sum(1 for r in result["results"] if r["position"])
                msg = f"Ranking check done. Showed up for {ranked} of {len(result['results'])} searches."
                if result["errors"]:
                    msg += f" Some searches failed: {result['errors'][0][:100]}"
                return _redirect(self, f"/contact/{cid}?message=" + quote(msg))
            except Exception as exc:
                return _redirect(self, f"/contact/{cid}?message=" + quote(f"Ranking check failed: {exc}"[:220]))
            finally:
                RANK_LOCK.release()

        if path == "/profile/save":
            try:
                cid = int(data.get("id", "0"))
                current = get_contact(cid, db_path(), tenant)
                if not current:
                    return self.send_body(404, b"Contact not found", "text/plain; charset=utf-8")
                socials, seen = [], set()
                for raw in [data.get(f"social_{k}", "") for k, _l, _d in PLATFORMS] + data.get("social_other", "").splitlines():
                    u = safe_url(raw)
                    if u and u.lower().rstrip("/") not in seen:
                        seen.add(u.lower().rstrip("/"))
                        socials.append(u)
                data["social_links"] = socials
                data["categories"] = [x.strip() for x in data.get("categories", "").split(",") if x.strip()]
                save_contact_fields(cid, data, db_path(), tenant)
                return _redirect(self, f"/contact/{cid}?message=Profile+saved")
            except Exception as exc:
                return self.send_body(400, h(exc).encode(), "text/plain; charset=utf-8")

        if path.startswith("/audit/"):
            try:
                cid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_body(400, b"Invalid contact id", "text/plain; charset=utf-8")
            try:
                c = get_contact(cid, db_path(), tenant)
                if not c:
                    return self.send_body(404, b"Contact not found", "text/plain; charset=utf-8")
                if not c.get("details_verified"):
                    return _redirect(self, f"/contact/{cid}?message=Verify+business+details+before+audit")
                if c.get("website_detail") != "own_website_listed" or not c.get("website_url"):
                    return _redirect(self, f"/contact/{cid}?message=Official+website+required")
                from audit_queue import enqueue_audit
                job_id = enqueue_audit(cid, tenant, db_path())
                return _redirect(self, f"/contact/{cid}?message=Audit+queued+job+{job_id}")
            except Exception as exc:
                return _redirect(self, f"/contact/{cid}?message={quote(type(exc).__name__ + ': ' + str(exc))}")

        if path == "/activity/add":
            try:
                from crm_ops import log_activity
                cid = int(data.get("contact_id", "0"))
                log_activity(cid, data.get("event_type", "call"), data.get("summary", ""), data.get("outcome", ""),
                             data.get("channel", ""), data.get("direction", "outbound"), db_path(), tenant)
                return _redirect(self, f"/contact/{cid}?message=Contact+activity+logged")
            except Exception as exc:
                cid = int(data.get("contact_id", "0") or 0)
                return _redirect(self, f"/contact/{cid}?message=" + quote(f"Could not log activity: {exc}"[:160]))

        if path == "/quote/create":
            try:
                from crm_ops import create_quote
                cid = int(data.get("contact_id", "0"))
                create_quote(cid, data.get("description", ""), float(data.get("amount", "0")), due_at=data.get("due_at", ""), db_path=db_path(), tenant_id=tenant)
                return _redirect(self, f"/contact/{cid}?message=Quote+recorded")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not create quote: {exc}"[:160]))

        if path == "/quote/status":
            try:
                from crm_ops import set_quote_status
                set_quote_status(int(data.get("quote_id", "0")), data.get("status", ""), db_path(), tenant)
                with connect(db_path()) as conn:
                    row = conn.execute("SELECT contact_id FROM crm_quotes WHERE id=? AND tenant_id=?", (int(data["quote_id"]), tenant)).fetchone()
                return _redirect(self, f"/contact/{row['contact_id']}?message=Quote+status+saved")
            except Exception as exc:
                return _redirect(self, "/finance?message=" + quote(f"Could not update quote: {exc}"[:160]))

        if path == "/invoice/create":
            try:
                from crm_ops import create_invoice, create_invoice_pdf
                cid = int(data.get("contact_id", "0"))
                iid = create_invoice(cid, data.get("description", ""), float(data.get("amount", "0")), data.get("due_on", ""), db_path=db_path(), tenant_id=tenant)
                create_invoice_pdf(iid, db_path=db_path(), tenant_id=tenant)
                return _redirect(self, f"/contact/{cid}?message=Branded+invoice+created+-+see+Sales+and+billing")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not create invoice: {exc}"[:160]))

        if path == "/payment/add":
            try:
                from crm_ops import record_payment
                cid = int(data.get("contact_id", "0"))
                invoice_id = int(data["invoice_id"]) if data.get("invoice_id", "").isdigit() else None
                record_payment(cid, float(data.get("amount", "0")), data.get("paid_on", ""), invoice_id,
                               "manual", data.get("reference", ""), db_path=db_path(), tenant_id=tenant)
                return _redirect(self, f"/contact/{cid}?message=Payment+recorded+manually")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not record payment: {exc}"[:160]))

        if path == "/recurring/save":
            try:
                from crm_ops import save_recurring_item
                cid = int(data.get("contact_id", "0"))
                save_recurring_item(cid, data.get("item_type", "other"), data.get("description", ""), float(data.get("amount", "0")),
                                    data.get("frequency", "monthly"), data.get("next_due_on", ""), db_path=db_path(), tenant_id=tenant)
                return _redirect(self, f"/contact/{cid}?message=Recurring+item+saved")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not save recurring item: {exc}"[:160]))

        if path == "/recurring/paid":
            try:
                from crm_ops import mark_recurring_paid
                mark_recurring_paid(int(data.get("item_id", "0")), date.today().isoformat(), db_path(), tenant)
                return _redirect(self, "/finance?message=Payment+marked+paid+and+next+due+date+advanced")
            except Exception as exc:
                return _redirect(self, "/finance?message=" + quote(f"Could not mark recurring item paid: {exc}"[:160]))

        if path == "/project/save":
            try:
                from crm_ops import save_project
                cid = int(data.get("contact_id", "0"))
                save_project(cid, data, db_path(), tenant)
                return _redirect(self, f"/contact/{cid}?message=Project+status+saved")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not save project: {exc}"[:160]))

        if path == "/audit/retry":
            try:
                from audit_queue import enqueue_audit
                cid = int(data.get("contact_id", "0"))
                job = enqueue_audit(cid, tenant, db_path())
                return _redirect(self, f"/queue?message=Retry+queued+as+{job}")
            except Exception as exc:
                return _redirect(self, "/queue?message=" + quote(f"Retry not queued: {exc}"[:160]))

        if path == "/audit/town":
            if data.get("confirm") != "yes":
                return _redirect(self, "/queue?message=Confirm+the+town+batch+before+queuing")
            try:
                limit = max(1, min(25, int(data.get("limit", "10"))))
                from crm import list_contacts
                from audit_queue import enqueue_audit
                eligible = [c for c in list_contacts(db_path(), town=data.get("town", ""), tenant_id=tenant, limit=500)
                            if c.get("details_verified") and c.get("website_detail") == "own_website_listed" and c.get("website_url")]
                queued = failures = 0
                for c in eligible[:limit]:
                    try:
                        enqueue_audit(c["id"], tenant, db_path())
                        queued += 1
                    except Exception:
                        failures += 1
                return _redirect(self, "/queue?message=" + quote(f"Queued {queued} audit(s); {failures} were skipped. Worker cost limits remain active."))
            except Exception as exc:
                return _redirect(self, "/queue?message=" + quote(f"Town queue failed: {exc}"[:160]))

        if path == "/bulk/stage":
            if data.get("confirm") != "yes":
                return _redirect(self, "/bulk?message=Confirm+the+bulk+change")
            try:
                from crm_ops import bulk_update_stage
                ids = [x for x in form.get("contact_id", []) if x.isdigit()]
                changed = bulk_update_stage(ids, data.get("stage", ""), db_path(), tenant)
                return _redirect(self, "/bulk?message=" + quote(f"Updated {changed} lead(s)."))
            except Exception as exc:
                return _redirect(self, "/bulk?message=" + quote(f"Bulk stage change failed: {exc}"[:160]))

        if path == "/bulk/csv-preview":
            try:
                from crm_csv import preview_csv
                import crm_pages
                rows = preview_csv(data.get("csv_text", ""), db_path(), tenant)
                if not rows:
                    raise ValueError("No usable business-name rows found")
                now = time.time()
                for stale in [key for key, value in self.server.csv_imports.items() if now - value.get("created", 0) > 900]:
                    self.server.csv_imports.pop(stale, None)
                while len(self.server.csv_imports) >= 50:
                    oldest = min(self.server.csv_imports, key=lambda key: self.server.csv_imports[key].get("created", 0))
                    self.server.csv_imports.pop(oldest, None)
                token = uuid.uuid4().hex
                self.server.csv_imports[token] = {"rows": rows, "created": now}
                return self.send_body(200, crm_pages.csv_preview_page(rows, token))
            except Exception as exc:
                return _redirect(self, "/bulk?message=" + quote(f"CSV preview failed: {exc}"[:160]))

        if path == "/bulk/csv-commit":
            if data.get("confirm") != "yes":
                return _redirect(self, "/bulk?message=Review+and+confirm+every+CSV+row")
            record = self.server.csv_imports.pop(data.get("token", ""), None)
            if not record or time.time() - record.get("created", 0) > 900:
                return _redirect(self, "/bulk?message=CSV+preview+expired;+preview+the+file+again")
            try:
                from crm_csv import commit_csv
                actions = {str(r["row"]): data.get(f"action_{r['row']}", "skip") for r in record["rows"]}
                result = commit_csv(record["rows"], actions, db_path(), tenant)
                return _redirect(self, "/bulk?message=" + quote(f"CSV import: {result['added']} added, {result['merged']} filled, {result['skipped']} skipped."))
            except Exception as exc:
                return _redirect(self, "/bulk?message=" + quote(f"CSV import failed: {exc}"[:160]))

        if path == "/website/sync":
            try:
                from netlify_crm_sync import sync_website_enquiries
                result = sync_website_enquiries(db_path(), tenant)
                message = f"Website enquiry sync: {result['imported']} imported/verified, {result['acknowledged']} acknowledged, {result['failed']} held for retry."
                return _redirect(self, "/bulk?message=" + quote(message))
            except Exception as exc:
                return _redirect(self, "/bulk?message=" + quote(f"Website enquiry sync not completed: {exc}"[:180]))

        if path == "/backup/create":
            try:
                from crm_ops import create_database_backup
                out = create_database_backup(db_path())
                return _redirect(self, "/privacy?message=" + quote(f"Verified private backup created: {out.name}"))
            except Exception as exc:
                return _redirect(self, "/privacy?message=" + quote(f"Backup failed: {exc}"[:160]))

        if path == "/privacy/opt-out":
            try:
                from crm_ops import record_opt_out
                cid = int(data.get("contact_id", "0"))
                record_opt_out(cid, data.get("reason", "Client requested no further contact"), db_path(), tenant)
                return _redirect(self, f"/contact/{cid}?message=Opt-out+recorded;+contact+suppressed+from+future+imports")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Could not record opt-out: {exc}"[:160]))

        if path == "/privacy/erase":
            try:
                from crm_ops import erase_contact
                cid = int(data.get("contact_id", "0"))
                erase_contact(cid, data.get("confirmation", ""), db_path(), tenant)
                return _redirect(self, "/privacy?message=CRM+record+and+saved+reports+erased;+hashed+do-not-contact+markers+retained")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Erasure not completed: {exc}"[:160]))

        if path == "/monthly/report":
            try:
                cid = int(data.get("contact_id", "0"))
                month = data.get("month", "")
                from crm_ops import create_monthly_report_pdf
                pdf = create_monthly_report_pdf(cid, month, db_path=db_path(), tenant_id=tenant)
                return _redirect(self, f"/monthly/{cid}/{quote(month)}")
            except Exception as exc:
                return _redirect(self, f"/contact/{int(data.get('contact_id','0') or 0)}?message=" + quote(f"Monthly report not created: {exc}"[:160]))

        return self.send_body(404, b"Not found", "text/plain; charset=utf-8")


def main():
    load_env()
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("The private CRM is intentionally restricted to localhost; do not expose it through a tunnel or network interface")
    tenant = tenant_id()
    migrate(db_path(), tenant)
    with tenant_context(tenant):
        with connect(db_path()):
            pass
    from audit_queue import recover_queued
    recovered = recover_queued(tenant, db_path())
    print(f"Recovered queued audit jobs: {recovered}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.sweep_plans = {}
    server.csv_imports = {}
    print(f"Straight Up Digital CRM at http://{HOST}:{PORT}/")
    print(f"SQLite: {DEFAULT_DB}")
    server.serve_forever()


if __name__ == "__main__":
    main()
