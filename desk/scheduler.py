"""In-process scheduler: runs the daily routine and backups while `desk.py serve` is up.

Keeps the home PC self-contained (no cron / Task Scheduler entries for the jobs themselves;
the OS only has to keep the server running). Times are in the desk's timezone
(SEPA_TZ, default Pacific/Auckland). Config lives in app/settings.schedule:

  {"daily": "10:15", "days": [1,2,3,4,5], "research": true, "news": "07:30", "backup": "02:00"}

days use Python weekday numbers in the desk timezone (Mon=0): Tue-Sat NZ = the US Mon-Fri close.
Each job's last run, status and output tail go to app/jobs so the page and Claude can see them.
"""
import os
import threading
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("SEPA_TZ", "Pacific/Auckland"))
DEFAULT = {"daily": "10:15", "days": [1, 2, 3, 4, 5], "research": True, "rsrank": True, "news": "", "backup": "02:00"}


class Scheduler:
    def __init__(self, store, market, log=print):
        self.store, self.market, self.log = store, market, log
        self.lock = threading.Lock()
        self.running = set()
        self._stop = threading.Event()

    # ----- config / state -----
    def config(self):
        s = (self.store.get("app/settings") or {}).get("data", {})
        return {**DEFAULT, **(s.get("schedule") or {})}

    def _jobs(self):
        return (self.store.get("app/jobs") or {}).get("data", {})

    def _record(self, name, **kw):
        cur = self._jobs().get(name, {})
        cur.update(kw)
        self.store.update("app/jobs", {name: cur})

    # ----- jobs -----
    def run_job(self, name, background=True):
        with self.lock:
            if name in self.running:
                return {"started": False, "reason": "already running"}
            self.running.add(name)

        def work():
            now = datetime.now(TZ)
            self._record(name, status="running", started=now.isoformat(timespec="seconds"), day=now.strftime("%Y-%m-%d"))
            ok, out = False, ""
            try:
                ok, out = self._do(name)
            except Exception:
                out = traceback.format_exc()
            finally:
                self._record(name, status="ok" if ok else "error", finished=datetime.now(TZ).isoformat(timespec="seconds"),
                             output=(out or "")[-3000:])
                with self.lock:
                    self.running.discard(name)
                self.log(f"[scheduler] {name}: {'ok' if ok else 'error'}")

        if background:
            threading.Thread(target=work, daemon=True, name=f"job-{name}").start()
            return {"started": True}
        work()
        return {"started": True, "done": True}

    def _do(self, name):
        from . import backup, claude_jobs, update
        if name == "update":
            r = update.run(self.store, self.market, log=lambda *_: None)
            return True, f"Updated {r['updated']} stocks; no data for: {', '.join(r['bad']) or 'none'}"
        if name == "rsrank":
            return self._rsrank()
        if name == "daily":
            r = update.run(self.store, self.market, log=lambda *_: None)
            out = f"Update: {r['updated']} stocks; no data for: {', '.join(r['bad']) or 'none'}\n"
            if self.config().get("rsrank"):
                ok_rs, rs_msg = self._rsrank()
                self._record("rsrank", status="ok" if ok_rs else "error", day=datetime.now(TZ).strftime("%Y-%m-%d"),
                             finished=datetime.now(TZ).isoformat(timespec="seconds"), output=rs_msg[-3000:])
                out += f"RS ranking: {rs_msg}\n"
            if not self.config().get("research"):
                return True, out + "Research disabled in settings.schedule."
            ok, rep = claude_jobs.run_prompt("daily-research")
            path = claude_jobs.save_report(rep)
            return ok, out + f"Report saved to {path}\n\n" + rep
        if name == "news":
            return claude_jobs.run_news_blocking(self.store)
        if name == "backup":
            return True, backup.run(self.store)
        raise ValueError(f"unknown job {name}")

    def _rsrank(self):
        from . import DB_PATH
        from .rsrank import RSRank
        try:
            return RSRank(DB_PATH, self.store, self.market, log=self.log).run()
        except Exception:
            return False, traceback.format_exc()[-2000:]

    # ----- loop -----
    def _due(self, name, hhmm, days, now, jobs):
        if not hhmm or (days is not None and now.weekday() not in days):
            return False
        try:
            h, m = (int(x) for x in hhmm.split(":"))
        except ValueError:
            return False
        if (now.hour, now.minute) < (h, m):
            return False
        last = jobs.get(name, {})
        # once per day; a missed slot (PC asleep) still runs when the server is next up that day
        return last.get("day") != now.strftime("%Y-%m-%d")

    def tick(self, now=None):
        now = now or datetime.now(TZ)
        cfg, jobs = self.config(), self._jobs()
        started = []
        days = cfg.get("days")
        for name, hhmm, d in (("daily", cfg.get("daily"), days), ("news", cfg.get("news"), days), ("backup", cfg.get("backup"), None)):
            if self._due(name, hhmm, d, now, jobs):
                self.run_job(name)
                started.append(name)
        return started

    def start(self):
        def loop():
            while not self._stop.wait(30):
                try:
                    self.tick()
                except Exception as e:
                    self.log(f"[scheduler] tick failed: {e}")
        threading.Thread(target=loop, daemon=True, name="scheduler").start()

    def stop(self):
        self._stop.set()
