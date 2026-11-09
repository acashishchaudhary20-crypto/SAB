"""SQLite storage. Tables are created from the register definitions in modules.py."""
import sqlite3
from datetime import datetime

from flask import current_app, g

from .modules import MODULES, SETTINGS_FIELDS


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")  # lets the background folder sync and the web pages work together
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _sql_type(ftype):
    return "REAL" if ftype == "number" else ("INTEGER" if ftype == "hidden" else "TEXT")


def init_db():
    db = get_db()
    for name, mod in MODULES.items():
        cols = ", ".join(f'"{f[0]}" {_sql_type(f[2])}' for f in mod["fields"])
        db.execute(
            f'CREATE TABLE IF NOT EXISTS "{name}" ('
            f"id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, "
            f"created_at TEXT, updated_at TEXT)"
        )
        # add any new columns introduced after the database was first created
        existing = {r["name"] for r in db.execute(f'PRAGMA table_info("{name}")')}
        for f in mod["fields"]:
            if f[0] not in existing:
                db.execute(f'ALTER TABLE "{name}" ADD COLUMN "{f[0]}" {_sql_type(f[2])}')
    db.execute(
        "CREATE TABLE IF NOT EXISTS replies ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT NOT NULL, record_id INTEGER NOT NULL, "
        "kind TEXT, source TEXT, content TEXT, created_at TEXT)"
    )
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    for key, _label, default in SETTINGS_FIELDS:
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default))
    db.commit()


def now():
    return datetime.now().isoformat(timespec="seconds")


def get_settings():
    return {r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")}


def save_settings(values):
    db = get_db()
    for key, value in values.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def get_record(module, rid):
    return get_db().execute(f'SELECT * FROM "{module}" WHERE id = ?', (rid,)).fetchone()


def insert_record(module, data):
    data = dict(data, created_at=now(), updated_at=now())
    cols = ", ".join(f'"{k}"' for k in data)
    qs = ", ".join("?" for _ in data)
    db = get_db()
    cur = db.execute(f'INSERT INTO "{module}" ({cols}) VALUES ({qs})', list(data.values()))
    db.commit()
    return cur.lastrowid


def update_record(module, rid, data):
    data = dict(data, updated_at=now())
    sets = ", ".join(f'"{k}" = ?' for k in data)
    db = get_db()
    db.execute(f'UPDATE "{module}" SET {sets} WHERE id = ?', [*data.values(), rid])
    db.commit()


def delete_record(module, rid):
    db = get_db()
    child = MODULES[module].get("children")
    if child:
        db.execute(f'DELETE FROM "{child}" WHERE dispute_id = ?', (rid,))
    db.execute("DELETE FROM replies WHERE module = ? AND record_id = ?", (module, rid))
    db.execute(f'DELETE FROM "{module}" WHERE id = ?', (rid,))
    db.commit()


def list_records(module, q=None, filters=None, limit=None):
    mod = MODULES[module]
    where, params = [], []
    if q:
        text_cols = [f[0] for f in mod["fields"] if f[2] in ("text", "textarea", "select", "date", "month")]
        where.append("(" + " OR ".join(f'"{c}" LIKE ?' for c in text_cols) + ")")
        params += [f"%{q}%"] * len(text_cols)
    for col, val in (filters or {}).items():
        if val:
            where.append(f'"{col}" = ?')
            params.append(val)
    sql = f'SELECT * FROM "{module}"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {mod['order']}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return get_db().execute(sql, params).fetchall()


def find_letters_by_refs(refs_text):
    """Return letters whose ref_no matches any comma/semicolon separated reference."""
    refs = [r.strip() for r in (refs_text or "").replace(";", ",").split(",") if r.strip()]
    if not refs:
        return []
    qs = ", ".join("?" for _ in refs)
    return get_db().execute(
        f"SELECT * FROM letters WHERE ref_no IN ({qs}) ORDER BY letter_date", refs
    ).fetchall()


def get_replies(module, rid):
    return get_db().execute(
        "SELECT * FROM replies WHERE module = ? AND record_id = ? ORDER BY id DESC", (module, rid)
    ).fetchall()


def add_reply(module, rid, kind, source, content):
    db = get_db()
    cur = db.execute(
        "INSERT INTO replies (module, record_id, kind, source, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (module, rid, kind, source, content, now()),
    )
    db.commit()
    return cur.lastrowid
