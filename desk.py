#!/usr/bin/env python3
"""SEPA + CAN SLIM Desk: command-line entry point.

  python3 desk.py serve [--host H] [--port 8765] [--no-scheduler]
                                             run the desk (default http://127.0.0.1:8765, with the scheduler)
  python3 desk.py update                     prices, market regime, RS line, Dalio momentum, chart history
  python3 desk.py job <daily|update|rsrank|news|backup>   run a scheduled job now, in the foreground
  python3 desk.py rsrank [--full] [TICKER ...]   Russell 3000 RS ranking now (--full re-downloads history);
                                             with tickers, prints their rank and 4-week change
  python3 desk.py backup                     SQLite snapshot + JSON export into backups/
  python3 desk.py bundle [file.zip]          code + ALL data in one zip, to move the desk to another PC
  python3 desk.py restore <file.zip>         load the data from a bundle into this PC's database
  python3 desk.py token                      create .env with a random SEPA_TOKEN (for remote access)
  python3 desk.py db get <path>              e.g. app/settings, stocks/GKOS
  python3 desk.py db list <collection>       e.g. stocks, positions, hist
  python3 desk.py db set <path> <json|@file|->  replace a document (- reads JSON from stdin)
  python3 desk.py db update <path> <json|@file>  merge fields ({"field": {"__delete__": true}} removes one)
  python3 desk.py db delete <path>
  python3 desk.py history <TICKER>           print the cached daily bars (compact format)
  python3 desk.py news                       run the news scan with Claude Code (same as the page button)
  python3 desk.py research                   run the daily research prompt with Claude Code
  python3 desk.py export [dir]               write all documents (except chart history) as JSON files
  python3 desk.py import <dir> [--overwrite] load documents from JSON files
"""
import json
import os
import sys

from desk import SEED_DIR, open_market, open_store


def _payload(arg):
    if arg == "-":
        return json.load(sys.stdin)
    if arg.startswith("@"):
        with open(arg[1:]) as fh:
            return json.load(fh)
    return json.loads(arg)


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    store = open_store()

    if cmd == "serve":
        from desk.server import serve
        port = int(rest[rest.index("--port") + 1]) if "--port" in rest else int(os.environ.get("SEPA_PORT", 8765))
        host = rest[rest.index("--host") + 1] if "--host" in rest else os.environ.get("SEPA_HOST", "127.0.0.1")
        serve(store, open_market(), host=host, port=port, schedule="--no-scheduler" not in rest)
    elif cmd == "job":
        from desk.scheduler import Scheduler
        name = rest[0] if rest else "daily"
        sch = Scheduler(store, open_market())
        sch.run_job(name, background=False)
        j = (store.get("app/jobs") or {})["data"][name]
        print(j.get("output", ""))
        return 0 if j.get("status") == "ok" else 1
    elif cmd == "rsrank":
        from desk import DB_PATH
        from desk.rsrank import RSRank
        rk = RSRank(DB_PATH, store, open_market())
        tickers = [t.upper() for t in rest if not t.startswith("--")]
        if tickers:
            with rk.lock:
                d = rk.conn.execute("SELECT MAX(d) FROM rank_hist").fetchone()[0]
                for t in tickers:
                    r = rk.conn.execute("SELECT rank FROM rank_hist WHERE d=? AND sym=?", (d, t)).fetchone()
                    print(f"{t}: RS {r[0] if r else 'n/a'} (as of {d})")
            return 0
        ok, msg = rk.run(full="--full" in rest)
        print(msg)
        return 0 if ok else 1
    elif cmd == "backup":
        from desk import backup
        print(backup.run(store))
    elif cmd == "bundle":
        from datetime import datetime
        from desk import backup
        path, n = backup.make_bundle(store, rest[0] if rest else f"sepa-desk-{datetime.now():%Y%m%d-%H%M}.zip")
        print(f"Bundle {path}: code + {n} documents")
    elif cmd == "restore":
        from desk import backup
        print(f"Restored {backup.restore_bundle(store, rest[0])} documents from {rest[0]}")
    elif cmd == "token":
        import secrets
        from desk import ROOT
        env = os.path.join(ROOT, ".env")
        if os.environ.get("SEPA_TOKEN"):
            print(f"SEPA_TOKEN already set (in {env} or the environment). Delete it there to make a new one.")
            return 1
        tok = secrets.token_urlsafe(24)
        with open(env, "a", encoding="utf-8") as fh:
            fh.write(f"SEPA_TOKEN={tok}\n")
        print(f"Wrote {env}\nYour access token (keep it in a password manager):\n{tok}")
    elif cmd == "update":
        from desk import update
        update.run(store, open_market())
    elif cmd == "db":
        if len(rest) < 2:
            print(__doc__); return 2
        op, path = rest[0], rest[1]
        if op == "get":
            d = store.get(path)
            print(json.dumps(d["data"] if d else None, indent=2))
            if not d:
                return 1
        elif op == "list":
            print(json.dumps({x["id"]: x["data"] for x in store.list(path)}, indent=2))
        elif op == "set":
            print("version", store.set(path, _payload(rest[2])))
        elif op == "update":
            print("version", store.update(path, _payload(rest[2])))
        elif op == "delete":
            store.delete(path); print("deleted", path)
        else:
            print(__doc__); return 2
    elif cmd == "history":
        from desk.market import bars_string
        print(bars_string(open_market().history(rest[0])))
    elif cmd in ("news", "research"):
        from desk import claude_jobs
        ok, out = claude_jobs.run_prompt("news-scan" if cmd == "news" else "daily-research")
        print(out)
        return 0 if ok else 1
    elif cmd == "export":
        d = rest[0] if rest else SEED_DIR
        print(f"Exported {store.export_dir(d)} documents to {d}")
    elif cmd == "import":
        print(f"Imported {store.import_dir(rest[0], overwrite='--overwrite' in rest)} documents")
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
