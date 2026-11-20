"""Short, plain-language PDF for the customer. The long audit PDF stays as the internal working copy."""
from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, KeepTogether, NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from audit_priority import priority_counts
from audit_signals import build_pillars
from audit_text import trim_snippet

OBSIDIAN, LIME, GRAPH, IVORY = "#080A09", "#A8FF65", "#252B27", "#F2F4EF"
GREEN, GREY, RED, AMBER, TRACK = "#4F8A22", "#5F685F", "#C0392B", "#C98A00", "#E4E8E1"
W, H = A4
MARGIN = 18 * mm
FW = W - 2 * MARGIN
BAND = 74 * mm

CATEGORY_WORDS = {
    "Website structure": "How well your website is built",
    "Google Business Profile": "Your Google listing",
    "Linked social profiles": "Social media links",
    "Content coverage": "What your website tells customers",
    "Independent reputation": "Customer reviews",
    "Business identity clarity": "How clear your business is",
}
PRIORITY_WORDS = {"High": ("Fix first", RED), "Medium": ("Fix next", AMBER), "Low": ("When you can", GREEN)}


def _fonts() -> tuple[str, str, str]:
    d = Path(__file__).resolve().parent / "assets" / "brand" / "fonts"
    files = {"Inter": d / "Inter-Regular.ttf", "Inter-Bold": d / "Inter-Bold.ttf", "Montserrat-Bold": d / "Montserrat-Bold.ttf"}
    if all(p.is_file() for p in files.values()):
        try:
            for n, p in files.items():
                if n not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(n, str(p)))
            return "Inter", "Inter-Bold", "Montserrat-Bold"
        except Exception:
            pass
    return "Helvetica", "Helvetica-Bold", "Helvetica-Bold"


BODY, BOLD, DISPLAY = _fonts()


def style(name, **kw) -> ParagraphStyle:
    base = dict(fontName=BODY, fontSize=10, leading=14.5, textColor=colors.HexColor(GRAPH))
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "h1": style("h1", fontName=DISPLAY, fontSize=17, leading=21, spaceAfter=4),
    "h2": style("h2", fontName=DISPLAY, fontSize=13, leading=17, spaceBefore=4, spaceAfter=6),
    "body": style("body"),
    "lead": style("lead", fontSize=11.5, leading=17),
    "small": style("small", fontSize=8.5, leading=12, textColor=colors.HexColor(GREY)),
    "label": style("label", fontName=BOLD, fontSize=8, leading=10, textColor=colors.HexColor(GREY)),
    "card_t": style("card_t", fontName=BOLD, fontSize=11, leading=14),
    "light": style("light", textColor=colors.HexColor(IVORY)),
    "light_b": style("light_b", fontName=DISPLAY, fontSize=22, leading=26, textColor=colors.HexColor(LIME)),
}


def esc(t) -> str:
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def trim(text: str, limit: int = 300) -> str:
    return trim_snippet(text, limit)


def rating(score) -> tuple[str, str]:
    if score is None:
        return "not scored", GREY
    if score >= 80:
        return "strong", GREEN
    if score >= 60:
        return "good, with a few gaps", AMBER
    if score >= 40:
        return "in need of work", AMBER
    return "in need of urgent attention", RED


class Bars(Flowable):
    """Plain horizontal score bars, one row per area."""

    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.row_h = 10.5 * mm
        self.width = FW
        self.height = self.row_h * len(rows)

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        label_w, num_w = 62 * mm, 26 * mm
        bar_w = self.width - label_w - num_w
        y = self.height
        for label, score, out_of in self.rows:
            y -= self.row_h
            mid = y + self.row_h / 2
            c.setFont(BODY, 9.5)
            c.setFillColor(colors.HexColor(GRAPH))
            c.drawString(0, mid - 3, label)
            c.setFillColor(colors.HexColor(TRACK))
            c.roundRect(label_w, mid - 3.2, bar_w, 6.4, 3.2, stroke=0, fill=1)
            if score is not None and out_of:
                pct = max(0.0, min(1.0, score / out_of))
                col = GREEN if pct >= 0.7 else AMBER if pct >= 0.4 else RED
                c.setFillColor(colors.HexColor(col))
                if pct > 0:
                    c.roundRect(label_w, mid - 3.2, max(6.4, bar_w * pct), 6.4, 3.2, stroke=0, fill=1)
                txt = f"{score} / {out_of}"
            else:
                txt = "Not checked"
            c.setFillColor(colors.HexColor(GRAPH))
            c.setFont(BOLD, 9)
            c.drawRightString(self.width, mid - 3, txt)


def _page_one(c, doc):
    brand = doc.brand
    c.saveState()
    c.setFillColor(colors.HexColor(OBSIDIAN))
    c.rect(0, H - BAND, W, BAND, stroke=0, fill=1)
    c.setFillColor(colors.HexColor(LIME))
    c.rect(0, H - BAND, W, 1.6 * mm, stroke=0, fill=1)
    logo = brand.get("logo")
    if logo and Path(logo).is_file():
        try:
            c.drawImage(str(logo), MARGIN - 3 * mm, H - 29 * mm, width=82 * mm, height=22 * mm, preserveAspectRatio=True, mask="auto", anchor="sw")
        except Exception:
            pass
    c.setFillColor(colors.HexColor(LIME))
    c.setFont(BOLD, 8.5)
    c.drawString(MARGIN, H - 38 * mm, "WEBSITE CHECK")
    c.setFillColor(colors.HexColor(IVORY))
    name = doc.business
    size = 25 if len(name) <= 24 else 20 if len(name) <= 36 else 16
    c.setFont(DISPLAY, size)
    c.drawString(MARGIN, H - 49 * mm, name[:46])
    c.setFont(BODY, 10)
    c.setFillColor(colors.HexColor("#B9C0B7"))
    c.drawString(MARGIN, H - 57 * mm, doc.website_label[:60])
    c.drawString(MARGIN, H - 63 * mm, doc.date_label)
    score = doc.score
    cx, cy, r = W - MARGIN - 20 * mm, H - 42 * mm, 17 * mm
    c.setLineWidth(5)
    c.setStrokeColor(colors.HexColor(GRAPH))
    c.circle(cx, cy, r, stroke=1, fill=0)
    if score is not None:
        col, _ = (GREEN, 0) if score >= 70 else (AMBER, 0) if score >= 40 else (RED, 0)
        c.setStrokeColor(colors.HexColor(LIME if score >= 70 else "#FFC857" if score >= 40 else "#FF6B5B"))
        c.setLineCap(1)
        c.arc(cx - r, cy - r, cx + r, cy + r, 90, -360 * max(0.01, score / 100))
    c.setFillColor(colors.HexColor(IVORY))
    c.setFont(DISPLAY, 26)
    c.drawCentredString(cx, cy - 3, str(score) if score is not None else "n/a")
    c.setFont(BODY, 8)
    c.setFillColor(colors.HexColor("#B9C0B7"))
    c.drawCentredString(cx, cy - 13, "out of 100")
    _footer(c, doc)
    c.restoreState()


def _later(c, doc):
    c.saveState()
    c.setStrokeColor(colors.HexColor(TRACK))
    c.setLineWidth(0.6)
    c.line(MARGIN, H - 16 * mm, W - MARGIN, H - 16 * mm)
    c.setFont(BOLD, 8.5)
    c.setFillColor(colors.HexColor(GREY))
    c.drawString(MARGIN, H - 12.5 * mm, f"Website check: {doc.business}"[:70])
    _footer(c, doc)
    c.restoreState()


def _footer(c, doc):
    c.setFont(BODY, 8)
    c.setFillColor(colors.HexColor(GREY))
    c.drawString(MARGIN, 10 * mm, "Straight Up Digital  |  Port Alfred, Eastern Cape")
    c.drawRightString(W - MARGIN, 10 * mm, f"Page {doc.page}")


def _card(op) -> Flowable:
    word, col = PRIORITY_WORDS.get(op.get("priority"), ("Fix next", AMBER))
    inner = [Paragraph(f'<font color="{col}">{esc(word.upper())}</font>', S["label"]),
             Paragraph(esc(op.get("title")), S["card_t"]), Spacer(1, 2),
             Paragraph(
                 f'<b>Impact:</b> {esc(op.get("impact") or "Not assessed")}  '
                 f'<b>Effort:</b> {esc(op.get("effort") or "Not assessed")}',
                 S["small"],
             ),
             Paragraph(f'<b>What we found.</b> {esc(trim(op.get("evidence"), 280))}', S["body"]), Spacer(1, 2),
             Paragraph(f'<b>What to do.</b> {esc(trim(op.get("action"), 280))}', S["body"])]
    t = Table([[inner]], colWidths=[FW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F8F4")), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(col)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return KeepTogether([t, Spacer(1, 5)])


def _online_block(audit: dict) -> list:
    ps = (audit.get("pagespeed") or {}).get("mobile") or {}
    desktop = (audit.get("pagespeed") or {}).get("desktop") or {}
    local = (audit.get("pagespeed") or {}).get("local") or {}
    rk = (audit.get("rankings") or {}).get("results") or []
    pc = audit.get("platform_checks") or []
    sh = audit.get("site_health") or {}
    email = audit.get("email_posture") or {}
    nap = audit.get("nap_comparison") or {}
    profiles = audit.get("profile_presence") or []
    search_sets = (audit.get("external_research") or {}).get("searches") or {}
    competitor_search = search_sets.get("competitor_discovery") or {}
    me = (audit.get("external_research") or {}).get("google_places") or {}
    comps = [c for c in ((audit.get("external_research") or {}).get("competitor_candidates") or []) if c.get("review_count")]
    comps = sorted(comps, key=lambda x: -(x.get("review_count") or 0))[:3]
    if (
        ps.get("score") is None
        and desktop.get("score") is None
        and not local
        and not rk
        and not pc
        and not sh.get("dns")
        and not nap
        and not profiles
        and not competitor_search
        and not (comps and me.get("review_count"))
    ):
        return []
    out = [Paragraph("How you show up online", S["h1"])]
    if ps.get("score") is not None:
        sc = ps["score"]
        word = "fast" if sc >= 90 else "average" if sc >= 50 else "slow"
        out += [Paragraph(f"<b>Speed on a phone (lab):</b> {sc} out of 100, which is {word}.", S["body"]), Spacer(1, 4)]
        cats = ps.get("categories") or {}
        if cats:
            bits = [f"{lab} {cats[k]}" for lab, k in (("ease of use", "accessibility"), ("good practice", "best_practices"), ("Google search basics", "seo")) if cats.get(k) is not None]
            out += [Paragraph("<b>Google's own website checks (out of 100):</b> " + ", ".join(bits) + ".", S["body"]), Spacer(1, 6)]
    if desktop.get("score") is not None:
        out += [Paragraph(f"<b>Speed on desktop (lab):</b> {desktop['score']} out of 100.", S["body"]), Spacer(1, 4)]
    crux = (
        (audit.get("pagespeed") or {}).get("crux")
        or ps.get("crux")
        or desktop.get("crux")
        or {}
    )
    metrics = []
    for label, key in (("LCP", "lcp"), ("INP", "inp"), ("TBT", "tbt"), ("CLS", "cls")):
        value = (ps.get("metrics") or {}).get(key)
        field = crux.get(key)
        if value:
            metrics.append(f"{label} lab {value}")
        elif field and field.get("p75") is not None:
            metrics.append(f"{label} field p75 {field['p75']}{field.get('unit') or ''}")
    if metrics:
        out += [Paragraph("<b>Core speed metrics:</b> " + "; ".join(metrics) + ".", S["body"]), Spacer(1, 6)]
    local_bits = []
    for label, key, suffix in (
        ("Response time", "response_time_ms", " ms"),
        ("TTFB estimate", "ttfb_ms", " ms"),
        ("HTML size", "html_size_bytes", " bytes"),
        ("Images", "image_count", ""),
        ("Images without alt text", "images_missing_alt", ""),
    ):
        if local.get(key) is not None:
            local_bits.append(f"{label}: {local[key]}{suffix}")
    if local.get("viewport_present") is not None:
        local_bits.append("Viewport tag: present" if local["viewport_present"] else "Viewport tag: not found")
    if local.get("https_redirect") is not None:
        local_bits.append("HTTP-to-HTTPS redirect: found" if local["https_redirect"] else "HTTP-to-HTTPS redirect: not observed")
    if local.get("https_redirect_chain"):
        local_bits.append("Redirect chain: " + " ; ".join(local["https_redirect_chain"]))
    if local_bits:
        out += [Paragraph("<b>Local estimates:</b> " + "; ".join(local_bits) + ".", S["body"]), Spacer(1, 6)]
    elif local.get("error"):
        out += [Paragraph(
            f"<b>Local estimate:</b> {esc(local['error'])}",
            S["small"],
        ), Spacer(1, 6)]
    ssl = sh.get("ssl_days_left")
    if ssl is not None:
        txt = f"<b>Security padlock:</b> valid, renews in {ssl} days." if ssl >= 30 else f"<b>Security padlock:</b> expires in {max(ssl, 0)} days. Visitors will see a warning if it lapses."
        out += [Paragraph(txt, S["body"]), Spacer(1, 6)]
    d = sh.get("dns") or {}
    if d.get("mx") and d.get("lookup_worked") and not (d.get("spf") and d.get("dmarc")):
        out += [Paragraph("<b>Business email:</b> missing protection records, so your emails can land in customers' spam folders. This is a quick fix.", S["body"]), Spacer(1, 6)]
    if email:
        spf_status = (
            "present" if email.get("spf_present") is True
            else "not detected" if email.get("spf_present") is False
            else "not verified"
        )
        out += [Paragraph(
            f"<b>Email posture:</b> MX provider {esc(email.get('mx_provider') or 'Unknown')}; "
            f"SPF {spf_status}; "
            f"DMARC policy {esc(email.get('dmarc_policy') or 'unknown')} "
            f"({esc(email.get('dmarc_strength') or 'not assessed')}).",
            S["body"],
        ), Spacer(1, 6)]
    if nap:
        rows = [[Paragraph("<b>NAP field</b>", S["body"]), Paragraph("<b>Website</b>", S["body"]),
                 Paragraph("<b>Google profile</b>", S["body"]), Paragraph("<b>Result</b>", S["body"])]]
        for key in ("name", "phone", "address"):
            item = nap.get(key) or {}
            rows.append([
                Paragraph(esc(key.title()), S["body"]),
                Paragraph(esc(item.get("website") or "Not found"), S["body"]),
                Paragraph(esc(item.get("google") or "Not found"), S["body"]),
                Paragraph(esc(item.get("status") or "Not compared"), S["body"]),
            ])
        table = Table(rows, colWidths=[FW * .16, FW * .30, FW * .30, FW * .24])
        table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        out += [table, Spacer(1, 8)]
    if comps and me.get("review_count"):
        rows = [[Paragraph("<b>Google reviews</b>", S["body"]), Paragraph("<b>Reviews</b>", S["body"]), Paragraph("<b>Rating</b>", S["body"])],
                [Paragraph("You", S["body"]), Paragraph(esc(me.get("review_count")), S["body"]), Paragraph(esc(me.get("rating") or "-"), S["body"])]]
        for c in comps:
            rows.append([Paragraph(esc(str(c.get("title"))[:40]), S["body"]), Paragraph(esc(c.get("review_count")), S["body"]), Paragraph(esc(c.get("rating") or "-"), S["body"])])
        t = Table(rows, colWidths=[FW * 0.56, FW * 0.22, FW * 0.22])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        out += [t, Spacer(1, 8)]
    if rk:
        rows = [[Paragraph("<b>When someone searches</b>", S["body"]), Paragraph("<b>Where you appear</b>", S["body"])]]
        for r in rk[:5]:
            pos = r.get("position")
            where = f"Position {pos} on Google" if pos and pos <= 10 else ("Position " + str(pos) if pos else "Not in the top results")
            if r.get("map_pack_position"):
                where += f", and number {r['map_pack_position']} on Google Maps"
            rows.append([Paragraph(esc(r.get("keyword")), S["body"]), Paragraph(esc(where), S["body"])])
        t = Table(rows, colWidths=[FW * 0.55, FW * 0.45])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        out += [t, Spacer(1, 8)]
    if pc:
        rows = [[Paragraph(f"<b>{h}</b>", S["body"]) for h in ("Where", "Rating", "Reviews", "Followers")]]
        for x in pc:
            rows.append([Paragraph(esc(x.get("label") or x.get("platform")), S["body"]),
                         Paragraph(esc("-" if x.get("rating") is None else f"{x['rating']:g}"), S["body"]),
                         Paragraph(esc("-" if x.get("review_count") is None else x["review_count"]), S["body"]),
                         Paragraph(esc("-" if x.get("followers") is None else x["followers"]), S["body"])])
        t = Table(rows, colWidths=[FW * 0.34, FW * 0.18, FW * 0.24, FW * 0.24])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        out += [t, Spacer(1, 4)]
    if profiles:
        rows = [[Paragraph("<b>Profile</b>", S["body"]), Paragraph("<b>Status</b>", S["body"]),
                 Paragraph("<b>URL</b>", S["body"]), Paragraph("<b>How it was checked</b>", S["body"])]]
        for profile in profiles[:8]:
            rows.append([
                Paragraph(esc(profile.get("platform")), S["body"]),
                Paragraph(esc(profile.get("status")), S["body"]),
                Paragraph(esc(profile.get("url")), S["small"]),
                Paragraph(esc(profile.get("match_reason")), S["small"]),
            ])
        table = Table(rows, colWidths=[FW * .18, FW * .23, FW * .30, FW * .29])
        table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        out += [Paragraph("Social and directory profiles", S["h2"]), table, Spacer(1, 4)]
    if competitor_search:
        query = competitor_search.get("query") or ""
        provider = competitor_search.get("provider") or "Unavailable"
        search_date = competitor_search.get("date") or "date not recorded"
        out += [
            Paragraph(
                f"<b>Competitor search:</b> {esc(query)} "
                f"({esc(provider)}, {esc(search_date)}).",
                S["body"],
            ),
            Spacer(1, 4),
        ]
        results = competitor_search.get("results") or []
        if results:
            rows = [[Paragraph("<b>Search result</b>", S["body"]), Paragraph("<b>URL</b>", S["body"])]]
            for result in results[:3]:
                rows.append([
                    Paragraph(esc(result.get("title") or "Untitled result"), S["body"]),
                    Paragraph(esc(result.get("url") or ""), S["small"]),
                ])
            table = Table(rows, colWidths=[FW * .42, FW * .58])
            table.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            out += [table, Spacer(1, 6)]
        elif competitor_search.get("error"):
            out += [Paragraph(esc(competitor_search["error"]), S["small"]), Spacer(1, 4)]
    return [KeepTogether(out), Spacer(1, 8)]


def build_client_pdf(audit: dict, brand: dict | None = None) -> bytes:
    brand = brand or {}
    sc = audit.get("scorecard") or {}
    score = sc.get("assessed_score")
    name = str(audit.get("business_name") or "Your business")
    site = str(audit.get("website") or "")
    ops = [o for o in (audit.get("opportunities") or []) if o.get("title")]
    order = {"High": 0, "Medium": 1, "Low": 2}
    ops.sort(key=lambda o: order.get(o.get("priority"), 1))
    when = str(audit.get("generated_at") or "")[:10]
    try:
        date_label = datetime.fromisoformat(when).strftime("%d %B %Y").lstrip("0")
    except ValueError:
        date_label = datetime.now().strftime("%d %B %Y").lstrip("0")

    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=f"Website check: {name}", author="Straight Up Digital",
                          leftMargin=MARGIN, rightMargin=MARGIN, topMargin=22 * mm, bottomMargin=18 * mm)
    doc.brand, doc.business, doc.score = brand, name, score
    doc.website_label = re.sub(r"^https?://(www\.)?", "", site).rstrip("/") or "Website not listed"
    doc.date_label = date_label
    first = Frame(MARGIN, 18 * mm, FW, H - BAND - 18 * mm - 10 * mm, id="f1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN, 18 * mm, FW, H - 18 * mm - 24 * mm, id="f2", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="first", frames=[first], onPage=_page_one),
                          PageTemplate(id="later", frames=[later], onPage=_later)])

    word, col = rating(score)
    story = [NextPageTemplate("later")]
    if score is None:
        intro = "We could not work out an overall score for this website, but the notes below show what stood out."
    else:
        intro = (f"Your website scored <b>{score} out of 100</b>, which we would call <font color=\"{col}\"><b>{word}</b></font>. "
                 "The score is a simple guide to how easy your business is to find, trust and contact online.")
    story += [Paragraph(intro, S["lead"]), Spacer(1, 10)]

    top = ops[:3]
    counts = audit.get("priority_counts") or priority_counts(ops)
    pillars = audit.get("pillars") or build_pillars(audit)
    story += [
        Paragraph(
            f"Finding priorities: {counts.get('High', 0)} high, "
            f"{counts.get('Medium', 0)} medium, {counts.get('Low', 0)} low.",
            S["small"],
        ),
        Spacer(1, 8),
        Paragraph("Four pillars at a glance", S["h2"]),
    ]
    pillar_rows = []
    for index in range(0, min(len(pillars), 4), 2):
        row = []
        for pillar in pillars[index:index + 2]:
            row.append([
                Paragraph(esc(pillar.get("name")), S["card_t"]),
                Paragraph(esc(pillar.get("verdict")), S["small"]),
                Paragraph(f"<b>Evidence:</b> {esc(trim(pillar.get('evidence'), 150))}", S["small"]),
                Paragraph(f"<b>Next step:</b> {esc(trim(pillar.get('fix'), 150))}", S["small"]),
            ])
        if len(row) == 1:
            row.append("")
        pillar_rows.append(row)
    if pillar_rows:
        pillar_table = Table(pillar_rows, colWidths=[FW / 2, FW / 2], hAlign="LEFT")
        pillar_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F8F4")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(TRACK)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [pillar_table, Spacer(1, 10)]
    if top:
        story.append(Paragraph("Fix these first", S["h2"]))
        rows = []
        for i, op in enumerate(top, 1):
            first_line = trim(op.get("evidence"), 150)
            rows.append([Paragraph(f'<font name="{DISPLAY}" size="15" color="{GREEN}">{i}</font>', S["body"]),
                         [Paragraph(f'<b>{esc(op.get("title"))}</b>', S["body"]), Paragraph(esc(first_line), S["small"])]])
        t = Table(rows, colWidths=[10 * mm, FW - 10 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor(TRACK)),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [t, Spacer(1, 12)]

    cats = [(CATEGORY_WORDS.get(c.get("category"), c.get("category") or ""), c.get("score"), c.get("out_of")) for c in sc.get("categories", [])]
    if cats:
        story += [Paragraph("How you scored, area by area", S["h2"]), Bars(cats), Spacer(1, 8)]
        good = [lab for lab, s_, o_ in cats if s_ is not None and o_ and s_ / o_ >= 0.7]
        story.append(Paragraph("<b>What is already working.</b> " + (", ".join(g.lower() if i else g for i, g in enumerate(good)) + "."
                     if good else "Your website is live and reachable, which is a solid base to build on."), S["body"]))

    if ops:
        story += [PageBreak(), Paragraph("What we found and how to fix it", S["h1"]),
                  Paragraph("Listed from most to least important. You can do these yourself, or we can do them for you.", S["small"]), Spacer(1, 8)]
        story += [_card(o) for o in ops[:8]]
        if len(ops) > 8:
            story.append(Paragraph(f"Plus {len(ops) - 8} smaller improvements we are happy to go through with you.", S["small"]))

    story += _online_block(audit)
    pkg = audit.get("package_suggestion") or {}
    price = pkg.get("estimated_price_zar")
    lines = []
    if brand.get("show_price", True) and price is not None:
        lines.append(Paragraph(f"R{price:,.0f}".replace(",", " "), S["light_b"]))
        lines.append(Paragraph("once-off, to fix everything on this list", S["light"]))
    else:
        lines.append(Paragraph("We can fix everything on this list for you.", S["light"]))
    if brand.get("hosting"):
        lines.append(Spacer(1, 4))
        lines.append(Paragraph(f'Optional website hosting and care: R{brand["hosting"]} per month.', S["light"]))
    lines += [Spacer(1, 6), Paragraph(esc(brand.get("contact") or "Reply to the message this came with and we will book a short walkthrough."), S["light"])]
    box = Table([[lines]], colWidths=[FW])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(OBSIDIAN)), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(LIME)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 16), ("RIGHTPADDING", (0, 0), (-1, -1), 16),
                             ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 14)]))
    story += [Spacer(1, 8), Paragraph("Your next step", S["h2"]), box, Spacer(1, 8),
              Paragraph("This check looks only at what is publicly visible online on the day it was done. Scores are a guide, not a promise of results.", S["small"])]
    doc.build(story)
    return buf.getvalue()
