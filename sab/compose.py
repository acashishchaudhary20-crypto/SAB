"""Compose Note Sheets and Letters on demand from the project's own records.

You describe what you need ("Note for approval of EOT-2 to the contractor for ROW
delay at Ch 12+300", "Letter to AE asking compliance of our letter 702 within 7
days"). The composer:

  1. finds the relevant records in the system (letters, documents / note sheets,
     DRB disputes, MPRs) by matching your request against their reference numbers,
     subjects and the text extracted from the files;
  2. lets you tick / untick the sources to use;
  3. drafts the note or letter in your office format, grounded only in those
     sources: Claude reads the stored text and, for scanned PDFs, the PDF itself.
     Your own earlier notes / outgoing letters are given as style examples.

Without an API key, a correctly structured template is produced with the sources
listed so you can complete it by hand.
"""
import base64
import os
import re
from datetime import date

from . import db as store

KINDS = {
    "note": "Note Sheet (internal noting for approval / information)",
    "letter": "Letter (outgoing)",
    "reply": "Reply to a letter",
    "notice": "Notice to Contractor (e.g. show-cause, cure period, risk & cost, LD)",
    "office_order": "Office Memorandum / Office Order",
    "agenda": "Meeting Agenda / Minutes of Meeting",
    "summary": "Brief / Status Summary for higher office (RO / HQ)",
}

STOP = set("""a an the and or of to for in on at by with from is are be as this that these those it its into our your
their please kindly regarding about under vide dated note letter prepare make draft write create give send reply
regard sir madam ref reference subject sub any all also etc per has have had was were will shall may can not""".split())

SOURCES = {
    # module: (title fields, text fields, label)
    "letters": (["ref_no", "subject"], ["body", "category", "from_party", "to_party", "ref_letters"], "Letter"),
    "documents": (["doc_no", "title"], ["description", "doc_type"], "Document"),
    "drb": (["dispute_no", "title"], ["facts", "claimant_case", "our_position", "drb_recommendation",
                                      "contract_clauses", "related_letters"], "DRB Dispute"),
    "mpr": (["month"], ["key_achievements", "issues", "ipc_status", "next_month_plan"], "MPR"),
}

MAX_SOURCE_CHARS = 25000          # per record
MAX_TOTAL_CHARS = 400000          # all sources together
MAX_PDF_BYTES = 8 * 1024 * 1024   # per PDF sent for reading
MAX_PDF_TOTAL = 24 * 1024 * 1024


def terms(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9/\-+.]*[A-Za-z0-9]|\d", text or "")
    out = []
    for w in words:
        lw = w.lower()
        if lw in STOP or (len(lw) < 3 and not lw.isdigit()):
            continue
        if lw not in out:
            out.append(lw)
    return out


DATE_FIELD = {"letters": "letter_date", "documents": "doc_date", "drb": "notice_date", "mpr": "month"}


def _date_of(module, row):
    return row[DATE_FIELD[module]] or ""


def _dmy(d):
    if d and len(d) == 10 and d[4] == "-":
        return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"
    return d or "?"


def describe(module, row):
    if module == "letters":
        return f"Letter {row['ref_no']} dt {_dmy(row['letter_date'])} ({row['from_party'] or '?'} → {row['to_party'] or '?'}): {row['subject'] or ''}"
    if module == "documents":
        return f"{row['doc_type'] or 'Document'} {row['doc_no'] or ''} {row['title'] or ''} dt {_dmy(row['doc_date'])}".replace("  ", " ")
    if module == "drb":
        return f"DRB {row['dispute_no']}: {row['title']} ({row['status'] or ''})"
    return f"MPR {row['month']}"


def find_sources(request_text, limit=15):
    """Rank all records by how well they match the request. Returns list of dicts."""
    ts = terms(request_text)
    if not ts:
        return []
    db = store.get_db()
    scored = []
    for module, (title_f, text_f, _label) in SOURCES.items():
        for row in db.execute(f'SELECT * FROM "{module}"'):
            title = " ".join(str(row[f] or "") for f in title_f).lower()
            body = " ".join(str(row[f] or "") for f in text_f).lower()
            score = 0.0
            for t in ts:
                if t in title:
                    score += 5 if any(ch.isdigit() for ch in t) else 3
                if t in body:
                    score += 1 + min(body.count(t), 5) * 0.2
            if score > 0:
                scored.append({"module": module, "id": row["id"], "score": round(score, 1),
                               "label": describe(module, row), "date": _date_of(module, row)})
    scored.sort(key=lambda r: (-r["score"], r["date"]))
    return scored[:limit]


def style_examples(kind, limit=2):
    """Earlier notes / outgoing letters written by the office, used to copy format and tone."""
    db = store.get_db()
    if kind in ("note", "office_order", "summary", "agenda"):
        rows = db.execute("SELECT title AS t, description AS x FROM documents WHERE doc_type = 'Note Sheet' "
                          "AND length(description) > 300 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = db.execute("SELECT subject AS t, body AS x FROM letters WHERE direction = 'Outgoing' "
                          "AND length(body) > 300 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [(r["t"], r["x"][:6000]) for r in rows]


def _record_block(module, row):
    skip = {"attachment", "created_at", "updated_at", "id"}
    lines = [f"{k}: {row[k]}" for k in row.keys() if k not in skip and row[k] not in (None, "")]
    return "\n".join(lines)[:MAX_SOURCE_CHARS]


def build_content(kind, request_text, selected, settings, upload_folder, extra_points="", addressee=""):
    """Build the user content (text + PDF blocks) for Claude."""
    parts, pdf_blocks, pdf_total, total = [], [], 0, 0
    parts.append("PROJECT DETAILS\n" + "\n".join(f"{k}: {v}" for k, v in settings.items() if v))
    parts.append(f"\nWE ARE: {settings.get('our_role')}  (the document is written on our behalf)")
    parts.append(f"\nDOCUMENT TO PREPARE: {KINDS.get(kind, kind)}")
    if addressee:
        parts.append(f"ADDRESSED TO / PUT UP TO: {addressee}")
    parts.append(f"\nWHAT THE USER WANTS:\n{request_text}")
    if extra_points:
        parts.append(f"\nPOINTS THAT MUST BE COVERED:\n{extra_points}")
    examples = style_examples(kind)
    if examples:
        parts.append("\nSTYLE EXAMPLES - earlier documents of our office. Follow their format, headings, numbering, "
                     "tone and sign-off; do NOT copy their facts:")
        for i, (t, x) in enumerate(examples, 1):
            parts.append(f"--- Example {i}: {t}\n{x}")
    parts.append(f"\nSOURCE RECORDS ({len(selected)}) - use only these facts:")
    for n, (module, row) in enumerate(selected, 1):
        block = _record_block(module, row)
        if total + len(block) > MAX_TOTAL_CHARS:
            block = block[: max(0, MAX_TOTAL_CHARS - total)] + "\n[... truncated ...]"
        total += len(block)
        parts.append(f"\n=== Source {n}: {describe(module, row)} ===\n{block}")
        att = row["attachment"] if "attachment" in row.keys() else None
        text_len = len((row["body"] if module == "letters" else row["description"] if module == "documents" else "") or "")
        if att and att.lower().endswith(".pdf") and text_len < 200:
            from .importer import attachment_path
            path = attachment_path(att, upload_folder)
            if path and os.path.isfile(path):
                size = os.path.getsize(path)
                if size <= MAX_PDF_BYTES and pdf_total + size <= MAX_PDF_TOTAL:
                    pdf_total += size
                    with open(path, "rb") as f:
                        data = base64.standard_b64encode(f.read()).decode()
                    pdf_blocks.append({"type": "document", "title": f"Source {n}: {describe(module, row)}"[:200],
                                       "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
                    parts.append(f"(The scanned PDF of Source {n} is attached - read it.)")
    parts.append(f"\nToday's date: {date.today().strftime('%d.%m.%Y')}")
    return pdf_blocks + [{"type": "text", "text": "\n".join(parts)}]


SYSTEM = """You are the senior technical and contracts officer of the project office described in PROJECT DETAILS - an Indian national-highway project (for example an NHAI PIU, the Authority's Engineer, or the EPC Contractor, as stated in "WE ARE"). You prepare office Note Sheets, letters, notices and briefs for the user, exactly as an experienced officer in that office would.

Rules:
- Use ONLY facts found in the SOURCE RECORDS (and attached PDFs) and in the user's request. Quote letter numbers and dates exactly as they appear. Never invent a fact, date, amount, chainage, clause or letter number. Where something needed is missing, write a clear placeholder in square brackets, e.g. [date of handover], [Clause __].
- Follow the format, headings, numbering and sign-off of the STYLE EXAMPLES when given; otherwise use standard Government of India / NHAI office practice:
  * Note Sheet: heading "NOTE" / "Sub:" line / "Ref:" list in chronological order / numbered paragraphs - background, chronology of correspondence, facts and present status, relevant contract provisions, examination / analysis, financial or time implication, recommendation - ending with "Submitted for kind perusal and approval please." (or "for information") followed by the signature block and "Put up to:".
  * Letter / Notice: letter no. and date line, addressee block, "Sub:", "Ref:" list, "Sir,", numbered paragraphs, clear request with a time-limit, reservation of rights where relevant, "Yours faithfully," signature block, "Copy to:".
- Be precise and concise; one idea per paragraph; refer to sources as "letter no. ... dated ..." in the text.
- Protect our side's contractual position, but state facts fairly so the note can withstand audit / DRB scrutiny.
- After the document, add a short section headed "SOURCES USED" listing each source you relied on, and "TO VERIFY" listing any placeholder or assumption the user must check.
Output plain text only (no markdown symbols such as ** or #)."""


def template(kind, request_text, selected, settings, addressee="", extra_points=""):
    today = date.today().strftime("%d.%m.%Y")
    refs = [f"{i}. {describe(m, r)}" for i, (m, r) in enumerate(selected, 1)]
    sign = f"\n({settings.get('signatory_name') or '[Name]'})\n{settings.get('signatory_designation') or '[Designation]'}"
    subject = request_text.strip().split("\n")[0][:200]
    if kind in ("note", "summary", "office_order", "agenda"):
        lines = [
            settings.get("project_name", ""), "", "NOTE", "",
            f"Sub: {subject}", "", "Ref:", *(refs or ["1. [ ]"]), "",
            "1. Background:", "   [Brief background of the matter.]", "",
            "2. Chronology of correspondence:",
            *[f"   ({chr(97 + i)}) {describe(m, r)}" for i, (m, r) in enumerate(selected)], "",
            "3. Present status / facts:", "   [ ]", "",
            f"4. Relevant provisions of the {settings.get('contract_form') or 'Contract'}:", "   [Clause __ - ]", "",
            "5. Examination:", "   [ ]", *([f"   Points: {extra_points}"] if extra_points else []), "",
            "6. Recommendation:", "   [ ]", "",
            "Submitted for kind perusal and approval please.", sign, f"Date: {today}", "",
            f"Put up to: {addressee or '[ ]'}",
        ]
    else:
        lines = [
            f"Letter No.: {settings.get('project_name', '')}/____/____", f"Date: {today}", "",
            "To,", addressee or "[Addressee]", "",
            f"Sub: {settings.get('project_name', '')} - {subject}", "", "Ref:", *(refs or ["1. [ ]"]), "",
            "Sir,", "",
            "1. Kindly refer to the above-referred letters.", "",
            "2. [Facts / present status.]", "",
            *([f"3. {extra_points}", ""] if extra_points else ["3. [Our position with contract provisions.]", ""]),
            "4. [Specific request / direction with time-limit.]", "",
            "Yours faithfully,", sign, "", "Copy to: [ ]",
        ]
    return "\n".join(lines)


def to_docx(text, path):
    """Write the composed text to a Word file in a plain office format."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    for line in text.split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(line)
        stripped = line.strip()
        if stripped.upper() in ("NOTE", "SOURCES USED", "TO VERIFY") or re.match(r"^(sub(ject)?)\s*[:.]", stripped, re.I):
            run.bold = True
    doc.save(path)
