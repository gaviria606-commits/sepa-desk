"""RS ranking pipeline with a fake iShares file and a fake Yahoo downloader (offline)."""
import os
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ.setdefault("SEPA_DB", os.path.join(TMP, "t.sqlite"))
os.environ["SEPA_OFFLINE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk.db import DocStore  # noqa: E402
from desk.rsrank import RSRank, parse_ishares_csv, percentile_ranks, rs_score, trend_template  # noqa: E402

N = 1200


def drift(i):
    return (i - N / 2) / N * 0.004        # ticker i: daily drift from -0.2% to +0.2%


def make_csv(n=N):
    head = 'iShares Russell 3000 ETF\nFund Holdings as of,"Nov 03, 2026"\nInception Date,"May 22, 2000"\n\n\n\n\n\n'
    rows = ['"Ticker","Name","Sector","Asset Class","Market Value","Weight (%)","Notional Value","Quantity","Price","Location","Exchange","Currency","FX Rate","Market Currency","Accrual Date"']
    rows += [f'"T{i:04d}","CO {i}","Information Technology","Equity","1","0.01","1","1","1","United States","NASDAQ","USD","1.00","USD","-"' for i in range(n)]
    rows += ['"BRKB","BERKSHIRE HATHAWAY INC CLASS B","Financials","Equity","1","0.01","1","1","1","United States","New York Stock Exchange Inc.","USD","1.00","USD","-"',
             '"USD","USD CASH","Cash and/or Derivatives","Cash","1","0.01","1","1","1","United States","-","USD","1.00","USD","-"',
             '"RTYZ6","RUSSELL 2000 EMINI DEC 26","Cash and/or Derivatives","Futures","1","0","1","1","1","United States","CME","USD","1.00","USD","-"']
    return head + "\n".join(rows) + '\n \n"The content contained herein is owned or licensed by BlackRock"\n'


class FakeYahoo:
    def __init__(self, sessions=300, split=None):
        self.sessions, self.split, self.calls = sessions, split, []
        self.days = []
        d = date.today() - timedelta(days=int(sessions * 1.45))
        while len(self.days) < sessions:
            if d.weekday() < 5:
                self.days.append(d.isoformat())
            d += timedelta(days=1)

    def bars(self, sym):
        rnd = random.Random(sym)
        i = int(sym[1:]) if sym.startswith("T") and sym[1:].isdigit() else N // 2
        c, out = 50.0, []
        for k, d in enumerate(self.days):
            c *= 1 + drift(i) + rnd.uniform(-0.004, 0.004)
            px = c / 2 if (self.split == sym and k < len(self.days) - 3) else c  # pre-split prices halve after re-adjustment
            out.append((d, px, 500_000.0))
        return out

    def __call__(self, symbols, period=None, start=None):
        self.calls.append((len(symbols), period, start))
        data = {s: self.bars(s) for s in symbols}
        if period:
            data = {s: b[-21:] for s, b in data.items()}
        return data


class Units(unittest.TestCase):
    def test_parse_ishares(self):
        rows = parse_ishares_csv(make_csv(5))
        self.assertEqual([r["ticker"] for r in rows], ["T0000", "T0001", "T0002", "T0003", "T0004", "BRKB"])

    def test_percentiles(self):
        r = percentile_ranks({"a": 1, "b": 2, "c": 3})
        self.assertEqual((r["a"], r["b"], r["c"]), (1, 50, 99))
        self.assertEqual(percentile_ranks({"a": 1, "b": 1})["a"], 50)

    def test_score_weights_recent_quarter(self):
        flat_then_up = [100.0] * 189 + [100.0 * (1.003 ** k) for k in range(1, 64)]
        up_then_flat = [100.0 * (1.003 ** k) for k in range(1, 64)] + [100.0 * 1.003 ** 63] * 189
        self.assertGreater(rs_score(flat_then_up), rs_score(up_then_flat))
        self.assertIsNone(rs_score([1.0] * 50))

    def test_trend_template(self):
        up = [50 * (1.002 ** k) for k in range(260)]
        self.assertEqual(trend_template(up)["pass"], 7)
        self.assertLess(trend_template(list(reversed(up)))["pass"], 3)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(TMP, f"{self._testMethodName}.sqlite")
        self.store = DocStore(self.db)
        self.store.set("stocks/T1150", {"ticker": "T1150", "rs": ""})                    # empty -> filled
        self.store.set("stocks/T1100", {"ticker": "T1100", "rs": "42"})                  # typed -> kept
        self.store.set("stocks/T0100", {"ticker": "T0100", "rs": "11", "rsSrc": "computed"})
        self.yahoo = FakeYahoo()
        self.rk = RSRank(self.db, self.store, None, downloader=self.yahoo, fetch=lambda url: make_csv(), log=lambda *_: None)

    def test_full_run(self):
        ok, msg = self.rk.run()
        self.assertTrue(ok, msg)
        lead = self.store.get("app/leaders")["data"]
        self.assertGreater(lead["n"], N * 0.95)
        ranks = {x["t"]: x["rank"] for x in lead["items"]}
        self.assertTrue(all(r >= 80 for r in ranks.values()))
        self.assertGreater(ranks.get("T1199", 0), 95)            # strongest drift ranks top
        self.assertNotIn("T0100", ranks)                         # weak names are not leaders
        s = {d["id"]: d["data"] for d in self.store.list("stocks")}
        self.assertEqual(s["T1150"]["rsSrc"], "computed")
        self.assertGreaterEqual(int(s["T1150"]["rs"]), 85)
        self.assertEqual(s["T1100"]["rs"], "42")                 # never overwrite a typed value
        self.assertGreaterEqual(int(s["T1100"]["rsRank"]), 80)   # ...but show the computed one beside it
        self.assertLessEqual(int(s["T0100"]["rs"]), 20)          # computed values keep updating
        self.assertIn("BRK-B", self.rk.symbols())                # class shares mapped to Yahoo symbols
        self.assertNotIn("USD", self.rk.symbols())

    def test_incremental_and_split(self):
        self.rk.run()
        first = list(self.yahoo.calls)
        self.assertTrue(all(c[2] for c in first))                # first run: full history
        self.yahoo.calls.clear()
        self.yahoo.split = "T0500"
        self.rk.update_prices()
        self.assertTrue(all(c[1] == "1mo" for c in self.yahoo.calls[:-1]))  # then incremental
        self.assertEqual(self.yahoo.calls[-1], (1, None, self.yahoo.calls[-1][2]))  # one re-download: the split
        with self.rk.lock:
            n = self.rk.conn.execute("SELECT COUNT(*) FROM px WHERE sym='T0500'").fetchone()[0]
        self.assertEqual(n, self.yahoo.sessions)

    def test_universe_download_failure_keeps_stored(self):
        self.rk.run()
        def boom(url):
            raise OSError("blocked")
        rk2 = RSRank(self.db, self.store, None, downloader=self.yahoo, fetch=boom, log=lambda *_: None)
        self.assertGreater(rk2.refresh_universe(force=True), N)


if __name__ == "__main__":
    unittest.main()
