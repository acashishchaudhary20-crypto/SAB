"""SAB Assistant: give a task in plain words, Claude carries it out using the project's records.

Claude works through tools on the local database: it searches all letters, note sheets,
documents (including files in connected Desktop / OneDrive / Google Drive folders), DRB
disputes, MPRs and DPRs; reads records and original files (including scanned PDFs);
and can save notes and draft letters, update letters, add DPR entries and DRB
proceedings, and start a folder sync. Every change it makes is listed in the reply.
"""
import base64
import json
import mimetypes
import os
import threading
import uuid
from datetime import date

from flask import current_app

from . import compose
from . import db as store
from . import importer
from . import sync
from .modules import ACTIVITIES, DRB_ENTRY_TYPES, LETTER_CATEGORIES, MODULES, PARTIES, UNITS

MODEL = os.environ.get("SAB_CLAUDE_MODEL", "claude-opus-5-5")
MAX_STEPS = 20
MAX_TEXT = 60000
MAX_FILE_BYTES = 20 * 1024 * 1024

_conversations = {}  # conversation id -> list of messages (kept in memory while SAB runs)
_conv_lock = threading.Lock()

REGISTERS = ["letters", "documents", "drb", "mpr", "dpr"]
LETTER_FIELDS = ["status", "reply_required", "reply_due", "category", "direction", "from_party", "to_party",
                 "subject", "ref_letters", "ref_no", "letter_date"]

TOOLS = [
    {"name": "search_records",
     "description": "Search every record in the project system (letters, note sheets and other documents incl. files "
                    "from connected Desktop/OneDrive/Google Drive folders, DRB disputes, MPRs, DPRs) by keywords, "
                    "letter numbers, chainages, names. Matches subjects, reference numbers AND the text inside files. "
                    "Use several focused searches rather than one long query.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Keywords, e.g. 'EOT 1056 ROW 12+300'"},
         "register": {"type": "string", "enum": REGISTERS, "description": "Optional: limit to one register"},
         "limit": {"type": "integer", "description": "Max results (default 12, max 40)"}},
         "required": ["query"]}},
    {"name": "read_record",
     "description": "Read the full details and stored text of one record found by search_records or a list tool.",
     "input_schema": {"type": "object", "properties": {
         "register": {"type": "string", "enum": REGISTERS}, "id": {"type": "integer"}},
         "required": ["register", "id"]}},
    {"name": "read_original_file",
     "description": "Open the original attached file of a record (PDF incl. scanned PDFs, image, Word, text) when the "
                    "stored text is missing, short or you need exact wording, tables or signatures.",
     "input_schema": {"type": "object", "properties": {
         "register": {"type": "string", "enum": REGISTERS}, "id": {"type": "integer"}},
         "required": ["register", "id"]}},
    {"name": "list_letters",
     "description": "List letters with filters, newest first. Use for questions like 'which letters are pending reply', "
                    "'letters from IE in October', 'overdue replies'.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["Draft", "Open", "Replied", "Closed", "For Information"]},
         "reply_required": {"type": "string", "enum": ["Yes", "No"]},
         "overdue_only": {"type": "boolean"},
         "direction": {"type": "string", "enum": ["Incoming", "Outgoing"]},
         "from_party": {"type": "string", "enum": PARTIES},
         "from_date": {"type": "string", "description": "YYYY-MM-DD"},
         "to_date": {"type": "string", "description": "YYYY-MM-DD"},
         "limit": {"type": "integer"}}}},
    {"name": "list_disputes", "description": "List all DRB disputes with status, amounts and next dates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "project_overview",
     "description": "Project settings and a snapshot: counts per register, overdue replies, active disputes, latest MPR "
                    "and DPR date, connected folders and last sync.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "dpr_summary",
     "description": "Activity-wise planned vs achieved quantities, rain days, manpower and hindrances from DPR entries "
                    "between two dates (inclusive).",
     "input_schema": {"type": "object", "properties": {
         "from_date": {"type": "string", "description": "YYYY-MM-DD"}, "to_date": {"type": "string", "description": "YYYY-MM-DD"}},
         "required": ["from_date", "to_date"]}},
    {"name": "save_note",
     "description": "Save a finished Note Sheet / office note / brief / MoM in the Document Repository. Use after "
                    "writing the full text. Returns the link.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "content": {"type": "string", "description": "Complete note text (plain text)"}},
         "required": ["title", "content"]}},
    {"name": "save_letter_draft",
     "description": "Save a finished outgoing letter / notice / reply as a Draft in Correspondence. Returns the link.",
     "input_schema": {"type": "object", "properties": {
         "subject": {"type": "string"}, "content": {"type": "string", "description": "Complete letter text"},
         "to_party": {"type": "string", "enum": PARTIES},
         "ref_letters": {"type": "string", "description": "Comma-separated ref nos. of letters this one refers to"},
         "category": {"type": "string", "enum": LETTER_CATEGORIES}},
         "required": ["subject", "content"]}},
    {"name": "update_letter",
     "description": "Change fields of an existing letter, e.g. mark reply required with a due date, set status Replied, "
                    "fix subject / category / direction / parties. Only when the user asked for it.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "integer"},
         "changes": {"type": "object", "description": f"Field -> new value. Allowed fields: {', '.join(LETTER_FIELDS)}. "
                                                      "Dates YYYY-MM-DD; reply_required Yes/No."}},
         "required": ["id", "changes"]}},
    {"name": "add_dpr_entry",
     "description": "Add one Daily Progress Report line (one activity at one chainage stretch) when the user gives "
                    "progress figures.",
     "input_schema": {"type": "object", "properties": {
         "report_date": {"type": "string", "description": "YYYY-MM-DD"},
         "activity": {"type": "string", "enum": ACTIVITIES}, "unit": {"type": "string", "enum": UNITS},
         "qty_planned": {"type": "number"}, "qty_achieved": {"type": "number"},
         "chainage_from": {"type": "string"}, "chainage_to": {"type": "string"},
         "side": {"type": "string", "enum": ["LHS", "RHS", "Both", "Median", "NA"]},
         "manpower": {"type": "number"}, "machinery": {"type": "string"},
         "weather": {"type": "string", "enum": ["Clear", "Cloudy", "Rain", "Heavy Rain", "Fog", "Extreme Heat"]},
         "hindrances": {"type": "string"}, "remarks": {"type": "string"}},
         "required": ["report_date", "activity"]}},
    {"name": "add_drb_proceeding",
     "description": "Add an event to a DRB dispute's timeline (filing, hearing, recommendation...).",
     "input_schema": {"type": "object", "properties": {
         "dispute_id": {"type": "integer"}, "entry_date": {"type": "string", "description": "YYYY-MM-DD"},
         "entry_type": {"type": "string", "enum": DRB_ENTRY_TYPES},
         "party": {"type": "string", "enum": PARTIES + ["DRB"]}, "summary": {"type": "string"}},
         "required": ["dispute_id", "entry_date", "entry_type"]}},
    {"name": "sync_folders",
     "description": "Start re-scanning the connected Desktop / OneDrive / Google Drive folders now, to pick up files "
                    "added in the last few minutes. Runs in the background.",
     "input_schema": {"type": "object", "properties": {}}},
]


def system_prompt(settings):
    details = "\n".join(f"- {k}: {v}" for k, v in settings.items() if v)
    return f"""You are the SAB Assistant: the personal project-office assistant of the user, who works on the highway project below. Today is {date.today().strftime('%d.%m.%Y (%A)')}.

PROJECT
{details}
The user is on the side of: {settings.get('our_role')}.

The project system holds all their letters, note sheets, documents (including every file in their connected Desktop / OneDrive / Google Drive folders), DRB disputes, MPRs and DPRs. You work only through the tools.

HOW TO WORK
- For any question or task, first find the facts: run focused searches (letter numbers, subjects, chainages, names), read the relevant records, and open original files when the stored text is missing or you need exact wording. Search again with different words if results are thin.
- Base every statement on the records. Quote letter numbers and dates exactly. Never invent a fact, date, amount, chainage, clause or letter number; if it is not in the records, say so or leave a [placeholder] in drafts.
- When asked to prepare a note, letter, notice, reply, brief, MoM or summary: gather the facts, write the complete document in the office format used in the user's own earlier notes/letters (search for them as examples) or standard Government of India / NHAI practice, then SAVE it with save_note or save_letter_draft, and finish with a short summary plus the list of sources used and points to verify.
- Change records (update_letter, add_dpr_entry, add_drb_proceeding) only when the user asked for it. Report every change.
- If the request is ambiguous in a way that changes the result (e.g. which of two disputes), ask one short question instead of guessing.
- Reply in the user's language (English or Hindi), concise, plain text without markdown symbols. Refer to records as e.g. "letter no. 1056 dt 14.09.2026"."""


# ----------------------------------------------------------------------------- tool implementations

def _dmy(d):
    return compose._dmy(d)


def _label(module, row):
    if module == "dpr":
        return f"DPR {_dmy(row['report_date'])} {row['activity']} Ch {row['chainage_from'] or ''}-{row['chainage_to'] or ''}"
    return compose.describe(module, row)


def _row_text(module, row, limit=MAX_TEXT):
    skip = {"created_at", "updated_at"}
    out = [f"register: {module}", f"id: {row['id']}"]
    for k in row.keys():
        if k in skip or k == "id" or row[k] in (None, ""):
            continue
        v = row[k]
        if k == "attachment":
            v = str(v).split(":", 2)[-1] if str(v).startswith("watch:") else str(v).split("_", 1)[-1]
            k = "attached_file"
        out.append(f"{k}: {v}")
    text = "\n".join(out)
    return text[:limit] + ("\n[... truncated ...]" if len(text) > limit else "")


def _url(module, rid):
    return f"/m/{module}/{rid}"


def t_search_records(query, register=None, limit=12):
    limit = max(1, min(int(limit or 12), 40))
    hits = compose.find_sources(query, limit=200)
    if register and register != "dpr":
        hits = [h for h in hits if h["module"] == register]
    out = []
    for h in hits[:limit]:
        row = store.get_record(h["module"], h["id"])
        text = (row["body"] if h["module"] == "letters" else row["description"] if h["module"] == "documents" else "") or ""
        out.append({"register": h["module"], "id": h["id"], "record": h["label"], "score": h["score"],
                    "text_preview": " ".join(text.split())[:300]})
    if register in (None, "dpr"):
        ts = compose.terms(query)
        if ts:
            for row in store.get_db().execute("SELECT * FROM dpr ORDER BY report_date DESC LIMIT 2000"):
                blob = " ".join(str(row[k] or "") for k in row.keys()).lower()
                score = sum(1 for t in ts if t in blob)
                if score and len(out) < limit + 10:
                    out.append({"register": "dpr", "id": row["id"], "record": _label("dpr", row), "score": score})
    if not out:
        return "No matching records. Try other keywords, a letter number, or a broader word."
    return json.dumps(out, ensure_ascii=False)


def _get(register, rid):
    if register not in REGISTERS:
        raise ValueError(f"Unknown register {register}")
    row = store.get_record(register, int(rid))
    if row is None:
        raise ValueError(f"No {register} record with id {rid}")
    return row


def t_read_record(register, id):
    row = _get(register, id)
    text = _row_text(register, row)
    stored = (row["body"] if register == "letters" else row["description"] if register == "documents" else "x") or ""
    att = row["attachment"] if "attachment" in row.keys() else None
    if att and len(stored.strip()) < 200:
        text += "\n(note: little or no stored text - use read_original_file to read the attached file)"
    if register == "drb":
        entries = store.get_db().execute("SELECT * FROM drb_entries WHERE dispute_id = ? ORDER BY entry_date", (id,)).fetchall()
        if entries:
            text += "\n\nPROCEEDINGS:\n" + "\n".join(
                f"- {_dmy(e['entry_date'])} {e['entry_type']} ({e['party'] or ''}): {e['summary'] or ''}" for e in entries)
    return text


def t_read_original_file(register, id):
    row = _get(register, id)
    att = row["attachment"] if "attachment" in row.keys() else None
    if not att:
        return "This record has no attached file."
    path = importer.attachment_path(att, current_app.config["UPLOAD_FOLDER"])
    if not path or not os.path.isfile(path):
        return "The attached file is no longer available (moved or deleted)."
    size = os.path.getsize(path)
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        if size > MAX_FILE_BYTES:
            return f"PDF is too large to open here ({size // 1048576} MB). Stored text:\n" + t_read_record(register, id)
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return [{"type": "document", "title": os.path.basename(path)[:200],
                 "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                {"type": "text", "text": f"Original file of {_label(register, row)}"}]
    if ext in ("jpg", "jpeg", "png") and size <= 5 * 1024 * 1024:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        mt = mimetypes.guess_type(path)[0] or "image/jpeg"
        return [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
                {"type": "text", "text": f"Image file of {_label(register, row)}"}]
    text = importer.extract_text(path, ext)
    return text[:MAX_TEXT] if text.strip() else f"Cannot read text from this .{ext} file."


def t_list_letters(status=None, reply_required=None, overdue_only=False, direction=None, from_party=None,
                   from_date=None, to_date=None, limit=30):
    where, params = [], []
    for col, val in (("status", status), ("reply_required", reply_required), ("direction", direction),
                     ("from_party", from_party)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if from_date:
        where.append("letter_date >= ?")
        params.append(from_date)
    if to_date:
        where.append("letter_date <= ?")
        params.append(to_date)
    if overdue_only:
        where.append("reply_required = 'Yes' AND status = 'Open' AND reply_due < ?")
        params.append(date.today().isoformat())
    sql = "SELECT * FROM letters" + (" WHERE " + " AND ".join(where) if where else "") + \
          f" ORDER BY letter_date DESC LIMIT {max(1, min(int(limit or 30), 100))}"
    rows = store.get_db().execute(sql, params).fetchall()
    return json.dumps([{"id": r["id"], "ref_no": r["ref_no"], "date": _dmy(r["letter_date"]), "direction": r["direction"],
                        "from": r["from_party"], "to": r["to_party"], "subject": r["subject"], "status": r["status"],
                        "reply_required": r["reply_required"], "reply_due": _dmy(r["reply_due"]) if r["reply_due"] else None}
                       for r in rows], ensure_ascii=False) if rows else "No letters match."


def t_list_disputes():
    rows = store.list_records("drb")
    return json.dumps([{"id": r["id"], "dispute_no": r["dispute_no"], "title": r["title"], "claimant": r["claimant"],
                        "status": r["status"], "amount_claimed_cr": r["amount_claimed"], "eot_days": r["eot_claimed_days"],
                        "next_hearing": _dmy(r["next_hearing"]) if r["next_hearing"] else None} for r in rows],
                      ensure_ascii=False) if rows else "No DRB disputes recorded."


def t_project_overview():
    db = store.get_db()
    counts = {m: db.execute(f'SELECT COUNT(*) FROM "{m}"').fetchone()[0] for m in REGISTERS}
    overdue = db.execute("SELECT COUNT(*) FROM letters WHERE reply_required='Yes' AND status='Open' AND reply_due < ?",
                         (date.today().isoformat(),)).fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM letters WHERE reply_required='Yes' AND status='Open'").fetchone()[0]
    sync.ensure_tables(db)
    flds = [{"folder": f["label"], "path": f["path"], "files": f["file_count"], "last_sync": f["last_sync"]}
            for f in sync.folders(enabled_only=True)]
    last_mpr = db.execute("SELECT month, physical_actual, physical_planned FROM mpr ORDER BY month DESC LIMIT 1").fetchone()
    return json.dumps({"settings": store.get_settings(), "record_counts": counts, "letters_pending_reply": pending,
                       "overdue_replies": overdue, "latest_dpr_date": db.execute("SELECT MAX(report_date) FROM dpr").fetchone()[0],
                       "latest_mpr": dict(last_mpr) if last_mpr else None, "connected_folders": flds,
                       "sync_status": {k: sync.status[k] for k in ("running", "last_end", "added", "updated", "errors")}},
                      ensure_ascii=False, default=str)


def t_dpr_summary(from_date, to_date):
    db = store.get_db()
    rows = db.execute("SELECT activity, unit, SUM(qty_planned) p, SUM(qty_achieved) a, COUNT(*) n FROM dpr "
                      "WHERE report_date BETWEEN ? AND ? GROUP BY activity, unit ORDER BY activity", (from_date, to_date)).fetchall()
    days = db.execute("SELECT COUNT(DISTINCT report_date) d, COUNT(DISTINCT CASE WHEN weather IN ('Rain','Heavy Rain') "
                      "THEN report_date END) r, AVG(manpower) m FROM dpr WHERE report_date BETWEEN ? AND ?",
                      (from_date, to_date)).fetchone()
    hind = db.execute("SELECT report_date, chainage_from, chainage_to, activity, hindrances FROM dpr WHERE report_date "
                      "BETWEEN ? AND ? AND hindrances IS NOT NULL AND hindrances != '' ORDER BY report_date",
                      (from_date, to_date)).fetchall()
    return json.dumps({"days_reported": days["d"], "rain_days": days["r"], "avg_manpower": round(days["m"] or 0),
                       "activities": [{"activity": r["activity"], "unit": r["unit"], "planned": r["p"], "achieved": r["a"],
                                       "entries": r["n"]} for r in rows],
                       "hindrances": [dict(h) for h in hind][:100]}, ensure_ascii=False)


def t_save_note(title, content, _actions=None):
    rid = store.insert_record("documents", {"title": title[:250], "doc_type": "Note Sheet",
                                            "doc_date": date.today().isoformat(), "description": content})
    _actions.append({"what": f"Saved note: {title[:120]}", "url": _url("documents", rid)})
    return f"Saved as Note Sheet (documents id {rid}). Link: {_url('documents', rid)}"


def t_save_letter_draft(subject, content, to_party=None, ref_letters=None, category=None, _actions=None):
    rid = store.insert_record("letters", {
        "ref_no": f"DRAFT-{date.today():%Y%m%d}-{uuid.uuid4().hex[:4].upper()}", "letter_date": date.today().isoformat(),
        "direction": "Outgoing", "from_party": store.get_settings().get("our_role"), "to_party": to_party,
        "subject": subject[:250], "category": category or "General", "body": content, "reply_required": "No",
        "status": "Draft", "ref_letters": ref_letters})
    _actions.append({"what": f"Saved draft letter: {subject[:120]}", "url": _url("letters", rid)})
    return f"Saved as Draft letter (letters id {rid}). Link: {_url('letters', rid)}"


def t_update_letter(id, changes, _actions=None):
    _get("letters", id)
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes must be a non-empty object")
    bad = [k for k in changes if k not in LETTER_FIELDS]
    if bad:
        raise ValueError(f"Not allowed to change: {', '.join(bad)}")
    store.update_record("letters", int(id), {k: (v if v != "" else None) for k, v in changes.items()})
    _actions.append({"what": f"Updated letter #{id}: " + ", ".join(f"{k} = {v}" for k, v in changes.items()),
                     "url": _url("letters", id)})
    return "Updated."


def t_add_dpr_entry(_actions=None, **fields):
    allowed = {f[0] for f in MODULES["dpr"]["fields"]}
    data = {k: v for k, v in fields.items() if k in allowed}
    rid = store.insert_record("dpr", data)
    _actions.append({"what": f"Added DPR entry {data.get('report_date')} {data.get('activity')}", "url": _url("dpr", rid)})
    return f"DPR entry added (id {rid})."


def t_add_drb_proceeding(dispute_id, entry_date, entry_type, party=None, summary=None, _actions=None):
    _get("drb", dispute_id)
    store.insert_record("drb_entries", {"dispute_id": int(dispute_id), "entry_date": entry_date, "entry_type": entry_type,
                                        "party": party, "summary": summary})
    _actions.append({"what": f"Added '{entry_type}' to dispute #{dispute_id}", "url": _url("drb", dispute_id)})
    return "Proceeding added."


def t_sync_folders(_actions=None):
    app = current_app._get_current_object()
    from .app import ALLOWED_EXT
    if sync.status["running"]:
        return "A sync is already running."
    threading.Thread(target=sync.run_sync, args=(app, ALLOWED_EXT), daemon=True).start()
    _actions.append({"what": "Started folder sync", "url": "/sync"})
    return "Sync started in the background; new files will be searchable within a few minutes."


HANDLERS = {
    "search_records": t_search_records, "read_record": t_read_record, "read_original_file": t_read_original_file,
    "list_letters": t_list_letters, "list_disputes": t_list_disputes, "project_overview": t_project_overview,
    "dpr_summary": t_dpr_summary, "save_note": t_save_note, "save_letter_draft": t_save_letter_draft,
    "update_letter": t_update_letter, "add_dpr_entry": t_add_dpr_entry, "add_drb_proceeding": t_add_drb_proceeding,
    "sync_folders": t_sync_folders,
}
WRITES = {"save_note", "save_letter_draft", "update_letter", "add_dpr_entry", "add_drb_proceeding", "sync_folders"}


def run_tool(name, args, actions):
    fn = HANDLERS.get(name)
    if fn is None:
        return f"Unknown tool {name}", True
    try:
        if not isinstance(args, dict):
            raise ValueError("tool input must be an object")
        if name in WRITES:
            return fn(_actions=actions, **args), False
        return fn(**args), False
    except TypeError as e:
        return f"Invalid arguments for {name}: {e}", True
    except Exception as e:
        return f"{name} failed: {e}", True


# ----------------------------------------------------------------------------- conversation loop

def new_conversation():
    cid = uuid.uuid4().hex
    with _conv_lock:
        _conversations[cid] = []
    return cid


def _call(client, **kw):
    """One model turn. Streaming keeps long turns (big notes, many documents) under HTTP time limits."""
    with client.beta.messages.stream(
            model=MODEL, thinking={"type": "adaptive"}, output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw) as stream:
        return stream.get_final_message()


def ask(cid, user_text):
    """Run one user turn to completion. Returns dict(text, actions, steps, error)."""
    try:
        import anthropic
    except ImportError:
        return {"error": "The 'anthropic' package is not installed."}
    with _conv_lock:
        history = _conversations.setdefault(cid, [])
    messages = list(history) + [{"role": "user", "content": user_text}]
    client = anthropic.Anthropic()
    system = system_prompt(store.get_settings())
    actions, steps, response = [], [], None
    try:
        for _ in range(MAX_STEPS):
            response = _call(client, max_tokens=32000, system=system, tools=TOOLS, messages=messages,
                             cache_control={"type": "ephemeral"})
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return {"error": "The request was declined by the model's safety system.", "actions": actions, "steps": steps}
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                break
            results = []
            for call in calls:
                out, is_err = run_tool(call.name, call.input, actions)
                steps.append({"tool": call.name, "input": {k: (v if len(str(v)) < 120 else str(v)[:117] + "...")
                                                           for k, v in (call.input or {}).items()}, "error": is_err})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out, "is_error": is_err})
            messages.append({"role": "user", "content": results})
        else:
            messages.append({"role": "user", "content": "Stop using tools now and give your answer with what you have."})
            response = _call(client, max_tokens=16000, system=system, tools=TOOLS, messages=messages)
            messages.append({"role": "assistant", "content": response.content})
    except anthropic.AuthenticationError:
        return {"error": "Anthropic API key was rejected - check the key.", "actions": actions, "steps": steps}
    except anthropic.RateLimitError:
        return {"error": "Rate limited by the Anthropic API - try again in a minute.", "actions": actions, "steps": steps}
    except anthropic.APIStatusError as e:
        return {"error": f"Anthropic API error {e.status_code}: {e.message}", "actions": actions, "steps": steps}
    except anthropic.APIConnectionError:
        return {"error": "Could not reach the Anthropic API (check internet).", "actions": actions, "steps": steps}
    except Exception as e:
        return {"error": f"Assistant failed: {e}", "actions": actions, "steps": steps}
    text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n[Answer cut short - ask me to continue.]"
    with _conv_lock:
        _conversations[cid] = messages
    return {"text": text or "(done)", "actions": actions, "steps": steps}
