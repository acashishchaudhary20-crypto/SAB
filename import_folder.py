"""Import every file in one or more folders (and their sub-folders) from the command line.

    python3 import_folder.py ~/Desktop/SAB
    python3 import_folder.py "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/SAB" --as letters

Same rules as the Bulk Import page: files already imported are skipped.
"""
import argparse
import os

from sab import importer
from sab.app import ALLOWED_EXT, create_app

p = argparse.ArgumentParser()
p.add_argument("folders", nargs="+")
p.add_argument("--as", dest="force", choices=["letters", "documents"], help="file everything in one register")
a = p.parse_args()

app = create_app()
counts = {}
with app.app_context():
    for folder in a.folders:
        folder = os.path.expanduser(folder)
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for n in sorted(names):
                full = os.path.join(root, n)
                rel = os.path.relpath(full, os.path.dirname(folder.rstrip("/")))
                try:
                    r = importer.import_file(full, rel, app.config["UPLOAD_FOLDER"], ALLOWED_EXT, a.force)
                except Exception as e:
                    r = {"status": "error", "note": str(e), "module": None}
                counts[r["status"]] = counts.get(r["status"], 0) + 1
                print(f"{r['status']:9} {r.get('module') or '':10} {rel}  {r.get('note', '')}")
print("\nDone:", ", ".join(f"{v} {k}" for k, v in counts.items()))
