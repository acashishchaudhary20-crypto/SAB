#!/usr/bin/env python3
"""Dak Courier - carries letters from any folder of this computer to the Google Drive "Dak Inbox".

Why: the scheduled Claude task "Dak register sync" reads everything that lands in Google Drive
and files it in the Claude Dak Register and the Inward Outward Register. It cannot see folders
that stay only on this Mac / PC. This script closes that gap: it looks through the folders you
name, picks the files that are letters (PDF / Word / scans), and copies each one ONCE into
"Dak Inbox" on Google Drive. Nothing is moved, renamed or deleted in your own folders.

    python3 dak_courier.py                 one pass now
    python3 dak_courier.py --watch         keep running, one pass every 15 minutes
    python3 dak_courier.py --dry-run       show what would be sent, copy nothing
    python3 dak_courier.py --add "~/Documents/EOT file"            watch one more folder
    python3 dak_courier.py --add "~/Documents/Scans" --everything  ... and take every file in it
    python3 dak_courier.py --remove "~/Documents/EOT file"
    python3 dak_courier.py --status        folders, inbox, what was sent recently
    python3 dak_courier.py --backfill      this pass also takes old files (still capped per day)

Needs only Python 3.8+. With "pip install pypdf" it can also look inside PDFs whose file name
says nothing (e.g. "18.142.pdf"); without it those are judged by name only.
"""
import argparse
import csv
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import string
import sys
import time
import zipfile
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # damaged PDFs are skipped quietly
LETTER_EXT = {"pdf", "doc", "docx", "jpg", "jpeg", "png", "heic", "tif", "tiff", "eml", "msg"}
IMAGE_EXT = {"jpg", "jpeg", "png", "heic", "tif", "tiff"}
SKIP_DIRS = {"library", "node_modules", "$recycle.bin", "system volume information", "applications",
             "appdata", "__pycache__", "venv", ".venv", "sab-data", "dak-courier"}
SKIP_NAMES = {"desktop.ini", "thumbs.db", "icon\r"}

DEFAULTS = {
    # Folders searched with the "is it a letter?" test. Desktop and Downloads are NOT listed because
    # Google Drive for desktop already syncs them and the Dak register sync already reads them.
    "folders": ["~/Documents"],
    # Folders where every PDF / Word file / scan is taken without the test (e.g. a scanner folder).
    "folders_take_everything": [],
    "inbox": "",                 # blank = find "Dak Inbox" in Google Drive automatically
    "subfolder": "",             # blank = "Mac folders" / "PC folders"
    "since_days": 30,            # ignore files older than this (0 = no age limit)
    "max_per_day": 60,           # at most this many files are sent in any 24 hours; the rest wait
    "settle_minutes": 10,        # leave files that changed in the last few minutes (still downloading / being edited)
    "max_mb": 100,
    "every_minutes": 15,         # --watch interval
    "scanned_pdfs": "skip",      # scanned PDF with a name that says nothing: "skip" or "send"
}

# ---------------------------------------------------------------- is it a letter?
NAME_STRONG = re.compile(
    r"dated|letter|(?<![a-z])ltr(?![a-z])|(?<![a-z])lr\.?\s*no|notice|(?<![a-z])reply|reminder|circular|"
    r"office\s*(order|memo)|minutes|(?<![a-z])mom(?![a-z])|(?<![a-z])nhai(?![a-z])|(?<![a-z])piu(?![a-z])|"
    r"morth|(?<![a-z])noc(?![a-z])|show\s*cause|पत्र|परिपत्र|सूचना", re.I)
COPY_MARK = re.compile(r" \(\d+\)\.\w+$| copy\.\w+$", re.I)
NAME_DATE = re.compile(r"(?<!\d)\d{1,2}\s*[.\-/ ]\s*\d{1,2}\s*[.\-/ ]\s*(20)?\d\d(?!\d)")
NAME_NOT_LETTER = re.compile(r"^(screenshot|screen shot|img[_\- ]?\d|dsc[_\- ]?\d|whatsapp image|photo[_\- ]?\d)", re.I)
TEXT_MARKERS = [
    re.compile(r"\bsub(ject)?\s*[:.\-]|विषय", re.I),
    re.compile(r"\bref(erence)?s?\.?\s*(no\.?)?\s*[:.\-]|\bletter\s+no\b|संदर्भ|पत्रांक|पत्र\s*संख्या", re.I),
    re.compile(r"\b(sir|madam)\b|महोदय", re.I),
    re.compile(r"yours\s+(faithfully|sincerely|truly)|भवदीय", re.I),
    re.compile(r"\bcopy\s+to\b|प्रतिलिपि", re.I),
    re.compile(r"national\s+highways\s+authority|\bnhai\b|ministry\s+of\s+road\s+transport|\bmorth\b|"
               r"project\s+director|project\s+implementation\s+unit", re.I),
    re.compile(r"\bcircular\b|office\s+memorandum|office\s+order|minutes\s+of\s+(the\s+)?meeting|कार्यालय\s*ज्ञाप", re.I),
]


def name_says_letter(filename):
    stem, ext = os.path.splitext(os.path.basename(filename))
    ext = ext.lstrip(".").lower()
    name = re.sub(r"[_]+", " ", stem)
    if NAME_NOT_LETTER.search(name):
        return False
    if NAME_STRONG.search(name):
        return True
    # a date in the name ("3942 30.10.2026 IE.pdf") is enough for documents, not for photos
    return ext not in IMAGE_EXT and bool(NAME_DATE.search(name))


def text_says_letter(text):
    return sum(1 for rx in TEXT_MARKERS if rx.search(text or "")) >= 2


def peek_text(path, ext):
    """First part of the text inside a file. Returns None when the file type cannot be read here."""
    try:
        if ext == "pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return None
            reader = PdfReader(path)
            return "\n".join((p.extract_text() or "") for p in reader.pages[:2])[:8000]
        if ext == "docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))[:8000]
        if ext == "eml":
            with open(path, encoding="utf8", errors="ignore") as f:
                return f.read(8000)
        if ext in ("doc", "msg"):  # old binary formats: the text sits inside as 8-bit or UTF-16
            with open(path, "rb") as f:
                raw = f.read(300000)
            return raw.decode("latin-1", "ignore") + "\n" + raw.decode("utf-16-le", "ignore")
    except Exception:
        return None
    return None


def judge(path, take_everything=False, scanned="skip"):
    """Return (send?, reason)."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if take_everything:
        return True, "take-everything folder"
    if name_says_letter(path):
        return True, "file name"
    if ext in IMAGE_EXT:
        return False, "photo / scan whose name does not say it is a letter"
    text = peek_text(path, ext)
    if text is None:
        return False, "cannot look inside (install pypdf)" if ext == "pdf" else "cannot look inside"
    if not text.strip():
        if ext == "pdf" and scanned == "send":
            return True, "scanned PDF"
        return False, "scanned PDF whose name does not say it is a letter"
    if text_says_letter(text):
        return True, "text inside"
    return False, "does not read like a letter"


# ---------------------------------------------------------------- settings and memory
def app_dir():
    base = os.environ.get("DAK_COURIER_DIR")
    if not base:
        docs = os.path.join(HOME, "Documents")
        base = os.path.join(docs if os.path.isdir(docs) else HOME, "Dak-Courier")
    os.makedirs(base, exist_ok=True)
    return base


def load_config(base):
    path = os.path.join(base, "config.json")
    cfg = dict(DEFAULTS)
    if os.path.isfile(path):
        with open(path, encoding="utf8") as f:
            cfg.update(json.load(f))
    else:
        save_config(base, cfg)
    return cfg


def save_config(base, cfg):
    with open(os.path.join(base, "config.json"), "w", encoding="utf8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def open_state(base):
    db = sqlite3.connect(os.path.join(base, "courier.sqlite3"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS sent (sha TEXT PRIMARY KEY, src TEXT, dest TEXT, size INTEGER, sent_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, verdict TEXT, "
               "reason TEXT, checked_at TEXT)")
    db.commit()
    return db


def full(path):
    return os.path.realpath(os.path.expanduser(path))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- where is "Dak Inbox"?
def drive_roots():
    """Folders where Google Drive for desktop shows 'My Drive' on this computer."""
    roots = []
    cloud = os.path.join(HOME, "Library", "CloudStorage")  # macOS
    if os.path.isdir(cloud):
        for n in sorted(os.listdir(cloud)):
            if n.startswith("GoogleDrive"):
                roots.append(os.path.join(cloud, n))
    if platform.system() == "Windows":
        for letter in string.ascii_uppercase[3:]:
            roots.append(letter + ":\\")
    for n in ("Google Drive", "My Drive", "GoogleDrive"):
        roots.append(os.path.join(HOME, n))
    out = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        out.append(r)
        try:  # "My Drive" (or its name in another language) sits one level below
            out += [os.path.join(r, n) for n in sorted(os.listdir(r))
                    if not n.startswith(".") and os.path.isdir(os.path.join(r, n))]
        except OSError:
            pass
    return out


def find_inbox(cfg, roots=None):
    """Return (path, note). The Drive folder is called "Dak Inbox " (with a space at the end), so compare loosely."""
    if cfg.get("inbox"):
        return full(cfg["inbox"]), "set in config.json"
    for root in (drive_roots() if roots is None else roots):
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for n in sorted(names):
            if n.lower().startswith("dak inbox") and os.path.isdir(os.path.join(root, n)):
                return os.path.join(root, n), "Google Drive"
    # Google Drive's "My Drive" is not on this computer: use Desktop/Letters, which Drive already
    # syncs (Computers > My Mac > Desktop > Letters) and the Dak register sync already reads.
    desktop = os.path.join(HOME, "Desktop")
    if os.path.isdir(desktop):
        return os.path.join(desktop, "Letters"), "Dak Inbox not found in Google Drive - using Desktop/Letters"
    return None, "Dak Inbox not found. Open Google Drive for desktop, or set \"inbox\" in config.json"


# ---------------------------------------------------------------- one pass
def walk(root, avoid):
    for dirpath, dnames, fnames in os.walk(root):
        dnames[:] = [d for d in dnames if not d.startswith(".") and d.lower() not in SKIP_DIRS
                     and not d.lower().endswith((".app", ".photoslibrary", ".musiclibrary"))
                     and os.path.realpath(os.path.join(dirpath, d)) not in avoid]
        for n in fnames:
            yield os.path.join(dirpath, n)


def unique_dest(folder, name, digest):
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(name)  # same name, different letter: keep both, never "(1)"
    return os.path.join(folder, "%s [%s]%s" % (stem, digest[:6], ext))


def run_once(cfg, base, dry_run=False, backfill=False, now=None, roots=None, out=print):
    """Look through the folders once and copy new letters to the inbox. Returns a summary dict."""
    now = now or datetime.now()
    db = open_state(base)
    inbox, note = find_inbox(cfg, roots)
    summary = {"inbox": inbox, "note": note, "sent": [], "waiting": 0, "not_letters": 0, "errors": []}
    if not inbox:
        summary["errors"].append(note)
        return summary
    sub = cfg.get("subfolder") or ("PC folders" if platform.system() == "Windows" else "Mac folders")
    target = os.path.join(inbox, sub)
    avoid = {os.path.realpath(inbox), os.path.realpath(base)}
    since = 0 if backfill else float(cfg.get("since_days") or 0)
    oldest = (now - timedelta(days=since)).timestamp() if since else 0
    settle = now.timestamp() - float(cfg.get("settle_minutes") or 0) * 60
    max_bytes = float(cfg.get("max_mb") or 100) * 1024 * 1024

    todo = []
    plan = [(p, False) for p in cfg.get("folders", [])] + [(p, True) for p in cfg.get("folders_take_everything", [])]
    for folder, everything in plan:
        root = full(folder)
        if not os.path.isdir(root):
            summary["errors"].append("Folder not found: %s" % folder)
            continue
        try:
            for path in walk(root, avoid):
                name = os.path.basename(path)
                ext = os.path.splitext(name)[1].lstrip(".").lower()
                if ext not in LETTER_EXT or name.startswith((".", "~$")) or name.lower() in SKIP_NAMES:
                    continue
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size == 0 or st.st_size > max_bytes or st.st_mtime < oldest or st.st_mtime > settle:
                    continue
                row = db.execute("SELECT * FROM seen WHERE path = ?", (path,)).fetchone()
                if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                    if row["verdict"] == "no":
                        summary["not_letters"] += 1
                    continue  # already sent, already known as a copy, or already judged not a letter
                todo.append((st.st_mtime, path, st.st_size, everything))
        except PermissionError:
            summary["errors"].append("The system did not allow reading %s (see README: permissions)" % folder)

    since_24h = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    quota = int(cfg.get("max_per_day") or 60) - db.execute(
        "SELECT COUNT(*) FROM sent WHERE sent_at > ?", (since_24h,)).fetchone()[0]

    def remember(path, size, mtime, verdict, reason):
        if not dry_run:
            db.execute("INSERT OR REPLACE INTO seen VALUES (?, ?, ?, ?, ?, ?)",
                       (path, size, mtime, verdict, reason, now.isoformat(timespec="seconds")))

    # newest letters first; of two identical downloads, "name.pdf" goes before "name (1).pdf"
    todo.sort(key=lambda t: (-t[0], bool(COPY_MARK.search(t[1])), len(t[1]), t[1]))
    for mtime, path, size, everything in todo:
        try:
            ok, reason = judge(path, everything, cfg.get("scanned_pdfs", "skip"))
            if not ok:
                summary["not_letters"] += 1
                remember(path, size, mtime, "no", reason)
                continue
            digest = sha256(path)
            if db.execute("SELECT 1 FROM sent WHERE sha = ?", (digest,)).fetchone():
                remember(path, size, mtime, "copy", "same content already sent")
                continue
            if quota <= 0:
                summary["waiting"] += 1  # not remembered: it is tried again on the next pass
                continue
            ext = os.path.splitext(path)[1].lower()
            prev = db.execute("SELECT checked_at FROM seen WHERE path = ? AND verdict = 'sent'", (path,)).fetchone()
            if prev and ext in (".doc", ".docx") and prev["checked_at"] > since_24h:
                summary["waiting"] += 1  # a Word file being edited: at most one copy a day
                continue
            dest = unique_dest(target, os.path.basename(path), digest)
            if not dry_run:
                os.makedirs(target, exist_ok=True)
                shutil.copyfile(path, dest)
                if os.path.getsize(dest) != size:
                    raise IOError("copy is incomplete")
                db.execute("INSERT OR REPLACE INTO sent VALUES (?, ?, ?, ?, ?)",
                           (digest, path, dest, size, now.isoformat(timespec="seconds")))
                remember(path, size, mtime, "sent", reason)
                db.commit()
                with open(os.path.join(base, "sent-log.csv"), "a", newline="", encoding="utf8") as f:
                    csv.writer(f).writerow([now.isoformat(timespec="seconds"), path, dest, reason])
            quota -= 1
            summary["sent"].append((path, dest, reason))
        except Exception as e:  # one bad file must not stop the pass
            summary["errors"].append("%s: %s" % (os.path.basename(path), e))
    db.commit()
    db.close()
    return summary


def report(summary, dry_run=False, out=print):
    verb = "Would send" if dry_run else "Sent"
    for src, dest, reason in summary["sent"]:
        out("  %s: %s  (%s)" % (verb.lower(), src, reason))
    line = "%s %d letter(s) to %s" % (verb, len(summary["sent"]), summary["inbox"] or "-")
    if summary["waiting"]:
        line += "; %d more are waiting for the daily limit" % summary["waiting"]
    if summary["not_letters"]:
        line += "; %d file(s) left alone as not letters" % summary["not_letters"]
    out("%s  %s" % (datetime.now().strftime("%d.%m.%Y %H:%M"), line))
    if summary["note"] and summary["note"] not in ("Google Drive", "set in config.json"):
        out("  note: " + summary["note"])
    for e in summary["errors"][:10]:
        out("  problem: " + e)


def status(cfg, base, out=print):
    inbox, note = find_inbox(cfg)
    out("Settings file : " + os.path.join(base, "config.json"))
    out("Dak Inbox     : %s  (%s)" % (inbox or "NOT FOUND", note))
    out("Letter folders: " + (", ".join(cfg.get("folders", [])) or "-"))
    out("Take-everything folders: " + (", ".join(cfg.get("folders_take_everything", [])) or "-"))
    out("Only files of the last %s days; at most %s a day; scanned PDFs with unclear names: %s"
        % (cfg.get("since_days") or "all", cfg.get("max_per_day"), cfg.get("scanned_pdfs")))
    try:
        import pypdf  # noqa: F401
        out("Looking inside PDFs: yes")
    except ImportError:
        out("Looking inside PDFs: NO - run:  python3 -m pip install pypdf")
    db = open_state(base)
    total = db.execute("SELECT COUNT(*) FROM sent").fetchone()[0]
    out("Sent so far   : %d" % total)
    for r in db.execute("SELECT * FROM sent ORDER BY sent_at DESC LIMIT 10"):
        out("  %s  %s" % (r["sent_at"].replace("T", " "), r["src"]))
    skipped = db.execute("SELECT path, reason FROM seen WHERE verdict = 'no' ORDER BY checked_at DESC LIMIT 10").fetchall()
    if skipped:
        out("Recently left alone (rename the file with 'letter' / 'dated', or drop it in Dak Inbox yourself):")
        for r in skipped:
            out("  %s  - %s" % (r["path"], r["reason"]))
    db.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Copy letters from this computer's folders to the Google Drive Dak Inbox.")
    p.add_argument("--watch", action="store_true", help="keep running")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent, copy nothing")
    p.add_argument("--backfill", action="store_true", help="ignore the age limit for this pass")
    p.add_argument("--add", metavar="FOLDER", help="watch one more folder")
    p.add_argument("--everything", action="store_true", help="with --add: take every PDF / Word file / scan in it")
    p.add_argument("--remove", metavar="FOLDER", help="stop watching a folder")
    p.add_argument("--inbox", metavar="FOLDER", help="set the Dak Inbox folder by hand")
    p.add_argument("--status", action="store_true")
    a = p.parse_args(argv)
    base = app_dir()
    cfg = load_config(base)

    def same(x, y):
        return full(x) == full(y)

    if a.add or a.remove or a.inbox:
        target = a.add or a.remove
        if target:
            for key in ("folders", "folders_take_everything"):
                cfg[key] = [f for f in cfg.get(key, []) if not same(f, target)]
        if a.add:
            if not os.path.isdir(full(a.add)):
                print("Folder not found: " + a.add)
                return 1
            cfg["folders_take_everything" if a.everything else "folders"].append(a.add)
        if a.inbox:
            cfg["inbox"] = a.inbox
        save_config(base, cfg)
        status(cfg, base)
        return 0
    if a.status:
        status(cfg, base)
        return 0
    if not a.watch:
        report(run_once(cfg, base, a.dry_run, a.backfill), a.dry_run)
        return 0
    print("Dak Courier is running. Keep this window open; close it to stop.")
    first = True
    while True:
        cfg = load_config(base)  # picks up edits to config.json without a restart
        try:
            report(run_once(cfg, base, a.dry_run, a.backfill and first), a.dry_run)
        except Exception as e:
            print("  problem: %s" % e)
        first = False
        time.sleep(max(1, int(cfg.get("every_minutes") or 15)) * 60)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
