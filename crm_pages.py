"""Additional operations pages for the local Straight Up Digital CRM."""
from __future__ import annotations

from datetime import date


def _w():
    import crm_web as w
    return w


def audit_queue_page(message: str = "") -> bytes:
    w = _w()
    from crm import connect
    tenant = w.tenant_id()
    with connect(w.db_path()) as conn:
        rows = conn.execute("SELECT j.job_id,j.contact_id,j.status,j.error_log,j.created_at,c.business_name FROM audit_jobs j LEFT JOIN contacts c ON c.id=j.contact_id AND c.tenant_id=j.tenant_id WHERE j.tenant_id=? ORDER BY j.created_at DESC LIMIT 250", (tenant,)).fetchall()
        towns = [r[0] for r in conn.execute("SELECT DISTINCT town FROM contacts WHERE tenant_id=? AND town<>'' ORDER BY town", (tenant,))]
    items = []
    for r in rows:
        retry = (f'<form method="post" action="/audit/retry"><input type="hidden" name="contact_id" value="{r["contact_id"]}"><button class="btn sm">Retry</button></form>' if r["status"] == "failed" else "")
        items.append(f'<tr><td>{w.h(r["business_name"] or "Removed lead")}</td><td>{w.h(r["status"])}</td><td>{w.h(r["created_at"][:16].replace("T", " "))}</td><td>{w.h((r["error_log"] or "")[:180])}</td><td>{retry}</td></tr>')
    table = '<div class="tbl-wrap"><table style="min-width:720px"><thead><tr><th>Business</th><th>Status</th><th>Created</th><th>Failure detail</th><th></th></tr></thead><tbody>' + ("".join(items) or '<tr><td colspan="5">No saved audit jobs.</td></tr>') + '</tbody></table></div>'
    options = "".join(f'<option value="{w.h(t)}">{w.h(t)}</option>' for t in towns)
    body = f'''<div class="head"><div><h1>Audit queue</h1><p>One worker processes audits in order. Review verified leads before queuing a batch.</p></div><a class="btn" href="/bulk">Bulk lead actions</a></div>
<section class="panel"><h2>Queue audits by town</h2><form method="post" action="/audit/town" class="filters"><select name="town" required>{options}</select><input type="number" name="limit" value="10" min="1" max="25" aria-label="Maximum leads"><label><input type="checkbox" name="confirm" value="yes" required> I confirm these audits should be queued</label><button class="btn primary">Queue eligible websites</button></form><p class="note">Only verified businesses with a confirmed official website are eligible. The configured daily cost limit still applies.</p></section>
<section class="panel"><h2>Recent jobs</h2>{table}</section>'''
    return w.layout("Audit queue", body, "queue", message)


def finance_page(message: str = "") -> bytes:
    w = _w()
    from crm import connect
    tenant = w.tenant_id()
    with connect(w.db_path()) as conn:
        invoices = conn.execute("SELECT i.invoice_number,i.description,i.amount,i.issued_on,i.due_on,i.status,c.business_name,COALESCE(SUM(p.amount),0) paid FROM crm_invoices i JOIN contacts c ON c.id=i.contact_id AND c.tenant_id=i.tenant_id LEFT JOIN crm_payments p ON p.invoice_id=i.id AND p.tenant_id=i.tenant_id WHERE i.tenant_id=? GROUP BY i.id ORDER BY i.due_on", (tenant,)).fetchall()
        recurring = conn.execute("SELECT r.*,c.business_name FROM crm_recurring_items r JOIN contacts c ON c.id=r.contact_id AND c.tenant_id=r.tenant_id WHERE r.tenant_id=? AND r.active=1 ORDER BY r.next_due_on LIMIT 200", (tenant,)).fetchall()
    rows = "".join(f'<tr><td>{w.h(r["business_name"])}</td><td>{w.h(r["invoice_number"])}</td><td>{w.h(r["description"])}</td><td>{w.money(r["amount"])}</td><td>{w.money(max(0, float(r["amount"])-float(r["paid"])))}</td><td>{w.h(r["due_on"])}</td><td>{w.h(r["status"])}</td></tr>' for r in invoices) or '<tr><td colspan="7">No invoices recorded.</td></tr>'
    rec_rows = "".join(f'<tr><td>{w.h(r["business_name"])}</td><td>{w.h(r["description"] or r["item_type"])}</td><td>{w.money(r["amount"])}</td><td>{w.h(r["frequency"])}</td><td>{w.h(r["next_due_on"] or "Not set")}</td><td>{w.h(r["last_paid_on"] or "Not marked")}</td><td><a class="btn" href="/contact/{r["contact_id"]}">Open</a></td></tr>' for r in recurring) or '<tr><td colspan="7">No renewal items are tracked.</td></tr>'
    body = f'''<div class="head"><div><h1>Sales &amp; billing</h1><p>Quotes, invoices, payments received and renewal follow-ups. This is a manual ledger, not a payment processor.</p></div></div>
<section class="panel"><h2>Invoices</h2><div class="tbl-wrap"><table style="min-width:820px"><thead><tr><th>Client</th><th>Number</th><th>Description</th><th>Total</th><th>Balance</th><th>Due</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="panel"><h2>Monthly fees and renewals</h2><div class="tbl-wrap"><table style="min-width:820px"><thead><tr><th>Client</th><th>Item</th><th>Amount</th><th>Cycle</th><th>Next due</th><th>Last paid</th><th></th></tr></thead><tbody>{rec_rows}</tbody></table></div></section>'''
    return w.layout("Sales & billing", body, "finance", message)


def projects_page(message: str = "") -> bytes:
    w = _w()
    from crm import connect
    with connect(w.db_path()) as conn:
        rows = conn.execute("SELECT c.id,c.business_name,c.town,p.status,p.domain,p.next_milestone,p.next_milestone_on,p.go_live_on FROM contacts c LEFT JOIN crm_projects p ON p.contact_id=c.id AND p.tenant_id=c.tenant_id WHERE c.tenant_id=? AND (p.id IS NOT NULL OR c.pipeline_stage='live') ORDER BY COALESCE(p.next_milestone_on,'9999-12-31')", (w.tenant_id(),)).fetchall()
    trs = "".join(f'<tr><td><a href="/contact/{r["id"]}">{w.h(r["business_name"])}</a></td><td>{w.h(r["town"])}</td><td>{w.h((r["status"] or "live").replace("_"," ").title())}</td><td>{w.h(r["domain"] or "—")}</td><td>{w.h(r["next_milestone"] or "—")}</td><td>{w.h(r["next_milestone_on"] or "—")}</td><td>{w.h(r["go_live_on"] or "—")}</td></tr>' for r in rows) or '<tr><td colspan="7">No projects tracked yet. Save project details from a client profile.</td></tr>'
    body = f'''<div class="head"><div><h1>Client projects</h1><p>Website delivery status, milestones, domain/hosting notes and go-live dates.</p></div></div><section class="panel"><div class="tbl-wrap"><table style="min-width:760px"><thead><tr><th>Client</th><th>Town</th><th>Status</th><th>Domain</th><th>Next milestone</th><th>Due</th><th>Live since</th></tr></thead><tbody>{trs}</tbody></table></div></section>'''
    return w.layout("Projects", body, "projects", message)


def insights_page(message: str = "") -> bytes:
    w = _w()
    from crm_ops import sales_metrics
    m = sales_metrics(w.db_path(), w.tenant_id())
    stages = m["stages"]
    total = max(1, m["total"])
    kpis = f'''<div class="kpis"><div class="kpi"><div><div class="lab">Leads</div><div class="num">{m["total"]}</div></div></div><div class="kpi mint"><div><div class="lab">Accepted / live</div><div class="num">{m["won"]}</div><div class="sub">{m["conversion_pct"]}% conversion</div></div></div><div class="kpi blue"><div><div class="lab">Weighted pipeline</div><div class="num">{w.money(m["weighted_pipeline_zar"])}</div><div class="sub">Indicative, not booked income</div></div></div><div class="kpi amber"><div><div class="lab">Live monthly income</div><div class="num">{w.money(m["monthly_live_zar"])}</div><div class="sub">Saved live-stage clients only</div></div></div></div>'''
    stage_rows = "".join(f'<tr><td>{w.h(w.STAGE_LABEL[s])}</td><td>{n}</td><td>{n/total*100:.1f}%</td></tr>' for s, n in stages.items())
    town_rows = "".join(f'<tr><td>{w.h(k)}</td><td>{v["leads"]}</td><td>{v["won"]}</td><td>{v["won"]/max(1,v["leads"])*100:.1f}%</td></tr>' for k, v in sorted(m["towns"].items(), key=lambda x: (-x[1]["leads"], x[0]))[:15]) or '<tr><td colspan="4">No town data.</td></tr>'
    sector_rows = "".join(f'<tr><td>{w.h(k)}</td><td>{v["leads"]}</td><td>{v["won"]}</td><td>{v["won"]/max(1,v["leads"])*100:.1f}%</td></tr>' for k, v in sorted(m["sectors"].items(), key=lambda x: (-x[1]["leads"], x[0]))[:15]) or '<tr><td colspan="4">No sector data.</td></tr>'
    speed = f'{m["median_first_contact_hours"]} hours' if m["median_first_contact_hours"] is not None else "Not enough logged activity yet"
    tiles = "".join(f'<div class="stage"><b>{stages[s]}</b><span>{w.h(w.STAGE_LABEL[s])}</span></div>' for s in w.LEAD_STAGES)
    body = f'''<div class="head"><div><h1>Sales insights</h1><p>Directional estimates from your saved CRM data. Log contacts and keep stages current to improve the picture.</p></div></div>{kpis}
<section class="panel"><h2>Pipeline by stage</h2><div class="stages">{tiles}</div><div class="tbl-wrap"><table><thead><tr><th>Stage</th><th>Leads</th><th>Share</th></tr></thead><tbody>{stage_rows}</tbody></table></div><p class="note">Planning estimate only: quoted 35%, contacted 10%, accepted/live 100%. Not a sales commitment or accounting forecast.</p></section>
<section class="panel"><h2>Follow-up speed</h2><p>Median time from lead creation to first logged outbound call/message: <b>{w.h(speed)}</b>.</p><p class="muted">Saved audits: {m["audits"]} across {m["audited_contacts"]} leads.</p></section>
<section class="panel"><h2>By town</h2><div class="tbl-wrap"><table><thead><tr><th>Town</th><th>Leads</th><th>Accepted/live</th><th>Rate</th></tr></thead><tbody>{town_rows}</tbody></table></div></section>
<section class="panel"><h2>By sector</h2><div class="tbl-wrap"><table><thead><tr><th>Sector</th><th>Leads</th><th>Accepted/live</th><th>Rate</th></tr></thead><tbody>{sector_rows}</tbody></table></div></section>'''
    return w.layout("Insights", body, "insights", message)


def bulk_page(message: str = "") -> bytes:
    w = _w()
    from crm import list_contacts
    leads = list_contacts(w.db_path(), tenant_id=w.tenant_id(), limit=250)
    rows = "".join(f'<tr><td><input type="checkbox" name="contact_id" value="{c["id"]}"></td><td><a href="/contact/{c["id"]}">{w.h(c["business_name"])}</a></td><td>{w.h(c.get("town"))}</td><td>{w.stage_pill(c.get("pipeline_stage"))}</td><td>{w.h(c.get("website_url") or "—")}</td></tr>' for c in leads)
    stage_opts = "".join(f'<option value="{s}">{w.h(w.STAGE_LABEL[s])}</option>' for s in w.LEAD_STAGES)
    body = f'''<div class="head"><div><h1>Bulk lead actions</h1><p>Select up to 250 leads for a stage change. Confirm before saving.</p></div></div><section class="panel"><form method="post" action="/bulk/stage"><div class="tbl-wrap"><table style="min-width:650px"><thead><tr><th>Select</th><th>Business</th><th>Town</th><th>Stage</th><th>Website</th></tr></thead><tbody>{rows or '<tr><td colspan="5">No leads</td></tr>'}</tbody></table></div><div class="filters" style="margin-top:12px"><select name="stage">{stage_opts}</select><label><input type="checkbox" name="confirm" value="yes" required> I reviewed these leads</label><button class="btn primary">Change selected stage</button></div></form></section>
<section class="panel"><h2>CSV import with duplicate review</h2><p class="note">Choose a local CSV file or paste its contents. Preview and classify each row before import. Existing contact values are never silently overwritten.</p><form method="post" action="/bulk/csv-preview"><label class="lab" for="csv-file">Choose CSV file (optional; maximum 250 KB)</label><input id="csv-file" data-csv-file type="file" accept=".csv,text/csv"><textarea id="csv-text" name="csv_text" required maxlength="250000" rows="8" placeholder="business_name,email,phone,website_url,town,sector&#10;Example Business,owner@example.co.za,0460000000,https://example.co.za,Port Alfred,Hospitality"></textarea><button class="btn">Preview CSV</button></form></section>
<section class="panel"><h2>Website enquiries</h2><p>Pulls delivered enquiries from the production site's private queue into this local CRM. Import is idempotent; marketing outreach stays disabled. Zoho remains the primary notification.</p><form method="post" action="/website/sync"><button class="btn primary">Sync website enquiries now</button></form></section>
<section class="panel"><h2>Local backup</h2><p>Creates and verifies a private SQLite-consistent backup on this computer.</p><form method="post" action="/backup/create"><button class="btn">Create backup now</button></form></section>'''
    return w.layout("Bulk actions", body, "leads", message)


def compliance_page(message: str = "") -> bytes:
    w = _w()
    from crm import connect
    tenant = w.tenant_id()
    with connect(w.db_path()) as conn:
        total = conn.execute("SELECT count(*) FROM contacts WHERE tenant_id=?", (tenant,)).fetchone()[0]
        sources = conn.execute("SELECT COALESCE(found_via,'unknown'),count(*) FROM contacts WHERE tenant_id=? GROUP BY found_via", (tenant,)).fetchall()
        consent = conn.execute("SELECT sum(CASE WHEN marketing_consent=1 THEN 1 ELSE 0 END),sum(CASE WHEN outreach_allowed=0 THEN 1 ELSE 0 END) FROM contacts WHERE tenant_id=?", (tenant,)).fetchone()
        suppressions = conn.execute("SELECT count(*) FROM suppression_list WHERE tenant_id=?", (tenant,)).fetchone()[0]
    source_rows = "".join(f'<tr><td>{w.h(r[0])}</td><td>{r[1]}</td></tr>' for r in sources)
    cards = f'''<div class="kpis"><div class="kpi"><div><div class="lab">Leads on file</div><div class="num">{total}</div></div></div><div class="kpi mint"><div><div class="lab">Marketing consent marked</div><div class="num">{consent[0] or 0}</div></div></div><div class="kpi amber"><div><div class="lab">Outreach disabled</div><div class="num">{consent[1] or 0}</div></div></div><div class="kpi"><div><div class="lab">Suppression records</div><div class="num">{suppressions}</div></div></div></div>'''
    body = f'''<div class="head"><div><h1>Privacy &amp; backups</h1><p>The CRM is local to this computer. It has no public login. Do not expose it through a tunnel or network share.</p></div></div>{cards}
<section class="panel"><h2>Recorded data sources</h2><div class="tbl-wrap"><table><thead><tr><th>Source</th><th>Leads</th></tr></thead><tbody>{source_rows}</tbody></table></div><p class="note">Review each contact's source, consent and outreach status before campaigning. Use the profile actions to log an opt-out or owner-confirmed erasure.</p></section>
<section class="panel"><h2>Verified local backup</h2><p>Keep backup files private and protect them like the CRM database.</p><form method="post" action="/backup/create"><button class="btn primary">Create verified backup</button></form></section>
<section class="panel"><h2>Retention &amp; access</h2><p>Audit reports and client notes can contain personal information. Limit access to the owner. This build intentionally binds only to localhost and does not support tunnel access. Erasure permanently removes CRM records and associated report files while retaining one-way hashed do-not-contact identifiers.</p></section>'''
    return w.layout("Privacy & backups", body, "shield", message)


def csv_preview_page(rows: list[dict], token: str, message: str = "") -> bytes:
    w = _w()
    lines = []
    for item in rows:
        d = item["data"]
        options = ['<option value="skip">Skip row</option>']
        if item["duplicates"]:
            for match in item["duplicates"]:
                label = f"Merge into #{match['id']} {match['business_name']} ({match.get('town') or match.get('location') or 'no town'})"
                options.append(f'<option value="merge:{int(match["id"])}">{w.h(label)}</option>')
            choice = "skip"
        else:
            options.append('<option value="new">Import as new lead (unverified)</option>')
            choice = "new"
        summary = " · ".join(w.h(d.get(k) or "—") for k in ("business_name", "email", "phone", "website_url", "town", "sector"))
        lines.append(f'<tr><td>{item["row"] + 1}</td><td>{summary}</td><td><select name="action_{item["row"]}">{"".join(options)}</select></td></tr>')
    body = f'''<div class="head"><div><h1>Review CSV rows</h1><p>Choose skip, import as a new unverified lead, or fill blank fields on a matched lead. No existing value is overwritten.</p></div><a class="btn" href="/bulk">Cancel</a></div>
<section class="panel"><form method="post" action="/bulk/csv-commit"><input type="hidden" name="token" value="{w.h(token)}"><div class="tbl-wrap"><table style="min-width:920px"><thead><tr><th>Row</th><th>CSV details</th><th>What should happen?</th></tr></thead><tbody>{''.join(lines)}</tbody></table></div><label style="display:block;margin:14px 0"><input type="checkbox" name="confirm" value="yes" required> I reviewed every row and confirm the selected changes</label><button class="btn primary">Apply reviewed import</button></form></section>'''
    return w.layout("Review CSV", body, "bulk", message)
