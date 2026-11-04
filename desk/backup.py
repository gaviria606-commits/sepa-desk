"""Backups and the portable migration bundle.

- run(): consistent SQLite snapshot (online backup API, safe while the server writes) plus a
  JSON export, into backups/<timestamp>/, keeping the newest KEEP copies.
- make_bundle(): one zip with the code and every document (including chart history), to move
  the desk between machines (work laptop -> home PC). restore_bundle() loads it on the other side.
"""
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime

from . import DB_PATH, ROOT

KEEP = int(os.environ.get("SEPA_BACKUP_KEEP", "14"))
BACKUP_DIR = os.environ.get("SEPA_BACKUP_DIR", os.path.join(ROOT, "backups"))
CODE_EXCLUDE = {"data", "backups", "reports", "__pycache__", ".git", ".venv", "venv", "dist"}


def run(store, dest=BACKUP_DIR):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(dest, stamp)
    os.makedirs(out, exist_ok=True)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(os.path.join(out, "desk.sqlite"))
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    n = store.export_dir(os.path.join(out, "json"), skip=())
    old = sorted(d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d)))
    for d in old[:-KEEP] if KEEP > 0 else []:
        shutil.rmtree(os.path.join(dest, d), ignore_errors=True)
    return f"Backup {out}: sqlite snapshot + {n} JSON documents (keeping {KEEP})."


def make_bundle(store, path):
    """Code + all data as JSON in one zip. Data goes to data/seed so a fresh install seeds from it."""
    path = os.path.abspath(path)
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(ROOT):
            rel = os.path.relpath(dirpath, ROOT)
            top = rel.split(os.sep)[0]
            if top in CODE_EXCLUDE or any(p.startswith(".") and p not in (".",) for p in rel.split(os.sep)):
                dirs[:] = []
                continue
            for f in files:
                full = os.path.join(dirpath, f)
                if os.path.abspath(full) == path or f.endswith((".pyc", ".sqlite", ".sqlite-wal", ".sqlite-shm")):
                    continue
                z.write(full, os.path.join("sepa-desk", os.path.relpath(full, ROOT)))
        for col in _collections(store):
            for d in store.list(col):
                z.writestr(f"sepa-desk/data/seed/{col}/{d['id']}.json", json.dumps(d["data"], indent=2))
                n += 1
        z.writestr("sepa-desk/data/seed/BUNDLE.txt",
                   f"Exported {datetime.now().isoformat(timespec='seconds')} with {n} documents.\n")
    return path, n


def restore_bundle(store, path, overwrite=True):
    """Load the documents from a bundle zip into this machine's database (code is not touched)."""
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            marker = "/data/seed/"
            if marker not in name or not name.endswith(".json"):
                continue
            rel = name.split(marker, 1)[1][:-5]
            if rel.count("/") != 1:
                continue
            if not overwrite and store.get(rel):
                continue
            store.set(rel, json.loads(z.read(name)))
            n += 1
    return n


def _collections(store):
    with store.lock:
        return [r[0] for r in store.conn.execute("SELECT DISTINCT coll FROM docs")]
