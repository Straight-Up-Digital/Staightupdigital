"""Extra pages for the local CRM: editable message templates, duplicate finder, optional password login."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote, urlparse

TEMPLATE_FILE = Path(__file__).resolve().parent / "message_templates.json"
TEMPLATE_DEFAULTS = {
    "whatsapp_audit": "{greeting}, it's St. John from Straight Up Digital here in Port Alfred. I had a proper look at {business} online and noticed {finding}.\n\nI put together a short report that shows what to fix first and why it matters for your business. Want me to send it through? No charge, no pressure.",
    "email_audit_subject": "Quick one about {business}'s website",
    "email_audit_body": "{greeting},\n\nSt. John here from Straight Up Digital in Port Alfred. I had a proper look at {business} online and noticed {finding}.\n\nI put the full breakdown in a short PDF: what's working, what to fix first and why it matters for getting you more calls and enquiries. It's attached, no charge.\n\nIf you'd like me to sort the fixes out, just reply here or WhatsApp me on 062 065 3503 and I'll send you a simple quote.\n\nCheers,\nSt. John Ritchie\nStraight Up Digital, Port Alfred\n\nIf you'd rather not hear from me again, reply STOP and I'll remove you.",
    "whatsapp_followup": "{greeting}, St. John again. Just checking you got my message about {business}. Happy to walk you through the report right here on WhatsApp or over a quick call, whatever suits you.",
    "whatsapp_followup2": "{greeting}, last one from me, I promise. If the website stuff isn't a priority right now, no stress at all. If it is, I can get the main fixes done for {business} quickly and keep it simple. Just say the word.",
}
TEMPLATE_LABELS = {
    "whatsapp_audit": "WhatsApp: first message after an audit",
    "email_audit_subject": "Email: subject line",
    "email_audit_body": "Email: message",
    "whatsapp_followup": "WhatsApp: follow-up (about 3 days later)",
    "whatsapp_followup2": "WhatsApp: last follow-up (about a week later)",
}
SOCIAL_HOSTS = ("facebook.", "fb.", "instagram.", "linkedin.", "twitter.", "x.com", "tiktok.", "youtube.", "google.", "wa.me")


def _w():
    import crm_web as w
    return w


# ---------------------------------------------------------------- message templates

def load_templates() -> dict:
    data = dict(TEMPLATE_DEFAULTS)
    try:
        saved = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
        for k in data:
            if isinstance(saved.get(k), str) and saved[k].strip():
                data[k] = saved[k]
    except Exception:
        pass
    return data


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_template(text: str, business: str, finding: str, contact: dict | None = None) -> str:
    contact = contact or {}
    first = str(contact.get("first_name") or "").strip().split(" ")[0].title()
    try:
        return str(text).format_map(_Safe(business=business, finding=finding, first_name=first, town=str(contact.get("town") or ""),
                                          greeting=f"Hey {first}" if first else "Hey there"))
    except Exception:
        return str(text)


def templates_page(message: str = "") -> bytes:
    w = _w()
    cur = load_templates()
    blocks = []
    for key, label in TEMPLATE_LABELS.items():
        rows = 2 if key.endswith("subject") else 5 if key.endswith("body") else 3
        blocks.append(f'<div class="field wide"><label class="lab" for="t_{key}">{w.h(label)}</label>'
                      f'<textarea id="t_{key}" name="{key}" rows="{rows}" maxlength="2000">{w.h(cur[key])}</textarea></div>')
    sample = render_template(cur["whatsapp_audit"], "Sample Guest House", "the page titles are missing on two pages")
    body = f'''<div class="head"><div><h1>Message templates</h1><p>The drafts behind the WhatsApp and email buttons on every lead. Use <b>{{greeting}}</b> (Hey Sarah, or Hey there), <b>{{business}}</b> for the business name, <b>{{first_name}}</b>, <b>{{town}}</b> and <b>{{finding}}</b> for the main thing the audit found, written in plain words. You always review a draft before sending it.</p></div></div>
<section class="panel"><form method="post" action="/templates/save"><div class="grid">{"".join(blocks)}</div>
<div class="actions" style="margin-top:12px"><button class="btn primary" type="submit">Save templates</button></div></form></section>
<section class="panel"><h2>Preview</h2><p class="note">First WhatsApp message with sample details:</p><pre style="white-space:pre-wrap">{w.h(sample)}</pre>
<form method="post" action="/templates/reset" onsubmit="return confirm('Go back to the original templates?');"><button class="btn sm" type="submit">Reset to the originals</button></form></section>'''
    return w.layout("Message templates", body, "templates", message)


# ---------------------------------------------------------------- duplicate finder

def _name_key(name: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return "".join(x for x in t.split() if x not in {"the", "and", "pty", "ltd", "cc", "co", "za"})


def _keys(c: dict) -> set:
    out = set()
    nk = _name_key(c.get("business_name", ""))
    if len(nk) >= 4:
        out.add("n:" + nk)
    ph = re.sub(r"\D", "", c.get("phone") or "")
    if len(ph) >= 9:
        out.add("p:" + ph[-9:])
    host = (urlparse(c.get("website_url") or "").hostname or "").lower().removeprefix("www.")
    if host and not any(s in host for s in SOCIAL_HOSTS):
        out.add("w:" + host)
    return out


def duplicate_groups(contacts: list[dict]) -> list[list[dict]]:
    parent = {c["id"]: c["id"] for c in contacts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen: dict[str, int] = {}
    for c in contacts:
        for k in _keys(c):
            if k in seen:
                parent[find(c["id"])] = find(seen[k])
            else:
                seen[k] = c["id"]
    groups: dict[int, list[dict]] = {}
    for c in contacts:
        groups.setdefault(find(c["id"]), []).append(c)
    return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: -len(g))


def duplicates_page(message: str = "") -> bytes:
    w = _w()
    contacts = [c for c in w.list_contacts(w.db_path(), tenant_id=w.tenant_id(), limit=5000) if c.get("lead_status") != "duplicate"]
    groups = duplicate_groups(contacts)
    panels = []
    for i, g in enumerate(groups[:40]):
        rows = "".join(
            f'<tr><td><input type="radio" name="keep" value="{c["id"]}"{" checked" if j == 0 else ""} aria-label="Keep this one"></td>'
            f'<td><a href="/contact/{c["id"]}">{w.h(c["business_name"])}</a></td><td>{w.h(c.get("town"))}</td><td>{w.h(c.get("phone"))}</td>'
            f'<td>{w.h(c.get("email"))}</td><td>{w.stage_pill(c.get("pipeline_stage"))}</td><td>{w.h(c.get("website_url"))}</td></tr>'
            for j, c in enumerate(sorted(g, key=lambda x: (x.get("pipeline_stage") in ("lost", "new"), x["id"]))))
        ids = "".join(f'<input type="hidden" name="group" value="{c["id"]}">' for c in g)
        panels.append(f'<section class="panel"><form method="post" action="/duplicates/resolve">{ids}'
                      f'<div class="tbl-wrap"><table style="min-width:760px"><thead><tr><th>Keep</th><th>Business</th><th>Town</th><th>Phone</th><th>Email</th><th>Stage</th><th>Website</th></tr></thead><tbody>{rows}</tbody></table></div>'
                      f'<div class="actions" style="margin-top:10px"><label><input type="checkbox" name="confirm" value="yes" required> These are the same business</label>'
                      f'<button class="btn dark" type="submit">Keep the selected one, mark the rest as duplicates</button></div></form></section>')
    more = f'<p class="muted">Showing the first 40 of {len(groups)} groups.</p>' if len(groups) > 40 else ""
    body = f'''<div class="head"><div><h1>Duplicates</h1><p>Leads that share a business name, a phone number or a website. Pick the one to keep. Missing email, phone and social links are copied across, the others are marked Lost and tagged as duplicates. Nothing is deleted.</p></div></div>
{"".join(panels) or '<section class="panel"><div class="empty">No duplicates found.</div></section>'}{more}'''
    return w.layout("Duplicates", body, "dupes", message)


def resolve_duplicates(keep: int, group: list[int]) -> int:
    w = _w()
    tenant = w.tenant_id()
    keeper = w.get_contact(keep, w.db_path(), tenant)
    if not keeper or keep not in group:
        raise ValueError("Choose which lead to keep")
    merged = dict(keeper)
    others = []
    for cid in group:
        if cid == keep:
            continue
        o = w.get_contact(cid, w.db_path(), tenant)
        if not o:
            continue
        others.append(o)
        for f in ("email", "phone", "first_name", "surname", "address", "website_url"):
            if not str(merged.get(f) or "").strip() and str(o.get(f) or "").strip():
                merged[f] = o[f]
        links = list(merged.get("social_links") or [])
        for u in o.get("social_links") or []:
            if u not in links:
                links.append(u)
        merged["social_links"] = links
    w.save_contact_fields(keep, merged, w.db_path(), tenant)
    for o in others:
        d = dict(o)
        d["pipeline_stage"] = "lost"
        d["lead_status"] = "duplicate"
        d["notes"] = (str(o.get("notes") or "") + f"\nDuplicate of lead #{keep} ({keeper['business_name']}).").strip()
        w.save_contact_fields(o["id"], d, w.db_path(), tenant)
    return len(others)


# ---------------- platform checks typed in by hand, plus rankings, for the PDFs

PLATFORMS = [("facebook", "Facebook", "facebook.com"), ("instagram", "Instagram", "instagram.com"), ("linkedin", "LinkedIn", "linkedin.com"),
             ("booking", "Booking.com", "booking.com"), ("airbnb", "Airbnb", "airbnb.com"), ("tripadvisor", "TripAdvisor", "tripadvisor.com"),
             ("trustpilot", "Trustpilot", "trustpilot.com"), ("hellopeter", "HelloPeter", "hellopeter.com")]


def _ensure_platform_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS platform_checks(id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', rating REAL, review_count INTEGER,
        followers INTEGER, last_activity TEXT NOT NULL DEFAULT '', checked_on TEXT NOT NULL DEFAULT '',
        UNIQUE(contact_id, tenant_id, platform))""")


def get_platform_checks(contact_id, db_path=None, tenant_id=None) -> list[dict]:
    w = _w()
    labels = {k: lab for k, lab, _d in PLATFORMS}
    with w.connect(db_path or w.db_path()) as conn:
        _ensure_platform_table(conn)
        rows = conn.execute("SELECT * FROM platform_checks WHERE contact_id=? AND tenant_id=?", (int(contact_id), tenant_id or w.tenant_id())).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = labels.get(d["platform"], d["platform"])
        if any(d.get(k) not in (None, "") for k in ("url", "rating", "review_count", "followers", "last_activity")):
            out.append(d)
    order = {k: i for i, (k, _l, _d) in enumerate(PLATFORMS)}
    return sorted(out, key=lambda d: order.get(d["platform"], 99))


def _num(text, kind=float, high=None):
    raw = str(text or "").strip().lower().replace(" ", "")
    if kind is int:
        raw = raw.replace(",", "").replace(".", "")
        mult = 1000 if raw.endswith("k") else 1
        raw = raw.rstrip("k")
        t = re.sub(r"[^0-9]", "", raw)
        if not t:
            return None
        return int(t) * mult
    t = re.sub(r"[^0-9.]", "", raw.replace(",", "."))
    if not t:
        return None
    try:
        v = kind(float(t))
    except ValueError:
        return None
    return v if v >= 0 and (high is None or v <= high) else None


def save_platform_checks(contact_id: int, data: dict) -> int:
    w = _w()
    tenant = w.tenant_id()
    saved = 0
    with w.connect(w.db_path()) as conn:
        _ensure_platform_table(conn)
        for key, _lab, _dom in PLATFORMS:
            url = (data.get(f"url_{key}") or "").strip()[:300]
            url = url if url.lower().startswith(("http://", "https://")) else ""
            rating = _num(data.get(f"rating_{key}"), float, 10)
            reviews = _num(data.get(f"reviews_{key}"), int)
            followers = _num(data.get(f"followers_{key}"), int)
            last = (data.get(f"last_{key}") or "").strip()[:60]
            if not any(v not in (None, "") for v in (url, rating, reviews, followers, last)):
                conn.execute("DELETE FROM platform_checks WHERE contact_id=? AND tenant_id=? AND platform=?", (contact_id, tenant, key))
                continue
            conn.execute("""INSERT INTO platform_checks(contact_id,tenant_id,platform,url,rating,review_count,followers,last_activity,checked_on)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(contact_id,tenant_id,platform) DO UPDATE SET url=excluded.url, rating=excluded.rating,
                review_count=excluded.review_count, followers=excluded.followers, last_activity=excluded.last_activity, checked_on=excluded.checked_on""",
                         (contact_id, tenant, key, url, rating, reviews, followers, last, time.strftime("%Y-%m-%d")))
            saved += 1
        conn.commit()
    return saved


def platform_panel(c: dict) -> str:
    w = _w()
    have = {d["platform"]: d for d in get_platform_checks(c["id"])}
    name = f'{c.get("business_name", "")} {c.get("town", "") or ""}'.strip()
    rows = []
    for key, label, domain in PLATFORMS:
        d = have.get(key, {})
        find = "https://www.google.com/search?q=" + quote(f'site:{domain} "{c.get("business_name", "")}" {c.get("town", "") or ""}')

        def val(k):
            v = d.get(k)
            return w.h("" if v is None else v)

        rows.append(f'<tr><td><b>{w.h(label)}</b><div><a class="small" target="_blank" rel="noreferrer" href="{w.h(find)}">Find it</a></div></td>'
                    f'<td><input name="url_{key}" value="{val("url")}" placeholder="https://" style="min-width:170px"></td>'
                    f'<td><input name="rating_{key}" value="{val("rating")}" inputmode="decimal" style="width:70px"></td>'
                    f'<td><input name="reviews_{key}" value="{val("review_count")}" inputmode="numeric" style="width:80px"></td>'
                    f'<td><input name="followers_{key}" value="{val("followers")}" inputmode="numeric" style="width:90px"></td>'
                    f'<td><input name="last_{key}" value="{val("last_activity")}" placeholder="e.g. Oct 2026" style="width:110px"></td></tr>')
    saved = max((d.get("checked_on") or "" for d in have.values()), default="")
    return (f'<section class="panel" id="platforms"><div class="panel-head"><h2>Reviews and social pages</h2><span>{("Saved " + w.h(saved)) if saved else "Type what you see"}</span></div>'
            f'<p class="note">Open each listing yourself and type what you see. These numbers go into both PDFs, marked as checked by hand. Leave a row empty if the business is not on that site.</p>'
            f'<form method="post" action="/platforms/save"><input type="hidden" name="cid" value="{c["id"]}">'
            f'<div class="tbl-wrap"><table style="min-width:640px"><thead><tr><th>Site</th><th>Page link</th><th>Rating</th><th>Reviews</th><th>Followers</th><th>Last activity</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="actions" style="margin-top:10px"><button class="btn primary" type="submit">Save platform checks</button></div></form></section>')


def audit_extras(contact_id, db_path=None, tenant_id=None) -> dict:
    """Latest ranking check and hand-entered platform data, to attach to an audit or a client PDF."""
    w = _w()
    out = {}
    try:
        runs = w.rank_history(contact_id)
        if runs:
            latest = runs[0]
            out["rankings"] = {"generated_at": latest.get("generated_at"), "source": latest.get("source"),
                               "results": [{k: r.get(k) for k in ("keyword", "position", "map_pack_position", "match_type")} for r in latest.get("results", [])]}
    except Exception:
        pass
    try:
        out["platform_checks"] = get_platform_checks(contact_id, db_path, tenant_id)
    except Exception:
        pass
    return out


def client_pdf_for(hid: int):
    """Build the client-facing PDF for one saved audit. Returns (bytes, safe_name, contact_id) or None."""
    w = _w()
    tenant = w.tenant_id()
    with w.connect(w.db_path()) as conn:
        row = conn.execute("""SELECT h.full_audit_json, h.contact_id, c.business_name FROM audit_history AS h
                              JOIN contacts AS c ON c.id = h.contact_id WHERE h.id = ? AND h.tenant_id = ? AND c.tenant_id = ?""",
                           (hid, tenant, tenant)).fetchone()
    if not row:
        return None
    audit = json.loads(row["full_audit_json"] or "{}")
    audit.setdefault("business_name", row["business_name"])
    audit.update(audit_extras(row["contact_id"], w.db_path(), tenant))
    try:
        hosting = int(w.pricing().get("hosting") or 99)
    except Exception:
        hosting = 99
    from client_pdf import build_client_pdf
    pdf = build_client_pdf(audit, {"logo": str(w.LOCKUP), "hosting": hosting, "contact": w.env("CRM_PDF_CONTACT", ""),
                                   "show_price": str(w.env("CRM_CLIENT_PDF_SHOW_PRICE", "1")) != "0"})
    safe = re.sub(r"[^A-Za-z0-9]+", "-", str(row["business_name"])).strip("-")[:40] or "client"
    return pdf, safe, row["contact_id"]


def _latest_audit_id(contact_id: int):
    w = _w()
    with w.connect(w.db_path()) as conn:
        r = conn.execute("SELECT id FROM audit_history WHERE contact_id=? AND tenant_id=? ORDER BY id DESC LIMIT 1", (int(contact_id), w.tenant_id())).fetchone()
    return r["id"] if r else None


def _zenv(*names: str) -> str:
    w = _w()
    for n in names:
        v = w.env(n, "")
        if v:
            return v
    return ""


def zoho_api_ready() -> bool:
    return bool(_zenv("ZOHO_CLIENT_ID", "ZOHO_API_CLIENT_ID") and _zenv("ZOHO_CLIENT_SECRET", "ZOHO_API_CLIENT_SECRET"))


def smtp_ready() -> bool:
    w = _w()
    return zoho_api_ready() or bool(w.env("CRM_SMTP_USER", "") and w.env("CRM_SMTP_PASSWORD", ""))


_ZOHO_TOKEN = {"value": "", "expires": 0.0}
_ZOHO_ACCOUNT = {"id": ""}


def _zoho_token() -> str:
    import httpx
    w = _w()
    if _ZOHO_TOKEN["value"] and time.time() < _ZOHO_TOKEN["expires"]:
        return _ZOHO_TOKEN["value"]
    dc = w.env("ZOHO_DC", "com") or "com"
    data = {"client_id": _zenv("ZOHO_CLIENT_ID", "ZOHO_API_CLIENT_ID"), "client_secret": _zenv("ZOHO_CLIENT_SECRET", "ZOHO_API_CLIENT_SECRET")}
    if w.env("ZOHO_REFRESH_TOKEN", ""):
        data.update(refresh_token=w.env("ZOHO_REFRESH_TOKEN", ""), grant_type="refresh_token")
    else:
        data.update(grant_type="client_credentials", scope="ZohoMail.accounts.READ,ZohoMail.messages.CREATE")
        if w.env("ZOHO_SOID", ""):
            data["soid"] = w.env("ZOHO_SOID", "")
    r = httpx.post(f"https://accounts.zoho.{dc}/oauth/v2/token", timeout=30, data=data)
    tok = (r.json() or {}).get("access_token") if r.status_code == 200 else None
    if not tok:
        raise ValueError("Zoho would not give an access token: " + r.text[:140])
    _ZOHO_TOKEN.update(value=tok, expires=time.time() + 3000)
    return tok


def _zoho_account_id(sender: str, headers: dict, dc: str) -> str:
    import httpx
    w = _w()
    if w.env("ZOHO_ACCOUNT_ID", ""):
        return w.env("ZOHO_ACCOUNT_ID", "")
    if _ZOHO_ACCOUNT["id"]:
        return _ZOHO_ACCOUNT["id"]
    r = httpx.get(f"https://mail.zoho.{dc}/api/accounts", headers=headers, timeout=30)
    if r.status_code != 200:
        raise ValueError(f"Could not look up the Zoho mail account (HTTP {r.status_code}): {r.text[:120]}")
    accounts = (r.json() or {}).get("data") or []
    pick = None
    for a in accounts:
        blob = json.dumps(a).lower()
        if sender.lower() in blob:
            pick = a
            break
    pick = pick or (accounts[0] if accounts else None)
    if not pick or not pick.get("accountId"):
        raise ValueError("No Zoho mail account was found for this login")
    _ZOHO_ACCOUNT["id"] = str(pick["accountId"])
    return _ZOHO_ACCOUNT["id"]


def _send_via_zoho_api(sender: str, to: str, subject: str, body: str, pdf: bytes, filename: str) -> None:
    import httpx
    w = _w()
    dc = w.env("ZOHO_DC", "com") or "com"
    headers = {"Authorization": f"Zoho-oauthtoken {_zoho_token()}", "Accept": "application/json"}
    acct = _zoho_account_id(sender, headers, dc)
    base = f"https://mail.zoho.{dc}/api/accounts/{acct}/messages"
    up = httpx.post(base + "/attachments", headers=headers, timeout=60, params={"isInline": "false"},
                    files={"attach": (filename, pdf, "application/pdf")})
    if up.status_code not in (200, 201):
        raise ValueError(f"Zoho attachment upload failed (HTTP {up.status_code}): {up.text[:120]}")
    data = (up.json() or {}).get("data")
    att = data[0] if isinstance(data, list) and data else data
    if not isinstance(att, dict):
        raise ValueError("Zoho did not return the uploaded attachment details")
    payload = {"fromAddress": sender, "toAddress": to, "subject": subject, "content": body.replace("\n", "<br>"), "mailFormat": "html",
               "attachments": [{"storeName": att.get("storeName"), "attachmentPath": att.get("attachmentPath"), "attachmentName": att.get("attachmentName") or filename}]}
    r = httpx.post(base, headers=headers, json=payload, timeout=60)
    if r.status_code not in (200, 201):
        raise ValueError(f"Zoho refused the email (HTTP {r.status_code}): {r.text[:140]}")


def send_client_email(contact_id: int) -> str:
    """Email the client copy as a PDF attachment. Returns the address it was sent to. Raises ValueError with a plain message."""
    import smtplib, ssl
    from email.message import EmailMessage
    w = _w()
    if not smtp_ready():
        raise ValueError("Email sending is not set up. Set the ZOHO_ variables (or CRM_SMTP_USER and CRM_SMTP_PASSWORD), then restart the CRM")
    c = w.get_contact(int(contact_id), w.db_path(), w.tenant_id())
    if not c or not (c.get("email") or "").strip():
        raise ValueError("This contact has no email address saved")
    hid = _latest_audit_id(contact_id)
    built = client_pdf_for(hid) if hid else None
    if not built:
        raise ValueError("Run an audit first")
    pdf, safe, _ = built
    name = c.get("business_name") or "your business"
    msg = EmailMessage()
    sender = w.env("CRM_SMTP_FROM", "") or w.env("CRM_SMTP_USER", "") or w.env("ZOHO_FROM_ADDRESS", "") or "straightupdigital@zohomail.com"
    msg["From"] = f"St. John from Straight Up Digital <{sender}>"
    msg["To"] = c["email"].strip()
    msg["Subject"] = "Website check"
    msg["Reply-To"] = sender
    tpl = load_templates()
    msg.set_content(render_template(tpl["email_audit_body"], name, voice_finding(int(contact_id), c), c))
    msg.replace_header("Subject", render_template(tpl["email_audit_subject"], name, "", c))
    if zoho_api_ready():
        _send_via_zoho_api(sender, c["email"].strip(), msg["Subject"], msg.get_content(), pdf, f"Website-check-{safe}.pdf")
        from crm_ops import log_activity
        log_activity(int(contact_id), "email", f"Sent the client website check PDF to {c['email']}", channel="Email", db_path=w.db_path(), tenant_id=w.tenant_id())
        return c["email"].strip()
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"Website-check-{safe}.pdf")
    host, port = w.env("CRM_SMTP_HOST", "smtp.zoho.com"), int(w.env("CRM_SMTP_PORT", "465") or 465)
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
        s.login(w.env("CRM_SMTP_USER", ""), w.env("CRM_SMTP_PASSWORD", ""))
        s.send_message(msg)
    from crm_ops import log_activity
    log_activity(int(contact_id), "email", f"Sent the client website check PDF to {c['email']}", channel="Email", db_path=w.db_path(), tenant_id=w.tenant_id())
    return c["email"].strip()


def notify_panel(c: dict) -> str:
    w = _w()
    hid = _latest_audit_id(c["id"])
    if not hid:
        return ('<section class="panel" id="notify"><div class="panel-head"><h2>Notify the client</h2><span>After an audit</span></div>'
                '<p class="note">Run an audit first. Then you can send the client copy by email or WhatsApp from here.</p></section>')
    phone = re.sub(r"\D", "", str(c.get("phone") or ""))
    if phone.startswith("0"):
        phone = "27" + phone[1:]
    name = c.get("business_name", "your business")
    wa_label, _wa_key, msg = whatsapp_draft(c)
    wa = f'https://wa.me/{phone}?text={quote(msg)}' if phone else ""
    email_ok = bool((c.get("email") or "").strip())
    smtp = smtp_ready()
    wa_btn = (f'<a class="btn primary" target="_blank" rel="noreferrer" href="{w.h(wa)}">Open WhatsApp: {w.h(wa_label)}</a>'
              f'<form method="post" action="/notify/wa" style="display:inline"><input type="hidden" name="cid" value="{c["id"]}"><button class="btn" type="submit">Log WhatsApp as sent</button></form>'
              if wa else '<span class="pill warn">Add a phone number for WhatsApp</span>')
    if email_ok and smtp:
        mail_btn = (f'<form method="post" action="/notify/email" class="filters" style="display:inline-flex;gap:8px;align-items:center"><input type="hidden" name="cid" value="{c["id"]}">'
                    f'<label class="small"><input type="checkbox" name="confirm" value="1" required> {w.h(c["email"])} has not asked to stop emails</label>'
                    f'<button class="btn primary" type="submit">Email the client copy</button></form>')
    elif email_ok:
        mail_btn = '<span class="pill warn">Email sending is not set up yet (see note)</span>'
    else:
        mail_btn = '<span class="pill warn">Add an email address to send by email</span>'
    note = ("" if smtp else '<p class="note">To send email from here, set <b>ZOHO_CLIENT_ID</b>, <b>ZOHO_CLIENT_SECRET</b> and <b>ZOHO_REFRESH_TOKEN</b> (and <b>ZOHO_FROM_ADDRESS</b>) before starting the CRM. '
                            'WhatsApp opens a ready message. WhatsApp links cannot attach a file, so download the PDF below and attach it in the chat.</p>')
    return (f'<section class="panel" id="notify"><div class="panel-head"><h2>Notify the client</h2><span>Latest audit</span></div>'
            f'<div class="actions"><a class="btn" target="_blank" href="/client-pdf/{hid}">Preview client PDF</a> <a class="btn" href="/client-pdf/{hid}?dl=1">Download client PDF</a></div>'
            f'<div class="actions" style="margin-top:10px">{wa_btn}</div><pre style="white-space:pre-wrap;font:inherit;background:var(--panel2,#f4f4f4);padding:10px;border-radius:8px;margin:10px 0">{w.h(msg)}</pre><div class="actions" style="margin-top:10px">{mail_btn}</div>{note}</section>')


# ---------------------------------------------------------------- personal wording and follow-ups

def _latest_audit_data(contact_id: int) -> dict:
    w = _w()
    try:
        with w.connect(w.db_path()) as conn:
            r = conn.execute("SELECT full_audit_json FROM audit_history WHERE contact_id=? AND tenant_id=? ORDER BY id DESC LIMIT 1",
                             (int(contact_id), w.tenant_id())).fetchone()
        audit = json.loads(r["full_audit_json"] or "{}") if r else {}
    except Exception:
        audit = {}
    try:
        audit.update(audit_extras(contact_id, w.db_path(), w.tenant_id()))
    except Exception:
        pass
    return audit


def voice_finding(contact_id: int, contact: dict | None = None, audit: dict | None = None) -> str:
    """One plain, specific sentence about the strongest thing the audit found. Written to follow 'I noticed'."""
    audit = audit if audit is not None else _latest_audit_data(contact_id)
    contact = contact or {}
    if not audit:
        return "you don't have a website yet, so people who search for you can't find anything to contact or book" if not (contact.get("website_url") or "").strip() else ""
    wr = audit.get("website_research") or {}
    sh = audit.get("site_health") or {}
    days = sh.get("ssl_days_left")
    if wr.get("ssl_invalid") and "ISSUER" not in str(wr.get("ssl_error", "")).upper() or (days is not None and days < 0):
        return "your website throws a security warning when people open it, which scares customers off before they read a word"
    ps = ((audit.get("pagespeed") or {}).get("mobile") or {}).get("score")
    if ps is not None and ps < 50:
        return f"your website is really slow on a phone (Google scores it {ps} out of 100) and most people leave before it even loads"
    rk = (audit.get("rankings") or {}).get("results") or []
    if rk:
        first = rk[0]
        kw, pos = first.get("keyword"), first.get("position")
        if kw and not pos:
            return f"when I search for \"{kw}\" you don't come up at all, and that's exactly where customers look first"
        if kw and pos and pos > 10:
            return f"when I search for \"{kw}\" you come up at number {pos}, which is page {(pos + 9) // 10} of Google, and hardly anyone goes that far"
    gp = (audit.get("external_research") or {}).get("google_places") or {}
    mine = gp.get("review_count") or 0
    comps = [c for c in ((audit.get("external_research") or {}).get("competitor_candidates") or []) if c.get("review_count")]
    if comps:
        top = max(comps, key=lambda x: x.get("review_count") or 0)
        if top["review_count"] >= max(2 * mine, mine + 20):
            return f"{top.get('title') or 'a competitor nearby'} has {top['review_count']} Google reviews and you have {mine}, and that gap is costing you calls"
    if sh.get("whatsapp_link") is False:
        return "there's no easy way to WhatsApp you from your website, and most local customers would rather message than call"
    return "a few things on your website that are costing you enquiries"


def outreach_count(contact_id: int) -> int:
    w = _w()
    try:
        from crm_ops import list_activities
        events = list_activities(int(contact_id), w.db_path(), w.tenant_id())
        return sum(1 for e in events if str(e.get("summary", "")).startswith("Sent the client"))
    except Exception:
        return 0


def schedule_followup(contact_id: int) -> str:
    """After each send: next reminder in 3 days, then 4 more, then stop. Returns the date set (or empty)."""
    from datetime import date, timedelta
    w = _w()
    n = outreach_count(contact_id)
    when = (date.today() + timedelta(days=3 if n <= 1 else 4)).isoformat() if n <= 2 else ""
    with w.connect(w.db_path()) as conn:
        conn.execute("UPDATE contacts SET next_follow_up_date=? WHERE id=? AND tenant_id=?", (when, int(contact_id), w.tenant_id()))
        conn.commit()
    return when


def whatsapp_draft(c: dict) -> tuple[str, str, str]:
    """(label, key, text) for the next WhatsApp this contact should get."""
    n = outreach_count(c["id"])
    key, label = (("whatsapp_audit", "First message") if n == 0 else ("whatsapp_followup", "Follow-up (day 3)") if n == 1
                  else ("whatsapp_followup2", "Last follow-up (day 7)"))
    text = render_template(load_templates()[key], c.get("business_name") or "your business", voice_finding(c["id"], c), c)
    return label, key, text


# ---------------------------------------------------------------- optional password login

def _password() -> str:
    """Non-empty when a login is configured (a password hash or a plain password)."""
    w = _w()
    return str(w.env("CRM_PASSWORD_HASH", "") or w.env("CRM_PASSWORD", "") or "")


def _check_password(given: str) -> bool:
    import base64
    w = _w()
    stored = str(w.env("CRM_PASSWORD_HASH", "") or "")
    if stored:
        try:
            _scheme, iters, salt, digest = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", given.encode("utf-8"), base64.b64decode(salt), int(iters))
            return hmac.compare_digest(base64.b64encode(dk).decode(), digest)
        except Exception:
            return False
    plain = str(w.env("CRM_PASSWORD", "") or "")
    return bool(plain) and hmac.compare_digest(given.encode("utf-8"), plain.encode("utf-8"))


_SESSIONS: dict[str, dict[str, object]] = {}
_FAILS: dict[str, list] = {}
SESSION_SECONDS = 8 * 3600


def _client_key(handler) -> str:
    # The backend is localhost-only; do not trust client-supplied forwarding headers for lockout keys.
    return str(handler.client_address[0])


def _locked(handler) -> int:
    rec = _FAILS.get(_client_key(handler))
    return max(0, int(rec[1] - time.time())) if rec and rec[1] > time.time() else 0


def _record_fail(handler) -> None:
    key = _client_key(handler)
    count, until = _FAILS.get(key, [0, 0.0])
    count += 1
    _FAILS[key] = [count, time.time() + 900 if count >= 5 else until]
    if count >= 5:
        _FAILS[key][0] = 0


def _secure_flag(handler) -> str:
    w = _w()
    https = (handler.headers.get("X-Forwarded-Proto", "") == "https" or "https" in handler.headers.get("CF-Visitor", "")
             or str(w.env("CRM_COOKIE_SECURE", "")) == "1")
    return "; Secure" if https else ""


def _session_id(handler) -> str:
    jar = SimpleCookie(handler.headers.get("Cookie", ""))
    return jar["sud_session"].value if "sud_session" in jar else ""


def _authed(handler) -> bool:
    if not _password():
        return True
    sid = _session_id(handler)
    session = _SESSIONS.get(sid)
    if not session or float(session.get("expires", 0)) < time.time():
        _SESSIONS.pop(sid, None)
        return False
    return True


def csrf_token(handler) -> str:
    session = _SESSIONS.get(_session_id(handler))
    if not session or float(session.get("expires", 0)) < time.time():
        return ""
    return str(session.get("csrf", ""))


def valid_csrf(handler, supplied: str) -> bool:
    expected = csrf_token(handler)
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def inject_csrf(body: bytes, token: str) -> bytes:
    """Add a session-bound hidden token to every HTML POST form except the pre-auth login form."""
    if not token:
        return body
    text = body.decode("utf-8", "replace")
    def add_token(match):
        tag = match.group(0)
        if not re.search(r"\bmethod\s*=\s*(['\"])post\1", tag, re.I):
            return tag
        action = re.search(r"\baction\s*=\s*(['\"])(.*?)\1", tag, re.I)
        if action and urlparse(action.group(2)).path == "/login":
            return tag
        return tag + f'<input type="hidden" name="_csrf" value="{token}">'
    return re.sub(r"<form\b[^>]*>", add_token, text, flags=re.I).encode("utf-8")


def trusted_post_origin(handler) -> bool:
    """Require an explicit same-origin Origin/Referer and a local or explicitly allowed host."""
    w = _w()
    source = handler.headers.get("Origin") or handler.headers.get("Referer") or ""
    if not source or source == "null":
        return False
    try:
        parsed = urlparse(source)
    except ValueError:
        return False
    request_host = (handler.headers.get("Host") or "").strip().lower()
    origin_host = parsed.netloc.strip().lower()
    hostname = (parsed.hostname or "").lower()
    allowed = {"127.0.0.1", "localhost", "::1"}
    allowed.update(x.strip().lower() for x in str(w.env("CRM_ALLOWED_HOSTS", "") or "").split(",") if x.strip())
    return (parsed.scheme in ("http", "https") and bool(request_host) and origin_host == request_host
            and hostname in allowed)


def login_required_but_missing() -> bool:
    w = _w()
    return str(w.env("CRM_REQUIRE_LOGIN", "")) == "1" and not _password()


def login_page(message: str = "") -> bytes:
    w = _w()
    note = f'<p class="note" style="color:var(--bad)">{w.h(message)}</p>' if message else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<link rel="icon" type="image/png" href="/brand-favicon"><title>Sign in | Straight Up Digital CRM</title><style>{w.CSS}'
            f'.sign{{max-width:380px;margin:14vh auto;padding:26px}}.sign img{{width:100%;max-width:328px;height:88px;object-fit:cover;object-position:left center;margin-bottom:14px}}.sign input{{width:100%;margin:10px 0}}</style></head>'
            f'<body><main class="panel sign"><img src="/brand-logo" alt="Straight Up Digital"><h1 style="font-size:20px">Sign in</h1>{note}'
            f'<form method="post" action="/login"><input type="password" name="password" placeholder="CRM password" autocomplete="current-password" autofocus required>'
            f'<button class="btn primary block" type="submit">Sign in</button></form></main></body></html>').encode("utf-8")


# ---------------------------------------------------------------- routing hooks used by crm_web.Handler

OPEN_PATHS = ("/health", "/login", "/brand-logo", "/brand-favicon")


def handle_get(handler, path: str, params: dict) -> bool:
    w = _w()
    if login_required_but_missing():
        handler.send_body(503, b"Login required: set CRM_PASSWORD_HASH (or CRM_PASSWORD) and restart the CRM.", "text/plain; charset=utf-8")
        return True
    if not _authed(handler) and path not in OPEN_PATHS and not path.startswith("/brand-font/"):
        w._redirect(handler, "/login")
        return True
    if path == "/login":
        if not _password() or _authed(handler):
            w._redirect(handler, "/")
        else:
            handler.send_body(200, login_page())
        return True
    if path == "/logout":
        handler.send_body(405, b"Logout requires a protected POST request", "text/plain; charset=utf-8")
        return True
    if path.startswith("/client-pdf/"):
        try:
            hid = int(path.rsplit("/", 1)[1])
            built = client_pdf_for(hid)
            if not built:
                handler.send_body(404, b"Audit not found", "text/plain; charset=utf-8")
                return True
            pdf, safe, _cid = built
            handler.send_response(200)
            handler.send_header("Content-Type", "application/pdf")
            handler.send_header("Content-Length", str(len(pdf)))
            handler.send_header("Content-Disposition", f'{"attachment" if params.get("dl") else "inline"}; filename="Website-check-{safe}.pdf"')
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(pdf)
        except Exception as exc:
            handler.send_body(500, f"Could not build the client PDF: {type(exc).__name__}: {exc}".encode("utf-8"), "text/plain; charset=utf-8")
        return True
    if path == "/templates":
        handler.send_body(200, templates_page(params.get("message", "")))
        return True
    if path == "/duplicates":
        handler.send_body(200, duplicates_page(params.get("message", "")))
        return True
    return False


def handle_post(handler, path: str, data: dict, form: dict) -> bool:
    w = _w()
    if login_required_but_missing():
        handler.send_body(503, b"Login required: set CRM_PASSWORD_HASH (or CRM_PASSWORD) and restart the CRM.", "text/plain; charset=utf-8")
        return True
    if path == "/login":
        wait = _locked(handler)
        if wait:
            handler.send_body(429, login_page(f"Too many tries. Wait {wait // 60 + 1} minutes and try again."))
        elif _password() and _check_password(data.get("password", "")):
            import secrets
            sid = secrets.token_urlsafe(32)
            _SESSIONS[sid] = {"expires": time.time() + SESSION_SECONDS, "csrf": secrets.token_urlsafe(32)}
            _FAILS.pop(_client_key(handler), None)
            handler.send_response(303)
            handler.send_header("Set-Cookie", f"sud_session={sid}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict" + _secure_flag(handler))
            handler.send_header("Location", "/")
            handler.end_headers()
        else:
            _record_fail(handler)
            time.sleep(1.5)
            handler.send_body(401, login_page("That password is not right."))
        return True
    if not _authed(handler):
        w._redirect(handler, "/login")
        return True
    if path == "/logout":
        _SESSIONS.pop(_session_id(handler), None)
        handler.send_response(303)
        handler.send_header("Set-Cookie", "sud_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" + _secure_flag(handler))
        handler.send_header("Location", "/login" if _password() else "/")
        handler.end_headers()
        return True
    if path == "/notify/email":
        cid = int(data.get("cid", "0") or 0)
        try:
            if data.get("confirm") != "1":
                raise ValueError("Tick the box to confirm first")
            to = send_client_email(cid)
            nxt = schedule_followup(cid)
            msg = f"Client copy emailed to {to}" + (f". Follow-up reminder set for {nxt}" if nxt else "")
        except Exception as exc:
            msg = f"Email not sent: {exc}"[:200]
        w._redirect(handler, f"/contact/{cid}?message=" + quote(msg) + "#audits")
        return True
    if path == "/notify/wa":
        cid = int(data.get("cid", "0") or 0)
        try:
            from crm_ops import log_activity
            log_activity(cid, "whatsapp", "Sent the client a WhatsApp about their website check", channel="WhatsApp", db_path=w.db_path(), tenant_id=w.tenant_id())
            nxt = schedule_followup(cid)
            msg = "WhatsApp logged" + (f". Follow-up reminder set for {nxt}" if nxt else ". No more follow-ups queued")
        except Exception as exc:
            msg = f"Could not log: {exc}"[:200]
        w._redirect(handler, f"/contact/{cid}?message=" + quote(msg) + "#audits")
        return True
    if path == "/platforms/save":
        cid = int(data.get("cid", "0") or 0)
        n = save_platform_checks(cid, data) if cid else 0
        w._redirect(handler, f"/contact/{cid}?message=" + quote(f"Saved {n} platform check(s)") + "#audits")
        return True
    if path == "/templates/save":
        keep = {k: data.get(k, "") for k in TEMPLATE_DEFAULTS if data.get(k, "").strip()}
        TEMPLATE_FILE.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding="utf-8")
        w._redirect(handler, "/templates?message=Templates+saved")
        return True
    if path == "/templates/reset":
        TEMPLATE_FILE.unlink(missing_ok=True)
        w._redirect(handler, "/templates?message=Templates+reset")
        return True
    if path == "/duplicates/resolve":
        try:
            if data.get("confirm") != "yes":
                raise ValueError("Tick the box to confirm these are the same business")
            group = [int(x) for x in form.get("group", []) if x.isdigit()][:20]
            n = resolve_duplicates(int(data.get("keep", "0")), group)
            w._redirect(handler, "/duplicates?message=" + quote(f"Kept one lead and marked {n} as duplicate."))
        except Exception as exc:
            w._redirect(handler, "/duplicates?message=" + quote(f"Could not merge: {exc}"[:160]))
        return True
    return False
