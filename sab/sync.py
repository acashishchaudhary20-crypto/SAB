"""Connected folders: keep Desktop / OneDrive / Google Drive / any folder indexed automatically.

Files are NOT copied. Each document in a connected folder becomes a Letter or Document
record whose attachment points to the original file ("watch:<id>:<name>"). Its text is
extracted so search, the composer and the assistant can read it. A background thread
re-scans every few minutes: new files are added, changed files are re-read, and files
that disappear are flagged as missing (their records are kept).
"""
import os
import threading
import time
import uuid
from datetime import datetime

from . import db as store
from . import importer

SKIP_DIRS = {"library", "node_modules", "$recycle.bin", "system volume information", "applications",
             ".trash", "__pycache__", "venv", ".venv", "sab-data"}
MAX_FILE_BYTES = 150 * 1024 * 1024

_lock = threading.Lock()
status = {"running": False, "last_start": None, "last_end": None, "current": "", "folder": "",
          "scanned": 0, "added": 0, "updated": 0, "missing": 0, "errors": 0, "last_error": ""}


def ensure_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS watch_folders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "path TEXT UNIQUE, label TEXT, enabled INTEGER DEFAULT 1, last_sync TEXT, file_count INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS watched_files (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "path TEXT UNIQUE, folder_id INTEGER, size INTEGER, mtime REAL, sha256 TEXT, module TEXT, "
               "record_id INTEGER, missing INTEGER DEFAULT 0, seen_at TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS watched_files_folder ON watched_files(folder_id)")
    db.commit()


def suggested_folders():
    """Desktop, Documents, Downloads and every cloud drive found on this computer."""
    home = os.path.expanduser("~")
    out = [(n, os.path.join(home, n)) for n in ("Desktop", "Documents", "Downloads")]
    cloud = os.path.join(home, "Library", "CloudStorage")
    if os.path.isdir(cloud):
        for n in sorted(os.listdir(cloud)):
            full = os.path.join(cloud, n)
            if os.path.isdir(full) and not n.startswith("."):
                my_drive = os.path.join(full, "My Drive")
                kind = n.split("-")[0].replace("GoogleDrive", "Google Drive")
                label = f"{kind} ({n.split('-', 1)[1]})" if "-" in n else kind
                out.append((label, my_drive if os.path.isdir(my_drive) else full))
    for legacy in ("OneDrive", "Google Drive"):
        p = os.path.join(home, legacy)
        if os.path.isdir(p) and not os.path.islink(p):
            out.append((legacy, p))
    return [(n, p) for n, p in out if os.path.isdir(p)]


def add_folder(path, label=None):
    db = store.get_db()
    ensure_tables(db)
    real = os.path.realpath(os.path.expanduser(path))
    db.execute("INSERT OR IGNORE INTO watch_folders (path, label, enabled) VALUES (?, ?, 1)",
               (real, label or os.path.basename(real)))
    db.execute("UPDATE watch_folders SET enabled = 1 WHERE path = ?", (real,))
    db.commit()


def remove_folder(fid):
    db = store.get_db()
    db.execute("UPDATE watch_folders SET enabled = 0 WHERE id = ?", (fid,))
    db.commit()


def folders(enabled_only=False):
    db = store.get_db()
    ensure_tables(db)
    sql = "SELECT * FROM watch_folders" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id"
    return db.execute(sql).fetchall()


def _walk(root, data_dir):
    data_dir = os.path.realpath(data_dir)
    for dirpath, dnames, fnames in os.walk(root):
        dnames[:] = [d for d in dnames if not d.startswith(".") and d.lower() not in SKIP_DIRS
                     and not d.endswith((".app", ".photoslibrary", ".musiclibrary"))
                     and os.path.realpath(os.path.join(dirpath, d)) != data_dir]
        for n in fnames:
            yield os.path.join(dirpath, n)


def _sync_file(db, folder, path, allowed_ext, token):
    """Add / refresh one file. Returns 'added', 'updated' or None."""
    base = os.path.basename(path)
    if base.startswith((".", "~$")) or base.lower() in importer.SKIP_NAMES:
        return None
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if ext not in allowed_ext or ext == "zip":
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_size == 0 or st.st_size > MAX_FILE_BYTES:
        return None
    row = db.execute("SELECT * FROM watched_files WHERE path = ?", (path,)).fetchone()
    now = token  # marks the file as seen in this scan
    if row and row["size"] == st.st_size and abs((row["mtime"] or 0) - st.st_mtime) < 1:
        if row["missing"] or row["folder_id"] != folder["id"]:
            db.execute("UPDATE watched_files SET missing = 0, folder_id = ?, seen_at = ? WHERE id = ?",
                       (folder["id"], now, row["id"]))
        else:
            db.execute("UPDATE watched_files SET seen_at = ? WHERE id = ?", (now, row["id"]))
        return None
    digest = importer._sha256(path)
    rel = os.path.join(folder["label"] or "", os.path.relpath(path, folder["path"]))
    text = importer.extract_text(path, ext)
    if row:  # file changed on disk: refresh the text of its record
        db.execute("UPDATE watched_files SET size = ?, mtime = ?, sha256 = ?, missing = 0, seen_at = ? WHERE id = ?",
                   (st.st_size, st.st_mtime, digest, now, row["id"]))
        rec = store.get_record(row["module"], row["record_id"]) if row["module"] else None
        if rec is not None and text.strip():
            field = "body" if row["module"] == "letters" else "description"
            store.update_record(row["module"], row["record_id"], {field: text})
        db.commit()
        return "updated"
    cur = db.execute("INSERT INTO watched_files (path, folder_id, size, mtime, sha256, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (path, folder["id"], st.st_size, st.st_mtime, digest, now))
    wid = cur.lastrowid
    dup = db.execute("SELECT * FROM imported_files WHERE sha256 = ?", (digest,)).fetchone()
    if dup and store.get_record(dup["module"], dup["record_id"]) is not None:
        module, rid = dup["module"], dup["record_id"]  # same content already in the system: just link
    else:
        module, data = importer.classify(rel.replace("\\", "/").replace("/", " / "), text)
        data["attachment"] = f"watch:{wid}:{base}"
        if module == "letters" and data.get("direction") == "Outgoing":
            data["from_party"] = store.get_settings().get("our_role")
        rid = store.insert_record(module, data)
        db.execute("INSERT OR REPLACE INTO imported_files VALUES (?, ?, ?, ?, ?)", (digest, module, rid, rel, store.now()))
    db.execute("UPDATE watched_files SET module = ?, record_id = ? WHERE id = ?", (module, rid, wid))
    db.commit()
    return "added"


def run_sync(app, allowed_ext):
    """Scan all enabled connected folders once. Safe to call from any thread."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        with app.app_context():
            db = store.get_db()
            ensure_tables(db)
            importer._ensure_table()
            status.update(running=True, last_start=datetime.now().isoformat(timespec="seconds"), scanned=0,
                          added=0, updated=0, missing=0, errors=0, last_error="", current="")
            data_dir = os.path.dirname(app.config["DATABASE"])
            for folder in folders(enabled_only=True):
                if not os.path.isdir(folder["path"]):
                    status["last_error"] = f"Folder not found: {folder['path']}"
                    continue
                status["folder"] = folder["label"]
                token = f"{store.now()}#{uuid.uuid4().hex[:8]}"
                count = 0
                try:
                    for path in _walk(folder["path"], data_dir):
                        status["current"] = path
                        try:
                            res = _sync_file(db, folder, path, allowed_ext, token)
                        except PermissionError:
                            status["errors"] += 1
                            status["last_error"] = f"macOS blocked access to {path}"
                            continue
                        except Exception as e:  # one bad file must not stop the sync
                            status["errors"] += 1
                            status["last_error"] = f"{os.path.basename(path)}: {e}"
                            continue
                        status["scanned"] += 1
                        count += 1
                        if res:
                            status[res] += 1
                except PermissionError:
                    status["errors"] += 1
                    status["last_error"] = f"macOS blocked access to {folder['path']}"
                    continue
                # files of this folder not seen in this scan have been moved or deleted
                cur = db.execute("UPDATE watched_files SET missing = 1 WHERE folder_id = ? AND missing = 0 "
                                 "AND (seen_at IS NULL OR seen_at != ?)", (folder["id"], token))
                status["missing"] += cur.rowcount
                db.execute("UPDATE watch_folders SET last_sync = ?, file_count = ? WHERE id = ?",
                           (store.now(), count, folder["id"]))
                db.commit()
            store.close_db()
    finally:
        status.update(running=False, current="", folder="", last_end=datetime.now().isoformat(timespec="seconds"))
        _lock.release()
    return True


def start_background(app, allowed_ext, minutes):
    def loop():
        time.sleep(5)
        while True:
            try:
                run_sync(app, allowed_ext)
            except Exception as e:
                status["last_error"] = str(e)
            time.sleep(max(1, minutes) * 60)

    threading.Thread(target=loop, name="sab-sync", daemon=True).start()


def watched_path(wid):
    row = store.get_db().execute("SELECT path FROM watched_files WHERE id = ?", (wid,)).fetchone()
    return row["path"] if row else None
