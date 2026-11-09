"""Southern Bypass Project - Project Management & Contracts System (Flask app)."""
import csv
import io
import os
from datetime import date, datetime

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_from_directory, url_for)
from werkzeug.utils import secure_filename

from . import db as store
from . import drafting
from . import assistant
from . import compose
from . import sync
from . import importer
from .modules import MODULES, ROLES, SETTINGS_FIELDS, field_map

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "csv", "jpg", "jpeg", "png",
               "dwg", "dxf", "zip", "txt", "ppt", "pptx", "msg", "eml", "heic", "rtf",
               "xlsm", "tif", "tiff", "kml", "kmz", "mpp", "odt", "ods"}


def create_app(test_config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    data_dir = os.environ.get("SAB_DATA_DIR") or default_data_dir()
    app.config.update(
        SECRET_KEY=os.environ.get("SAB_SECRET_KEY", "change-me-in-production"),
        DATABASE=os.path.join(data_dir, "sab.sqlite3"),
        UPLOAD_FOLDER=os.path.join(data_dir, "uploads"),
        MAX_CONTENT_LENGTH=4 * 1024 * 1024 * 1024,  # bulk import of whole folders
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    # optional: API key for AI drafting kept in a file next to the data (never inside the code folder)
    key_file = os.path.join(os.path.dirname(app.config["DATABASE"]), "anthropic_key.txt")
    if not os.environ.get("ANTHROPIC_API_KEY") and os.path.isfile(key_file):
        with open(key_file, encoding="utf8") as fh:
            key = fh.read().strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key

    app.teardown_appcontext(store.close_db)
    with app.app_context():
        store.init_db()

    @app.context_processor
    def inject_globals():
        return {
            "MODULES": MODULES,
            "settings": store.get_settings(),
            "ai_available": drafting.ai_available(),
            "today": date.today().isoformat(),
        }

    @app.template_filter("dmy")
    def dmy(value):
        if value and isinstance(value, str) and len(value) == 10 and value[4] == "-":
            return f"{value[8:10]}-{value[5:7]}-{value[0:4]}"
        return value if value is not None else ""

    @app.template_filter("num")
    def num(value):
        if value is None or value == "":
            return ""
        try:
            f = float(value)
        except (TypeError, ValueError):
            return value
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"

    _register_routes(app)
    with app.app_context():
        sync.ensure_tables(store.get_db())
        try:
            minutes = int(store.get_settings().get("sync_minutes") or 10)
        except ValueError:
            minutes = 10
    if not app.config.get("TESTING") and os.environ.get("SAB_NO_SYNC") != "1":
        sync.start_background(app, ALLOWED_EXT, minutes)
    return app


def default_data_dir():
    """Keep data outside the code folder so a new version of the app never loses it.

    An existing 'data' folder next to the code (older versions) is still used.
    """
    legacy = os.path.join(BASE_DIR, "data")
    if os.path.isdir(legacy):
        return legacy
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "SAB-Data")


def _module_or_404(module):
    if module not in MODULES:
        abort(404)
    return MODULES[module]


def _save_upload(app, file):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type .{ext} is not allowed.")
    name = datetime.now().strftime("%Y%m%d%H%M%S_") + secure_filename(file.filename)
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], name))
    return name


def _form_data(app, module, existing=None):
    mod = MODULES[module]
    data = {}
    for name, label, ftype, _opts in mod["fields"]:
        if ftype == "file":
            saved = _save_upload(app, request.files.get(name))
            if saved:
                data[name] = saved
            elif existing is None:
                data[name] = None
            continue
        raw = request.form.get(name, "").strip()
        if ftype in ("number", "hidden"):
            if raw == "":
                data[name] = None
            else:
                try:
                    data[name] = float(raw) if ftype == "number" else int(raw)
                except ValueError:
                    raise ValueError(f"'{label}' must be a number.")
        else:
            data[name] = raw or None
    missing = [field_map(module)[f][1] for f in mod.get("required", []) if not data.get(f)]
    if missing:
        raise ValueError("Required: " + ", ".join(missing))
    return data


def dpr_month_summary(month):
    """Aggregate all DPR entries of a month (YYYY-MM) for the MPR."""
    rows = store.get_db().execute(
        "SELECT activity, unit, SUM(qty_planned) AS planned, SUM(qty_achieved) AS achieved, "
        "COUNT(*) AS entries FROM dpr WHERE substr(report_date,1,7) = ? "
        "GROUP BY activity, unit ORDER BY activity", (month,)).fetchall()
    days = store.get_db().execute(
        "SELECT COUNT(DISTINCT report_date) AS days, "
        "COUNT(DISTINCT CASE WHEN weather IN ('Rain','Heavy Rain') THEN report_date END) AS rain_days, "
        "AVG(manpower) AS avg_manpower "
        "FROM dpr WHERE substr(report_date,1,7) = ?", (month,)).fetchone()
    hindrances = store.get_db().execute(
        "SELECT report_date, chainage_from, chainage_to, activity, hindrances FROM dpr "
        "WHERE substr(report_date,1,7) = ? AND hindrances IS NOT NULL AND hindrances != '' "
        "ORDER BY report_date", (month,)).fetchall()
    return rows, days, hindrances


def _summary_text(month, rows, days, hindrances):
    ach = [f"- {r['activity']}: {r['achieved'] or 0:,.2f} {r['unit'] or ''} achieved "
           f"against {r['planned'] or 0:,.2f} planned" for r in rows]
    iss = [f"- {r['report_date']} Ch. {r['chainage_from'] or ''}-{r['chainage_to'] or ''} "
           f"({r['activity']}): {r['hindrances']}" for r in hindrances]
    head = (f"Working days reported: {days['days'] or 0}; rain-affected days: {days['rain_days'] or 0}; "
            f"average manpower: {round(days['avg_manpower'] or 0)}.")
    return head + "\n" + "\n".join(ach), "\n".join(iss)


def _register_routes(app):

    @app.route("/")
    def dashboard():
        db = store.get_db()
        counts = {m: db.execute(f'SELECT COUNT(*) FROM "{m}"').fetchone()[0]
                  for m, mod in MODULES.items() if not mod.get("hidden")}
        today = date.today().isoformat()
        overdue = db.execute(
            "SELECT * FROM letters WHERE reply_required = 'Yes' AND status = 'Open' "
            "AND reply_due IS NOT NULL AND reply_due < ? ORDER BY reply_due", (today,)).fetchall()
        due_soon = db.execute(
            "SELECT * FROM letters WHERE reply_required = 'Yes' AND status = 'Open' "
            "AND (reply_due IS NULL OR reply_due >= ?) ORDER BY reply_due", (today,)).fetchall()
        disputes = db.execute(
            "SELECT * FROM drb WHERE status NOT IN ('Settled','Withdrawn','Recommendation Accepted') "
            "ORDER BY next_hearing IS NULL, next_hearing").fetchall()
        claimed = db.execute(
            "SELECT COALESCE(SUM(amount_claimed),0) FROM drb WHERE status NOT IN ('Settled','Withdrawn')"
        ).fetchone()[0]
        last_mpr = db.execute("SELECT * FROM mpr ORDER BY month DESC LIMIT 1").fetchone()
        last_dpr_date = db.execute("SELECT MAX(report_date) FROM dpr").fetchone()[0]
        last_dpr = db.execute("SELECT * FROM dpr WHERE report_date = ?", (last_dpr_date,)).fetchall() if last_dpr_date else []
        recent_letters = store.list_records("letters", limit=8)
        return render_template("dashboard.html", counts=counts, overdue=overdue, due_soon=due_soon,
                               disputes=disputes, claimed=claimed, last_mpr=last_mpr,
                               last_dpr=last_dpr, last_dpr_date=last_dpr_date,
                               recent_letters=recent_letters)

    @app.route("/m/<module>")
    def list_view(module):
        mod = _module_or_404(module)
        if mod.get("hidden"):
            abort(404)
        q = request.args.get("q", "").strip()
        filter_cols = [f for f in ("direction", "status", "category", "activity", "doc_type")
                       if f in field_map(module)]
        filters = {c: request.args.get(c, "") for c in filter_cols}
        rows = store.list_records(module, q=q, filters=filters)
        if module == "dpr" and request.args.get("month"):
            rows = [r for r in rows if (r["report_date"] or "").startswith(request.args["month"])]
        return render_template("list.html", module=module, mod=mod, rows=rows, q=q,
                               filters=filters, fmap=field_map(module))

    @app.route("/m/<module>/export.csv")
    def export_csv(module):
        mod = _module_or_404(module)
        rows = store.list_records(module)
        buf = io.StringIO()
        w = csv.writer(buf)
        names = ["id"] + [f[0] for f in mod["fields"]]
        w.writerow(["ID"] + [f[1] for f in mod["fields"]])
        for r in rows:
            w.writerow([r[n] for n in names])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={module}_{date.today()}.csv"})

    @app.route("/m/<module>/new", methods=["GET", "POST"])
    def new_view(module):
        mod = _module_or_404(module)
        if request.method == "POST":
            try:
                data = _form_data(app, module)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("form.html", module=module, mod=mod, record=request.form, is_new=True)
            rid = store.insert_record(module, data)
            flash(f"{mod['singular']} saved.", "ok")
            if mod.get("parent"):
                return redirect(url_for("detail_view", module=mod["parent"], rid=data["dispute_id"]))
            return redirect(url_for("detail_view", module=module, rid=rid))
        prefill = {k: v for k, v in request.args.items()}
        prefill.setdefault("status", None)
        if module == "dpr":
            prefill.setdefault("report_date", date.today().isoformat())
        if module == "letters":
            prefill.setdefault("letter_date", date.today().isoformat())
        return render_template("form.html", module=module, mod=mod, record=prefill, is_new=True)

    @app.route("/m/<module>/<int:rid>")
    def detail_view(module, rid):
        mod = _module_or_404(module)
        record = store.get_record(module, rid)
        if record is None:
            abort(404)
        ctx = {"module": module, "mod": mod, "record": record, "fmap": field_map(module)}
        ctx["replies"] = store.get_replies(module, rid) if mod.get("can_reply") else []
        if module == "letters":
            ctx["linked"] = store.find_letters_by_refs(record["ref_letters"])
            ctx["answers"] = store.get_db().execute(
                "SELECT * FROM letters WHERE ref_letters LIKE ? AND id != ? ORDER BY letter_date",
                (f"%{record['ref_no']}%", rid)).fetchall()
        if module == "drb":
            ctx["linked"] = store.find_letters_by_refs(record["related_letters"])
            ctx["entries"] = store.get_db().execute(
                "SELECT * FROM drb_entries WHERE dispute_id = ? ORDER BY entry_date, id", (rid,)).fetchall()
            ctx["entry_mod"] = MODULES["drb_entries"]
        if module == "mpr":
            ctx["summary"] = dpr_month_summary(record["month"] or "")
        return render_template("detail.html", **ctx)

    @app.route("/m/<module>/<int:rid>/edit", methods=["GET", "POST"])
    def edit_view(module, rid):
        mod = _module_or_404(module)
        record = store.get_record(module, rid)
        if record is None:
            abort(404)
        if request.method == "POST":
            try:
                data = _form_data(app, module, existing=record)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("form.html", module=module, mod=mod, record=request.form, is_new=False, rid=rid)
            store.update_record(module, rid, data)
            flash(f"{mod['singular']} updated.", "ok")
            if mod.get("parent"):
                return redirect(url_for("detail_view", module=mod["parent"], rid=record["dispute_id"]))
            return redirect(url_for("detail_view", module=module, rid=rid))
        return render_template("form.html", module=module, mod=mod, record=record, is_new=False, rid=rid)

    @app.route("/m/<module>/<int:rid>/delete", methods=["POST"])
    def delete_view(module, rid):
        mod = _module_or_404(module)
        record = store.get_record(module, rid)
        if record is None:
            abort(404)
        store.delete_record(module, rid)
        flash(f"{mod['singular']} deleted.", "ok")
        if mod.get("parent"):
            return redirect(url_for("detail_view", module=mod["parent"], rid=record["dispute_id"]))
        return redirect(url_for("list_view", module=module))

    @app.route("/draft/<module>/<int:rid>", methods=["GET", "POST"])
    def draft_view(module, rid):
        mod = _module_or_404(module)
        if not mod.get("can_reply"):
            abort(404)
        record = store.get_record(module, rid)
        if record is None:
            abort(404)
        settings = store.get_settings()
        if module == "letters":
            linked = store.find_letters_by_refs(record["ref_letters"])
            entries = []
            kinds = drafting.LETTER_KINDS
        else:
            linked = store.find_letters_by_refs(record["related_letters"])
            entries = store.get_db().execute(
                "SELECT * FROM drb_entries WHERE dispute_id = ? ORDER BY entry_date, id", (rid,)).fetchall()
            kinds = drafting.DRB_KINDS
        draft, kind, instructions = None, request.form.get("kind", kinds[0]), request.form.get("instructions", "")
        if request.method == "POST":
            action = request.form.get("action")
            if action == "save":
                content = request.form.get("content", "").strip()
                if content:
                    store.add_reply(module, rid, kind, request.form.get("source", "manual"), content)
                    flash("Draft saved to this record.", "ok")
                return redirect(url_for("detail_view", module=module, rid=rid))
            if action == "ai":
                prompt = drafting.build_prompt(module, record, linked, entries, settings, kind, instructions)
                draft, err = drafting.ai_draft(prompt)
                if err:
                    flash(err + " Showing template draft instead.", "error")
            if draft is None:
                draft = (drafting.template_letter_reply(record, linked, settings, kind) if module == "letters"
                         else drafting.template_drb(record, entries, linked, settings, kind))
                source = "template"
            else:
                source = "ai"
            return render_template("draft.html", module=module, mod=mod, record=record, kinds=kinds,
                                   kind=kind, draft=draft, source=source, instructions=instructions,
                                   linked=linked, entries=entries)
        return render_template("draft.html", module=module, mod=mod, record=record, kinds=kinds,
                               kind=kind, draft=None, source=None, instructions="", linked=linked,
                               entries=entries)

    @app.route("/reply/<int:reply_id>/delete", methods=["POST"])
    def delete_reply(reply_id):
        db = store.get_db()
        row = db.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
        if row is None:
            abort(404)
        db.execute("DELETE FROM replies WHERE id = ?", (reply_id,))
        db.commit()
        flash("Draft deleted.", "ok")
        return redirect(url_for("detail_view", module=row["module"], rid=row["record_id"]))

    @app.route("/reply/<int:reply_id>/issue", methods=["POST"])
    def issue_reply(reply_id):
        """Register a saved reply as an outgoing letter and mark the original as replied."""
        db = store.get_db()
        row = db.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
        if row is None or row["module"] != "letters":
            abort(404)
        original = store.get_record("letters", row["record_id"])
        ref_no = request.form.get("ref_no", "").strip()
        if not ref_no:
            flash("Enter the outgoing letter reference number.", "error")
            return redirect(url_for("detail_view", module="letters", rid=original["id"]))
        role = store.get_settings().get("our_role", "Contractor")
        new_id = store.insert_record("letters", {
            "ref_no": ref_no, "letter_date": request.form.get("letter_date") or date.today().isoformat(),
            "direction": "Outgoing", "from_party": role, "to_party": original["from_party"],
            "subject": f"Reply - {original['subject']}", "category": original["category"],
            "ref_letters": original["ref_no"], "body": row["content"], "reply_required": "No",
            "status": "Closed",
        })
        store.update_record("letters", original["id"], {"status": "Replied"})
        flash(f"Outgoing letter {ref_no} registered and original marked as Replied.", "ok")
        return redirect(url_for("detail_view", module="letters", rid=new_id))

    @app.route("/mpr/generate")
    def mpr_generate():
        month = request.args.get("month") or date.today().strftime("%Y-%m")
        rows, days, hindrances = dpr_month_summary(month)
        prev = store.get_db().execute("SELECT * FROM mpr WHERE month < ? ORDER BY month DESC LIMIT 1",
                                      (month,)).fetchone()
        achievements, issues = _summary_text(month, rows, days, hindrances)
        return render_template("mpr_generate.html", month=month, rows=rows, days=days,
                               hindrances=hindrances, prev=prev, achievements=achievements, issues=issues)

    @app.route("/mpr/<int:rid>/print")
    def mpr_print(rid):
        record = store.get_record("mpr", rid)
        if record is None:
            abort(404)
        rows, days, hindrances = dpr_month_summary(record["month"] or "")
        db = store.get_db()
        disputes = db.execute("SELECT * FROM drb WHERE status NOT IN ('Settled','Withdrawn')").fetchall()
        letters = db.execute("SELECT * FROM letters WHERE substr(letter_date,1,7) = ? ORDER BY letter_date",
                             (record["month"],)).fetchall()
        return render_template("mpr_print.html", record=record, rows=rows, days=days,
                               hindrances=hindrances, disputes=disputes, letters=letters,
                               fmap=field_map("mpr"))

    @app.route("/search")
    def search():
        q = request.args.get("q", "").strip()
        results = {}
        if q:
            for m, mod in MODULES.items():
                rows = store.list_records(m, q=q)
                if rows:
                    results[m] = rows
            replies = store.get_db().execute("SELECT * FROM replies WHERE content LIKE ?", (f"%{q}%",)).fetchall()
            if replies:
                results["replies"] = replies
        return render_template("search.html", q=q, results=results)

    @app.route("/settings", methods=["GET", "POST"])
    def settings_view():
        if request.method == "POST":
            store.save_settings({k: request.form.get(k, "").strip() for k, _l, _d in SETTINGS_FIELDS})
            flash("Project settings saved.", "ok")
            return redirect(url_for("settings_view"))
        return render_template("settings.html", fields=SETTINGS_FIELDS, roles=ROLES)

    def _local_only():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403, "Browsing this computer's folders is only allowed on the computer running SAB.")

    def _safe_dir(path):
        home = os.path.realpath(os.path.expanduser("~"))
        real = os.path.realpath(os.path.expanduser(path or "~"))
        if not (real == home or real.startswith(home + os.sep)) or not os.path.isdir(real):
            abort(400, "Folder not found or outside your home folder.")
        return real

    def _start_folders():
        return sync.suggested_folders()

    @app.route("/import/folders")
    def import_folders():
        _local_only()
        if not request.args.get("path"):
            return {"path": None, "parent": None, "dirs": [{"name": n, "path": p} for n, p in _start_folders()], "files": 0}
        path = _safe_dir(request.args["path"])
        dirs, files = [], 0
        try:
            for n in sorted(os.listdir(path), key=str.lower):
                full = os.path.join(path, n)
                if n.startswith("."):
                    continue
                if os.path.isdir(full):
                    dirs.append({"name": n, "path": full})
                else:
                    files += 1
        except PermissionError:
            return {"error": "macOS did not allow access to this folder. See the note on this page.", "path": path,
                    "parent": os.path.dirname(path), "dirs": [], "files": 0}
        return {"path": path, "parent": os.path.dirname(path), "dirs": dirs, "files": files}

    @app.route("/import/list", methods=["POST"])
    def import_list():
        _local_only()
        root = _safe_dir(request.form.get("path"))
        found = []
        for dirpath, dnames, fnames in os.walk(root):
            dnames[:] = [d for d in dnames if not d.startswith(".")]
            for n in sorted(fnames):
                if not n.startswith("."):
                    found.append(os.path.join(dirpath, n))
        return {"root": root, "files": found}

    @app.route("/import", methods=["GET", "POST"])
    def import_view():
        if request.method == "GET":
            return render_template("import.html", results=None, data_dir=os.path.dirname(app.config["DATABASE"]))
        force = request.form.get("register") or None
        if force not in (None, "letters", "documents"):
            force = None
        results = []
        local = request.form.get("local_path")
        if local:
            _local_only()
            root = _safe_dir(request.form.get("root"))
            real = os.path.realpath(local)
            if not real.startswith(root + os.sep) or not os.path.isfile(real):
                abort(400)
            rel = os.path.join(os.path.basename(root), os.path.relpath(real, root))
            try:
                results.append(importer.import_file(real, rel, app.config["UPLOAD_FOLDER"], ALLOWED_EXT, force))
            except Exception as e:
                results.append({"name": rel, "status": "error", "module": None, "id": None, "note": str(e)})
            return {"results": results}
        tmp = os.path.join(app.config["UPLOAD_FOLDER"], ".incoming")
        os.makedirs(tmp, exist_ok=True)
        for f in request.files.getlist("files"):
            if not f or not f.filename:
                continue
            path = os.path.join(tmp, "upload.tmp")
            f.save(path)
            try:
                results.append(importer.import_file(path, f.filename, app.config["UPLOAD_FOLDER"], ALLOWED_EXT, force))
            except Exception as e:  # never let one bad file stop the whole import
                results.append({"name": f.filename, "status": "error", "module": None, "id": None, "note": str(e)})
            finally:
                if os.path.exists(path):
                    os.remove(path)
        if request.form.get("ajax"):
            return {"results": results}
        n = sum(r["status"] == "imported" for r in results)
        flash(f"Imported {n} file(s); {len(results) - n} skipped.", "ok")
        return render_template("import.html", results=results, data_dir=os.path.dirname(app.config["DATABASE"]))

    @app.route("/compose", methods=["GET", "POST"])
    def compose_view():
        f = request.form
        ctx = {"kinds": compose.KINDS, "kind": f.get("kind", "note"), "request_text": f.get("request_text", ""),
               "addressee": f.get("addressee", ""), "extra_points": f.get("extra_points", ""),
               "candidates": None, "chosen": set(), "draft": None, "source": None}
        action = f.get("action")
        if request.method == "GET" or not action:
            return render_template("compose.html", **ctx)
        if not ctx["request_text"].strip():
            flash("Describe what you need first.", "error")
            return render_template("compose.html", **ctx)

        if action == "save":
            content = f.get("content", "").strip()
            subject = f.get("subject", "").strip() or ctx["request_text"][:200]
            if ctx["kind"] in ("note", "summary", "office_order", "agenda"):
                rid = store.insert_record("documents", {
                    "title": subject, "doc_type": "Note Sheet", "doc_date": date.today().isoformat(),
                    "description": content})
                flash("Saved in the Document Repository as a Note Sheet.", "ok")
                return redirect(url_for("detail_view", module="documents", rid=rid))
            role = store.get_settings().get("our_role")
            rid = store.insert_record("letters", {
                "ref_no": f.get("ref_no", "").strip() or f"DRAFT-{date.today():%Y%m%d}-{datetime.now():%H%M%S}",
                "letter_date": date.today().isoformat(), "direction": "Outgoing", "from_party": role,
                "subject": subject, "category": "General", "body": content, "reply_required": "No",
                "status": "Draft", "ref_letters": f.get("ref_letters", "")})
            flash("Saved in Correspondence as a Draft outgoing letter. Edit it to add the final letter no.", "ok")
            return redirect(url_for("detail_view", module="letters", rid=rid))

        found = compose.find_sources(ctx["request_text"] + " " + ctx["extra_points"] + " " + f.get("more", ""), limit=25)
        chosen = set(f.getlist("src"))
        if action == "find":
            top = found[0]["score"] if found else 0
            chosen = {f"{c['module']}:{c['id']}" for c in found[:8] if c["score"] >= top * 0.3}
        # keep previously ticked sources visible even if they no longer rank
        known = {f"{c['module']}:{c['id']}" for c in found}
        for key in chosen - known:
            m, _, i = key.partition(":")
            row = store.get_record(m, int(i)) if m in compose.SOURCES and i.isdigit() else None
            if row:
                found.append({"module": m, "id": row["id"], "score": "✓", "label": compose.describe(m, row),
                              "date": compose._date_of(m, row)})
        ctx.update(candidates=found, chosen=chosen)

        if action == "generate":
            selected = []
            for key in f.getlist("src"):
                m, _, i = key.partition(":")
                row = store.get_record(m, int(i)) if m in compose.SOURCES and i.isdigit() else None
                if row:
                    selected.append((m, row))
            selected.sort(key=lambda mr: compose._date_of(*mr))
            settings = store.get_settings()
            draft = None
            if f.get("engine") == "ai":
                content = compose.build_content(ctx["kind"], ctx["request_text"], selected, settings,
                                                app.config["UPLOAD_FOLDER"], ctx["extra_points"], ctx["addressee"])
                draft, err = drafting.ai_draft(content, system=compose.SYSTEM)
                if err:
                    flash(err + " Showing the template instead.", "error")
            ctx["source"] = "ai" if draft else "template"
            ctx["draft"] = draft or compose.template(ctx["kind"], ctx["request_text"], selected, settings,
                                                     ctx["addressee"], ctx["extra_points"])
            ctx["ref_letters"] = ", ".join(r["ref_no"] for m, r in selected if m == "letters")
        return render_template("compose.html", **ctx)

    @app.route("/compose/docx", methods=["POST"])
    def compose_docx():
        content = request.form.get("content", "")
        name = secure_filename((request.form.get("subject") or "draft")[:60]) or "draft"
        path = os.path.join(app.config["UPLOAD_FOLDER"], ".incoming", f"{name}.docx")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        compose.to_docx(content, path)
        return send_from_directory(os.path.dirname(path), os.path.basename(path), as_attachment=True)

    # ------------------------------------------------------------ connected folders (auto sync)
    @app.route("/sync", methods=["GET", "POST"])
    def sync_view():
        if request.method == "POST":
            _local_only()
            action = request.form.get("action")
            if action == "add":
                path = _safe_dir(request.form.get("path"))
                sync.add_folder(path, request.form.get("label") or None)
                flash(f"Connected: {path}. Indexing starts now.", "ok")
                action = "run"
            elif action == "add_all":
                for label, path in sync.suggested_folders():
                    if label not in ("Documents", "Downloads"):
                        sync.add_folder(path, label)
                flash("Connected Desktop and all cloud drives found. Indexing starts now.", "ok")
                action = "run"
            elif action == "remove":
                sync.remove_folder(int(request.form.get("id")))
                flash("Folder disconnected (records already created are kept).", "ok")
            if action == "run" and not sync.status["running"]:
                import threading
                threading.Thread(target=sync.run_sync, args=(app, ALLOWED_EXT), daemon=True).start()
            return redirect(url_for("sync_view"))
        return render_template("sync.html", folders=sync.folders(enabled_only=True),
                               suggested=sync.suggested_folders(), status=sync.status)

    @app.route("/sync/status")
    def sync_status():
        return dict(sync.status)

    @app.route("/wf/<int:wid>")
    def watched_file(wid):
        from flask import send_file
        path = sync.watched_path(wid)
        if not path or not os.path.isfile(path):
            abort(404, "This file has been moved or deleted from the connected folder.")
        return send_file(path)

    # ------------------------------------------------------------ assistant (give a task in plain words)
    @app.route("/assistant")
    def assistant_view():
        return render_template("assistant.html")

    @app.route("/assistant/ask", methods=["POST"])
    def assistant_ask():
        if not drafting.ai_available():
            return {"error": "The assistant needs an Anthropic API key. Save it in anthropic_key.txt in your "
                             "SAB-Data folder (see README) and restart SAB."}, 400
        text = (request.form.get("message") or "").strip()
        cid = request.form.get("cid") or assistant.new_conversation()
        if not text:
            return {"error": "Type a task first."}, 400
        result = assistant.ask(cid, text)
        result["cid"] = cid
        return result

    @app.route("/uploads/<path:name>")
    def uploaded(name):
        return send_from_directory(app.config["UPLOAD_FOLDER"], name)


