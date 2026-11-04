"""A tiny document store on SQLite that mirrors the claude.ai artifact database.

Paths alternate collection/document, like the artifact store:
  "app/settings"  -> collection "app", document "settings"
  "stocks/GKOS"   -> collection "stocks", document "GKOS"
Every write bumps a global sequence number that the browser shim polls.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DELETE = "__delete__"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_path(path):
    parts = [p for p in str(path).strip("/").split("/") if p]
    if not parts or len(parts) % 2:
        raise ValueError(f"'{path}' is not a document path (it needs collection/doc segments)")
    return "/".join(parts[:-1]), parts[-1]


class DocStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, coll TEXT, id TEXT, data TEXT, version INTEGER, updated TEXT)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS docs_coll ON docs(coll)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.commit()

    # -- sequence used by the browser to notice changes
    def seq(self):
        with self.lock:
            row = self.conn.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
            return int(row[0]) if row else 0

    def _bump(self):
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES('seq', '1') ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1"
        )

    # -- reads
    def get(self, path):
        with self.lock:
            row = self.conn.execute("SELECT data, version, updated FROM docs WHERE path=?", (path.strip("/"),)).fetchone()
        if not row:
            return None
        return {"data": json.loads(row[0]), "version": row[1], "updated": row[2]}

    def list(self, coll):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, data, version, updated FROM docs WHERE coll=? ORDER BY id", (coll.strip("/"),)
            ).fetchall()
        return [{"id": r[0], "data": json.loads(r[1]), "version": r[2], "updated": r[3]} for r in rows]

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    # -- writes
    def set(self, path, data, if_version=None):
        coll, doc_id = split_path(path)
        p = f"{coll}/{doc_id}"
        if not isinstance(data, dict):
            raise ValueError("a document must be a JSON object")
        with self.lock:
            row = self.conn.execute("SELECT version FROM docs WHERE path=?", (p,)).fetchone()
            if if_version is not None and row and row[0] != if_version:
                raise RuntimeError(f"version mismatch on {p}: have {row[0]}, expected {if_version}")
            version = (row[0] + 1) if row else 1
            self.conn.execute(
                "INSERT INTO docs(path, coll, id, data, version, updated) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET data=excluded.data, version=excluded.version, updated=excluded.updated",
                (p, coll, doc_id, json.dumps(data), version, _now()),
            )
            self._bump()
            self.conn.commit()
            return version

    def update(self, path, patch, if_version=None):
        with self.lock:
            cur = self.get(path)
            data = dict(cur["data"]) if cur else {}
            for k, v in (patch or {}).items():
                if isinstance(v, dict) and v.get(DELETE) is True:
                    data.pop(k, None)
                else:
                    data[k] = v
            return self.set(path, data, if_version=if_version)

    def delete(self, path):
        with self.lock:
            self.conn.execute("DELETE FROM docs WHERE path=?", (path.strip("/"),))
            self._bump()
            self.conn.commit()

    # -- import / export as one JSON file per document (data/seed layout)
    def export_dir(self, root, skip=("hist",)):
        with self.lock:
            rows = self.conn.execute("SELECT path, data FROM docs").fetchall()
        n = 0
        for path, data in rows:
            if path.split("/")[0] in skip:
                continue
            f = os.path.join(root, path + ".json")
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w") as fh:
                json.dump(json.loads(data), fh, indent=2)
            n += 1
        return n

    def import_dir(self, root, overwrite=False):
        n = 0
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.endswith(".json"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)[:-5].replace(os.sep, "/")
                try:
                    split_path(rel)
                except ValueError:
                    continue
                if not overwrite and self.get(rel):
                    continue
                with open(full) as fh:
                    self.set(rel, json.load(fh))
                n += 1
        return n
