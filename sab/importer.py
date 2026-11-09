"""Bulk import of existing project files (letters, notes, drawings, MoMs, IPCs ...).

Each file is copied into the upload store and registered as either a Letter or a
Document. The register, document type, date, letter number and sender are guessed
from the file name and, for PDF / Word files, from the text inside the file. The
extracted text is stored on the record, so the global search also finds words
*inside* the documents. Files already imported (same content) are skipped.
"""
import hashlib
import os
import re
import shutil
import zipfile
from datetime import datetime

from . import db as store

SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "icon\r"}
MAX_TEXT = 20000  # characters of extracted text kept per file

LETTER_WORDS = re.compile(r"\b(letter|ltr|notice|reply|response|reminder|clarification|communication|dated)", re.I)
NOTE_SHEET = re.compile(r"(^|/\s*)note\b", re.I)
TO_PARTY = [
    (r"\bto (the )?(contractor|concessionaire|epc)\b", "Contractor"),
    (r"\bto (the )?(ae|ie|authority'?s engineer|independent engineer)\b", "Authority's Engineer"),
    (r"\bto (the )?(nhai|piu|ro|authority|hq|employer)\b", "Authority / Employer"),
]

DOC_TYPE_RULES = [
    (r"(^|/\s*)note\b", "Note Sheet"),
    (r"\b(drawing|dwg|gad|plan|profile|cross[ -]?section|l[- ]?section)\b|\.(dwg|dxf)$", "Drawing"),
    (r"\b(mom|minutes|meeting)\b", "Minutes of Meeting"),
    (r"\b(ipc|invoice|bill|payment|ra[ -]?bill)\b", "IPC / Invoice"),
    (r"\b(eot|extension of time)\b", "EOT"),
    (r"\b(cos|variation|change of scope)\b", "Variation / COS"),
    (r"\b(bg|bank guarantee|guarantee)\b", "Bank Guarantee"),
    (r"\b(insurance|policy|car policy)\b", "Insurance"),
    (r"\b(test|qc|qa|lab|cube|core|ndt)\b", "Test Report"),
    (r"\b(method statement|ms)\b", "Method Statement"),
    (r"\b(design|report|dbr|calculation)\b", "Design Report"),
    (r"\b(agreement|contract|schedule|gcc|pcc|rfp)\b", "Contract Agreement"),
    (r"\b(approval|approved|sanction)\b", "Approval"),
    (r"\.(jpe?g|png|heic)$", "Photograph"),
]

LETTER_CATEGORY_RULES = [
    (r"\b(eot|extension of time)\b", "EOT (Extension of Time)"),
    (r"\b(ipc|bill|payment|deduction)\b", "Payment / IPC"),
    (r"\b(cos|variation|change of scope)\b", "Variation / Change of Scope"),
    (r"\b(claim)\b", "Claim"),
    (r"\b(row|land|acquisition|encroachment)\b", "Land / ROW"),
    (r"\b(utility|powergrid|electric|ht line|pipeline|bsnl|jal)\b", "Utility Shifting"),
    (r"\b(forest|tree|environment)\b", "Forest / Environment"),
    (r"\b(drawing|design|gad)\b", "Design / Drawings"),
    (r"\b(quality|test|ncr|qc)\b", "Quality / QA-QC"),
    (r"\b(safety|accident|hse)\b", "Safety / HSE"),
    (r"\b(mom|minutes|meeting)\b", "Meeting / MoM"),
    (r"\b(risk and cost|risk & cost|termination|show cause|damages|ld)\b", "Contractual"),
]

SENDER_RULES = [
    (r"\b(ae|ie|authority'?s engineer|independent engineer)\b", "Authority's Engineer"),
    (r"\b(nhai|piu|authority|employer|ro\b|ministry|morth)\b", "Authority / Employer"),
    (r"\b(contractor|concessionaire|epc contractor)\b", "Contractor"),
]

DATE_PATTERNS = [
    (re.compile(r"\b(20\d\d)[-._/](\d{1,2})[-._/](\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[-._/](\d{1,2})[-._/](20\d\d)\b"), ("d", "m", "y")),
]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TEXT_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s.-]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s.,-]*(20\d\d)\b", re.I)
LETTER_NO = [
    re.compile(r"(?:letter|ltr|ref)\.?\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-._]{0,60}\d)", re.I),
    re.compile(r"\bltr\s*(\d[\w/\-]*)", re.I),
    re.compile(r"^\s*(\d{2,6})\s+dated\b", re.I),
]


def _norm(s):
    return re.sub(r"[_\-]+", " ", s or "")


def _match(rules, text, default=None):
    for pattern, value in rules:
        if re.search(pattern, text, re.I):
            return value
    return default


def find_date(text):
    for rx, order in DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            parts = dict(zip(order, m.groups()))
            y = int(parts["y"]) if "y" in parts else 2000 + int(parts["yy"])
            try:
                return datetime(y, int(parts["m"]), int(parts["d"])).date().isoformat()
            except ValueError:
                continue
    m = TEXT_DATE.search(text or "")
    if m:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1))).date().isoformat()
        except ValueError:
            pass
    return None


def extract_text(path, ext=None):
    """Best-effort text extraction from PDF, Word and plain-text files."""
    ext = (ext or path.rsplit(".", 1)[-1]).lower()
    try:
        if ext == "pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return ""
            reader = PdfReader(path)
            out = []
            for page in reader.pages[:15]:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) > MAX_TEXT:
                    break
            return "\n".join(out)[:MAX_TEXT]
        if ext == "docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)[:MAX_TEXT]
        if ext in ("txt", "csv", "eml"):
            with open(path, encoding="utf8", errors="ignore") as f:
                return f.read(MAX_TEXT)
    except Exception:  # damaged / encrypted / scanned file: keep the file, skip the text
        return ""
    return ""


def classify(filename, text, force=None):
    """Return (module, data-dict) for a file."""
    stem = filename.rsplit(".", 1)[0]
    name = _norm(stem)
    short = _norm(stem.replace("\\", "/").split("/")[-1]).strip()
    head = (text or "")[:3000]
    both = name + "\n" + head
    looks_letter = bool(LETTER_WORDS.search(short)) and not NOTE_SHEET.search(short)
    is_letter = force == "letters" or (force != "documents" and looks_letter)
    date = find_date(stem) or find_date(head)
    if is_letter:
        m = next((m for rx in LETTER_NO for src in (short, head) if (m := rx.search(src))), None)
        ref = m.group(1).strip() if m else short
        to_party = _match(TO_PARTY, short)
        outgoing = bool(to_party) or bool(re.search(r"\bdraft\b", short, re.I))
        subj = re.search(r"\b(?:sub(?:ject)?)\s*[:.\-]\s*(.{5,200})", head, re.I)
        return "letters", {
            "ref_no": ref[:80],
            "letter_date": date,
            "direction": "Outgoing" if outgoing else "Incoming",
            "from_party": None if outgoing else _match(SENDER_RULES, both),
            "to_party": to_party,
            "subject": (subj.group(1).strip() if subj else short)[:250],
            "category": _match(LETTER_CATEGORY_RULES, both, "General"),
            "body": text or None,
            "reply_required": "No",
            "status": "For Information",
        }
    return "documents", {
        "doc_no": None,
        "title": short[:250],
        "doc_type": _match(DOC_TYPE_RULES, filename) or _match(DOC_TYPE_RULES, both, "Other"),
        "doc_date": date,
        "description": text or None,
    }


def _ensure_table():
    db = store.get_db()
    db.execute("CREATE TABLE IF NOT EXISTS imported_files (sha256 TEXT PRIMARY KEY, module TEXT, "
                "record_id INTEGER, original_name TEXT, imported_at TEXT)")
    return db


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_file(src_path, original_name, upload_folder, allowed_ext, force=None):
    """Import one file already on disk. Returns a result dict."""
    base = os.path.basename(original_name.replace("\\", "/"))
    res = {"name": original_name, "status": "skipped", "module": None, "id": None, "note": ""}
    if base.lower() in SKIP_NAMES or base.startswith("._") or base.startswith("~$"):
        res["note"] = "system file"
        return res
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if ext not in allowed_ext:
        res["note"] = f".{ext or '?'} files are not supported"
        return res
    db = _ensure_table()
    digest = _sha256(src_path)
    dup = db.execute("SELECT * FROM imported_files WHERE sha256 = ?", (digest,)).fetchone()
    if dup and store.get_record(dup["module"], dup["record_id"]) is not None:
        res.update(note="already imported", module=dup["module"], id=dup["record_id"])
        return res
    from werkzeug.utils import secure_filename
    stored = datetime.now().strftime("%Y%m%d%H%M%S_") + digest[:6] + "_" + (secure_filename(base) or f"file.{ext}")
    shutil.copyfile(src_path, os.path.join(upload_folder, stored))
    text = extract_text(src_path, ext)
    module, data = classify(original_name.replace("\\", "/").replace("/", " / "), text, force)
    data["attachment"] = stored
    if module == "letters" and data.get("direction") == "Outgoing":
        data["from_party"] = store.get_settings().get("our_role")
    rid = store.insert_record(module, data)
    db.execute("INSERT OR REPLACE INTO imported_files VALUES (?, ?, ?, ?, ?)",
               (digest, module, rid, original_name, store.now()))
    db.commit()
    res.update(status="imported", module=module, id=rid,
               note="text extracted" if text.strip() else ("no text (scanned?)" if ext == "pdf" else ""))
    return res


def attachment_path(value, upload_folder):
    """Absolute path of a record's attachment: a copied upload or a file in a connected folder."""
    if not value:
        return None
    if value.startswith("watch:"):
        from . import sync
        try:
            return sync.watched_path(int(value.split(":")[1]))
        except (ValueError, IndexError):
            return None
    return os.path.join(upload_folder, value)
