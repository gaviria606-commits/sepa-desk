"""Offline tests: python -m unittest discover -s tests   (no network, temp database)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp()
os.environ["SEPA_DB"] = os.path.join(TMP, "t.sqlite")
os.environ["SEPA_OFFLINE"] = "1"
os.environ["SEPA_BACKUP_DIR"] = os.path.join(TMP, "backups")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk import open_market, open_store, regime  # noqa: E402
from desk.scheduler import TZ, Scheduler  # noqa: E402
from desk.server import Auth, ThreadingHTTPServer, make_handler  # noqa: E402


def series(moves, vols=None, start=100.0):
    bars, c, d = [], start, datetime(2026, 1, 1)
    for i, m in enumerate(moves):
        c *= 1 + m
        bars.append({"t": (d + timedelta(days=i)).strftime("%Y-%m-%d"), "c": c, "l": c * 0.99, "v": (vols or [1000] * len(moves))[i]})
    return bars


class Regime(unittest.TestCase):
    def test_distribution_days_counted_and_expire(self):
        moves, vols = [0.001] * 40, [1000] * 40
        for i in (30, 33, 36):                      # three down days on rising volume
            moves[i], vols[i] = -0.006, 2000
        a = regime.analyse(series(moves, vols))
        self.assertEqual(a["dd"], 3)
        self.assertTrue(a["uptrend"])
        # 25+ sessions later they have expired
        a2 = regime.analyse(series(moves + [0.0005] * 30, vols + [1000] * 30))
        self.assertEqual(a2["dd"], 0)

    def test_low_volume_decline_is_not_distribution(self):
        moves = [0.001] * 40; moves[35] = -0.01
        self.assertEqual(regime.analyse(series(moves))["dd"], 0)

    def test_correction_then_follow_through(self):
        moves = [0.002] * 40 + [-0.02] * 8          # ~15% drawdown -> correction
        vols = [1000] * 48
        a = regime.analyse(series(moves, vols))
        self.assertFalse(a["uptrend"])
        # rally attempt: day1..3 small gains, day 5 +1.5% on higher volume = FTD
        moves2 = moves + [0.004, 0.003, 0.002, 0.001, 0.015]
        vols2 = vols + [1000, 1000, 1000, 1000, 1500]
        b = regime.analyse(series(moves2, vols2))
        self.assertTrue(b["uptrend"])
        self.assertIsNotNone(b["ftdDate"])

    def test_undercut_resets_rally(self):
        moves = [0.002] * 40 + [-0.02] * 8 + [0.004, 0.003, -0.03, 0.001, 0.015]
        vols = [1000] * 52 + [1500]
        self.assertFalse(regime.analyse(series(moves, vols))["uptrend"])   # day 2 after new low, too early


class UpdateTests(unittest.TestCase):
    def test_no_index_data_does_not_mark_market_fresh(self):
        from desk import update
        store = open_store()
        store.update("app/settings", {"market": {"spx50": True, "updated": "2020-01-01"}})
        update.run(store, open_market(), log=lambda *_: None)   # offline: no SPY/QQQ in seed
        self.assertEqual(store.get("app/settings")["data"]["market"]["updated"], "2020-01-01")

    def test_rs_metrics(self):
        from desk.update import rs_metrics
        bench = series([0.0005] * 260)
        lead = series([0.002] * 260)
        m = rs_metrics(lead, bench)
        self.assertEqual(m["rsLineHi"], "1")
        self.assertGreater(float(m["rsPerf"]), 0)


class AuthTests(unittest.TestCase):
    def test_cookie_roundtrip_and_tamper(self):
        a = Auth("x" * 24)
        c = a.make_cookie()
        self.assertTrue(a.check_cookie(c))
        self.assertFalse(a.check_cookie(c[:-1] + ("0" if c[-1] != "0" else "1")))
        self.assertFalse(Auth("y" * 24).check_cookie(c))
        self.assertFalse(a.check_token("nope"))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.market = open_store(), open_market()
        cls.tok = "t" * 24
        cls.sched = Scheduler(cls.store, cls.market, log=lambda *_: None)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store, cls.market, Auth(cls.tok), cls.sched))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, data=None, headers=None):
        r = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(r) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_health_is_public(self):
        code, body, _ = self.req("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["auth"])

    def test_page_requires_login(self):
        code, _, h = self.req("/")
        self.assertEqual(code, 303)
        self.assertEqual(h["Location"], "/login")
        self.assertEqual(self.req("/api/doc?path=app/settings")[0], 401)
        self.assertEqual(self.req("/claude-shim.js")[0], 303)

    def test_login_and_csrf(self):
        code, _, h = self.req("/login", b"token=" + self.tok.encode())
        self.assertEqual(code, 303)
        cookie = h["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["Set-Cookie"])
        code, body, _ = self.req("/", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertIn(b"claude-shim.js", body)
        self.assertIn(b"local-panel.js", body)
        w = json.dumps({"op": "update", "data": {"x": 1}}).encode()
        evil = {"Cookie": cookie, "Origin": "https://evil.example", "Content-Type": "application/json"}
        self.assertEqual(self.req("/api/doc?path=app/test", w, evil)[0], 403)
        ok = {"Cookie": cookie, "Origin": self.base, "Content-Type": "application/json"}
        self.assertEqual(self.req("/api/doc?path=app/test", w, ok)[0], 200)
        self.assertEqual(self.store.get("app/test")["data"]["x"], 1)

    def test_bad_login(self):
        self.assertEqual(self.req("/login", b"token=wrong")[0], 401)

    def test_bearer(self):
        code, body, _ = self.req("/api/jobs", headers={"Authorization": "Bearer " + self.tok})
        self.assertEqual(code, 200)
        self.assertIn("schedule", json.loads(body))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class SchedulerTests(unittest.TestCase):
    def test_due_once_per_day_and_weekdays(self):
        s = Scheduler(open_store(), open_market(), log=lambda *_: None)
        tue = datetime(2026, 11, 3, 10, 20, tzinfo=TZ)   # a Tuesday
        self.assertTrue(s._due("daily", "10:15", [1, 2, 3, 4, 5], tue, {}))
        self.assertFalse(s._due("daily", "10:15", [1, 2, 3, 4, 5], tue.replace(hour=9), {}))
        self.assertFalse(s._due("daily", "10:15", [1, 2, 3, 4, 5], tue, {"daily": {"day": "2026-11-03"}}))
        sun = datetime(2026, 11, 8, 11, 0, tzinfo=TZ)
        self.assertFalse(s._due("daily", "10:15", [1, 2, 3, 4, 5], sun, {}))
        self.assertTrue(s._due("backup", "02:00", None, sun, {}))

    def test_backup_job_runs(self):
        store = open_store()
        s = Scheduler(store, open_market(), log=lambda *_: None)
        s.run_job("backup", background=False)
        self.assertEqual(store.get("app/jobs")["data"]["backup"]["status"], "ok")


class BundleTests(unittest.TestCase):
    def test_bundle_restore_roundtrip(self):
        from desk import backup
        from desk.db import DocStore
        store = open_store()
        store.set("stocks/ZZZT", {"ticker": "ZZZT", "pivot": "12.5"})
        env = os.path.join(backup.ROOT, ".env")
        made_env = not os.path.exists(env)
        if made_env:
            open(env, "w").write("SEPA_TOKEN=must-not-be-bundled\n")
        try:
            path, n = backup.make_bundle(store, os.path.join(TMP, "b.zip"))
        finally:
            if made_env:
                os.remove(env)
        names = zipfile.ZipFile(path).namelist()
        self.assertIn("sepa-desk/desk.py", names)
        self.assertIn("sepa-desk/data/seed/stocks/ZZZT.json", names)
        self.assertFalse(any(x.endswith(".sqlite") or "/.env" in x for x in names))
        other = DocStore(os.path.join(TMP, "other.sqlite"))
        self.assertEqual(backup.restore_bundle(other, path), n)
        self.assertEqual(other.get("stocks/ZZZT")["data"]["pivot"], "12.5")

    def test_restore_once_never_rolls_back_live_data(self):
        from desk import backup
        from desk.db import DocStore
        d = tempfile.mkdtemp(dir=TMP)
        src = DocStore(os.path.join(d, "src.sqlite"))
        src.set("app/settings", {"account": 2822})
        backup.make_bundle(src, os.path.join(d, "sepa-desk-20261105-1530.zip"))
        live = DocStore(os.path.join(d, "live.sqlite"))
        marker = os.path.join(d, ".restored")
        self.assertIn("Restored", backup.restore_once(live, [d], marker))
        live.set("app/settings", {"account": 9999})  # weeks of real use
        self.assertIn("already restored", backup.restore_once(live, [d], marker))
        self.assertEqual(live.get("app/settings")["data"]["account"], 9999)
        self.assertIn("nothing to restore", backup.restore_once(live, [tempfile.mkdtemp(dir=TMP)], marker))


if __name__ == "__main__":
    unittest.main()
