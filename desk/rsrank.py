"""Russell 3000 relative-strength ranking (IBD-style RS Rating, 1-99) and the leaders list.

Pipeline (runs in the daily job, after the US close):
  1. Universe: iShares IWV (Russell 3000 ETF) holdings, refreshed weekly. Fallbacks: the last
     universe stored here, then data/universe.csv (Ticker[,Name,Sector]) if you drop one in.
     Watchlist and position tickers are always added.
  2. Prices: daily close + volume for every ticker in SQLite table `px`. First run downloads
     ~14 months (several minutes); later runs fetch only the last month in batches. A split or
     data revision (overlapping close off by >2%) triggers a full re-download of that ticker.
  3. Score: IBD's weighting, 40% on the latest quarter: 2*C/C63 + C/C126 + C/C189 + C/C252
     (legs renormalised for stocks listed under a year; at least 63 sessions required).
  4. Rank: percentile across the universe, 1-99. Daily ranks kept in `rank_hist` (for the
     4-week change, i.e. whether RS is improving into a base).
  5. Leaders: rank >= 80, Minervini trend template on closes, O'Neil liquidity (price >= $10,
     50-day volume >= 300K). Market cap looked up for those only. Written to app/leaders.
  6. Watchlist: rsRank / rsRank4w on every stock. `rs` itself is filled only when empty or
     previously filled by this job (rsSrc == "computed"); a value you typed is never replaced.
"""
import csv
import io
import re
import json
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta

from . import ROOT

IWV_URL = os.environ.get(
    "SEPA_UNIVERSE_URL",
    "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/1467271812596.ajax"
    "?fileType=csv&fileName=IWV_holdings&dataType=fund")
UNIVERSE_FILE = os.path.join(ROOT, "data", "universe.csv")
TICKER_MAP_FILE = os.path.join(ROOT, "data", "ticker_map.json")
UNIVERSE_MAX_AGE_DAYS = 7
FULL_DAYS = 430            # ~295 sessions: 252 for the score + 22 for the 200-day a month ago
INCR_PERIOD = "1mo"
KEEP_DAYS = 450
BATCH = 100
MIN_SESSIONS = 63
LEADER_MIN_RANK = 80
LEADER_MAX = 200
MIN_PRICE, MIN_VOL = 10.0, 300_000
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-/ ]{0,7}$")
# iShares writes share classes without a separator; Yahoo uses a dash.
CLASS_SHARES = {"BRKB": "BRK-B", "BFB": "BF-B", "BFA": "BF-A", "LENB": "LEN-B", "HEIA": "HEI-A", "MOGA": "MOG-A",
                "MOGB": "MOG-B", "UHALB": "UHAL-B", "CWENA": "CWEN-A", "GEFB": "GEF-B", "BIOB": "BIO-B",
                "FOXA": "FOXA", "NWSA": "NWSA", "LBRDA": "LBRDA", "LBRDK": "LBRDK", "ZG": "ZG"}


def _yahoo_symbol(t, overrides):
    t = t.strip().upper()
    if t in overrides:
        return overrides[t]
    if t in CLASS_SHARES:
        return CLASS_SHARES[t]
    return t.replace(".", "-").replace("/", "-").replace(" ", "-")


def parse_ishares_csv(text):
    """iShares holdings CSV: ~9 preamble lines, then a header starting with 'Ticker'. Equities only."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.lstrip('﻿"').startswith("Ticker")), None)
    if start is None:
        return []
    out = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        t = (r.get("Ticker") or "").strip().upper()
        if (r.get("Asset Class") or "").strip() != "Equity" or not TICKER_RE.match(t) or not any(c.isalpha() for c in t):
            continue
        out.append({"ticker": t.upper(), "name": (r.get("Name") or "").strip().title(),
                    "sector": (r.get("Sector") or "").strip()})
    return out


def _http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sepa-desk)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")


def yf_download(symbols, period=None, start=None):
    """Batch daily bars from Yahoo: {symbol: [(YYYY-MM-DD, close, volume), ...]} oldest first."""
    import yfinance as yf
    df = yf.download(symbols, period=period, start=start, interval="1d", group_by="ticker",
                     auto_adjust=False, actions=False, threads=True, progress=False)
    out = {}
    if df is None or df.empty:
        return out
    multi = hasattr(df.columns, "levels") and df.columns.nlevels > 1
    for s in symbols:
        try:
            sub = df[s] if multi else df
        except KeyError:
            continue
        rows = []
        for idx, r in sub.iterrows():
            c, v = r.get("Close"), r.get("Volume")
            if c is None or c != c or c <= 0:      # NaN-safe
                continue
            rows.append((idx.strftime("%Y-%m-%d"), float(c), float(v) if v == v and v is not None else 0.0))
        if rows:
            out[s] = rows
    return out


def sma(xs, n, back=0):
    end = len(xs) - back
    return sum(xs[end - n:end]) / n if end >= n else None


def rs_score(closes):
    n = len(closes)
    if n < MIN_SESSIONS + 1:
        return None
    c = closes[-1]
    num = den = 0.0
    for w, k in ((2, 63), (1, 126), (1, 189), (1, 252)):
        if n > k and closes[-1 - k] > 0:
            num += w * c / closes[-1 - k]
            den += w
    return num / den if den else None


def percentile_ranks(scores):
    """{t: score} -> {t: 1..99}; ties share a rank."""
    items = sorted(scores.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 99}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        r = 1 + int(round(98 * ((i + j) / 2) / (n - 1)))
        for k in range(i, j + 1):
            out[items[k][0]] = r
        i = j + 1
    return out


def trend_template(closes):
    """Minervini's 7 price criteria on closes (RS is the 8th, handled by the rank). None if < 222 sessions."""
    if len(closes) < 222:
        return None
    p = closes[-1]
    m50, m150, m200, m200p = sma(closes, 50), sma(closes, 150), sma(closes, 200), sma(closes, 200, 22)
    yr = closes[-252:]
    hi, lo = max(yr), min(yr)
    checks = [p > m150 and p > m200, m150 > m200, m200 > m200p, m50 > m150 and m50 > m200, p > m50,
              p >= lo * 1.3, p >= hi * 0.75]
    return {"pass": sum(checks), "m50": m50, "m150": m150, "m200": m200, "m200p": m200p, "hi": hi, "lo": lo}


class RSRank:
    def __init__(self, db_path, store, market=None, downloader=None, fetch=None, log=print):
        self.store, self.market, self.log = store, market, log
        self.download = downloader or yf_download
        self.fetch = fetch or _http_get
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS univ (ticker TEXT PRIMARY KEY, sym TEXT, name TEXT, sector TEXT, src TEXT, seen TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS px (sym TEXT, d TEXT, c REAL, v REAL, PRIMARY KEY (sym, d)) WITHOUT ROWID")
        c.execute("CREATE TABLE IF NOT EXISTS rank_hist (d TEXT, sym TEXT, rank INTEGER, PRIMARY KEY (d, sym)) WITHOUT ROWID")
        c.execute("CREATE TABLE IF NOT EXISTS rs_meta (k TEXT PRIMARY KEY, v TEXT)")
        c.commit()

    # ----- meta -----
    def _meta(self, k, v=None):
        with self.lock:
            if v is None:
                r = self.conn.execute("SELECT v FROM rs_meta WHERE k=?", (k,)).fetchone()
                return r[0] if r else None
            self.conn.execute("INSERT INTO rs_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.conn.commit()

    # ----- 1. universe -----
    def refresh_universe(self, force=False):
        last = self._meta("universe_at")
        fresh = last and (datetime.now() - datetime.fromisoformat(last)).days < UNIVERSE_MAX_AGE_DAYS
        have = self.conn.execute("SELECT COUNT(*) FROM univ WHERE src != 'desk'").fetchone()[0]
        overrides = {}
        if os.path.exists(TICKER_MAP_FILE):
            with open(TICKER_MAP_FILE) as fh:
                overrides = {k.upper(): v for k, v in json.load(fh).items()}
        rows, src = [], None
        if force or not fresh or not have:
            try:
                rows, src = parse_ishares_csv(self.fetch(IWV_URL)), "IWV"
                if len(rows) < 1000:
                    raise ValueError(f"only {len(rows)} holdings parsed")
            except Exception as e:
                self.log(f"[rsrank] universe download failed ({e}); using the stored universe")
                rows, src = [], None
            if not rows and not have and os.path.exists(UNIVERSE_FILE):
                with open(UNIVERSE_FILE, encoding="utf-8-sig") as fh:
                    rows = [{"ticker": (r.get("Ticker") or r.get("ticker") or "").strip().upper(),
                             "name": r.get("Name", ""), "sector": r.get("Sector", "")} for r in csv.DictReader(fh)]
                rows, src = [r for r in rows if r["ticker"]], "file"
        today = date.today().isoformat()
        with self.lock:
            if rows:
                self.conn.execute("DELETE FROM univ WHERE src != 'desk'")
                self.conn.executemany(
                    "INSERT OR REPLACE INTO univ(ticker, sym, name, sector, src, seen) VALUES(?,?,?,?,?,?)",
                    [(r["ticker"], _yahoo_symbol(r["ticker"], overrides), r["name"], r["sector"], src, today) for r in rows])
                self._meta("universe_at", datetime.now().isoformat(timespec="seconds"))
                self._meta("universe_src", src)
            # desk tickers are always ranked
            self.conn.execute("DELETE FROM univ WHERE src = 'desk'")
            ids = {d["data"].get("ticker", d["id"]).upper() for d in self.store.list("stocks")}
            ids |= {(d["data"].get("ticker") or "").upper() for d in self.store.list("positions")}
            known = {r[0] for r in self.conn.execute("SELECT sym FROM univ")}
            self.conn.executemany("INSERT OR IGNORE INTO univ(ticker, sym, name, sector, src, seen) VALUES(?,?,?,?,?,?)",
                                  [(t, t, "", "", "desk", today) for t in sorted(ids) if t and t not in known])
            self.conn.commit()
            return self.conn.execute("SELECT COUNT(*) FROM univ").fetchone()[0]

    def symbols(self):
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT DISTINCT sym FROM univ ORDER BY sym")]

    # ----- 2. prices -----
    def _last_dates(self):
        with self.lock:
            return dict(self.conn.execute("SELECT sym, MAX(d) FROM px GROUP BY sym"))

    def _counts(self):
        with self.lock:
            return dict(self.conn.execute("SELECT sym, COUNT(*) FROM px GROUP BY sym"))

    def update_prices(self, syms=None, full=False):
        syms = syms or self.symbols()
        last, counts = self._last_dates(), self._counts()
        stale_cut = (date.today() - timedelta(days=20)).isoformat()
        need_full = [s for s in syms if full or counts.get(s, 0) < 230 or (last.get(s) or "") < stale_cut]
        full_set = set(need_full)
        need_incr = [s for s in syms if s not in full_set]
        got, revised = 0, []
        start = (date.today() - timedelta(days=FULL_DAYS)).isoformat()
        for group, kw in ((need_full, {"start": start}), (need_incr, {"period": INCR_PERIOD})):
            for i in range(0, len(group), BATCH):
                chunk = group[i:i + BATCH]
                try:
                    data = self.download(chunk, **kw)
                except Exception as e:
                    self.log(f"[rsrank] batch {i // BATCH} failed: {e}")
                    time.sleep(2)
                    continue
                for s, rows in data.items():
                    if "period" in kw and self._revised(s, rows):
                        revised.append(s)
                        continue
                    self._store_rows(s, rows)
                    got += 1
        if revised:   # splits / revisions: replace the whole history
            for i in range(0, len(revised), BATCH):
                chunk = revised[i:i + BATCH]
                try:
                    data = self.download(chunk, start=start)
                except Exception as e:
                    self.log(f"[rsrank] re-download failed: {e}")
                    continue
                for s, rows in data.items():
                    with self.lock:
                        self.conn.execute("DELETE FROM px WHERE sym=?", (s,))
                    self._store_rows(s, rows)
        with self.lock:
            self.conn.execute("DELETE FROM px WHERE d < ?", ((date.today() - timedelta(days=KEEP_DAYS)).isoformat(),))
            self.conn.commit()
        return {"requested": len(syms), "updated": got, "full": len(need_full), "revised": len(revised)}

    def _revised(self, s, rows):
        with self.lock:
            old = dict(self.conn.execute("SELECT d, c FROM px WHERE sym=? AND d >= ?", (s, rows[0][0])))
        for d, c, _ in rows[:-1]:   # the last bar may be today's still-moving close
            if d in old and old[d] and abs(c / old[d] - 1) > 0.02:
                return True
        return False

    def _store_rows(self, s, rows):
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO px(sym, d, c, v) VALUES(?,?,?,?)", [(s, d, c, v) for d, c, v in rows])
            self.conn.commit()

    def series(self):
        out = {}
        with self.lock:
            for s, c, v in self.conn.execute("SELECT sym, c, v FROM px ORDER BY sym, d"):
                a = out.get(s)
                if a is None:
                    a = out[s] = ([], [])
                a[0].append(c)
                a[1].append(v)
        return out

    # ----- 3-5. rank + leaders -----
    def compute(self):
        data = self.series()
        with self.lock:
            asof_row = self.conn.execute("SELECT MAX(d) FROM px").fetchone()
            meta = {r[0]: (r[1], r[2], r[3]) for r in self.conn.execute("SELECT sym, ticker, name, sector FROM univ")}
            lastd = dict(self.conn.execute("SELECT sym, MAX(d) FROM px GROUP BY sym"))
        asof = asof_row[0] if asof_row else None
        if not asof:
            return None
        # stocks whose last bar is >5 days older than the newest are delisted/halted: skip
        cut = (date.fromisoformat(asof) - timedelta(days=5)).isoformat()
        scores = {}
        for s, (c, _) in data.items():
            if s not in meta or (lastd.get(s) or "") < cut:
                continue
            sc = rs_score(c)
            if sc is not None:
                scores[s] = sc
        ranks = percentile_ranks(scores)
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO rank_hist(d, sym, rank) VALUES(?,?,?)", [(asof, s, r) for s, r in ranks.items()])
            self.conn.execute("DELETE FROM rank_hist WHERE d < ?", ((date.fromisoformat(asof) - timedelta(days=400)).isoformat(),))
            days = [r[0] for r in self.conn.execute("SELECT DISTINCT d FROM rank_hist WHERE d < ? ORDER BY d DESC LIMIT 20", (asof,))]
            prev = dict(self.conn.execute("SELECT sym, rank FROM rank_hist WHERE d=?", (days[-1],))) if len(days) >= 20 else {}
            self.conn.commit()
        leaders = []
        for s, r in ranks.items():
            if r < LEADER_MIN_RANK:
                continue
            c, v = data[s]
            tt = trend_template(c)
            av = sma(v, 50)
            if not tt or c[-1] < MIN_PRICE or not av or av < MIN_VOL:
                continue
            tk, name, sector = meta.get(s, (s, "", ""))
            leaders.append({"t": tk, "sym": s, "name": name, "sector": sector, "rank": r, "rank4w": prev.get(s),
                            "tt": tt["pass"], "c": round(c[-1], 2), "offHi": round((c[-1] / tt["hi"] - 1) * 100, 1),
                            "m50": round(tt["m50"], 2), "m150": round(tt["m150"], 2), "m200": round(tt["m200"], 2),
                            "m200p": round(tt["m200p"], 2), "hi": round(tt["hi"], 2), "lo": round(tt["lo"], 2),
                            "av": round(av / 1000), "vr": round(v[-1] / av * 100) if av else None})
        leaders.sort(key=lambda x: (-(x["tt"] == 7), -x["rank"], x["offHi"] * -1))
        leaders = leaders[:LEADER_MAX]
        return {"asOf": asof, "ranks": ranks, "prev": prev, "leaders": leaders, "n": len(ranks)}

    def _caps(self, leaders):
        if not self.market:
            return
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            infos = list(ex.map(lambda x: self.market.info(x["sym"]), leaders))
        for x, info in zip(leaders, infos):
            mc = (info or {}).get("marketCap")
            x["mcap"] = round(mc / 1e9, 2) if mc else None

    # ----- 6. publish -----
    def publish(self, res):
        leaders = res["leaders"]
        self._caps(leaders)
        on_desk = {d["id"].upper() for d in self.store.list("stocks")}
        for x in leaders:
            x["onDesk"] = x["t"] in on_desk or x["sym"] in on_desk
        self.store.set("app/leaders", {
            "asOf": res["asOf"], "n": res["n"], "universe": self._meta("universe_src") or "",
            "universeAt": self._meta("universe_at") or "", "updated": datetime.now().isoformat(timespec="seconds"),
            "rule": f"RS >= {LEADER_MIN_RANK}, trend template on closes, price >= ${MIN_PRICE:.0f}, 50-day volume >= 300K",
            "items": leaders})
        ranks, prev = res["ranks"], res["prev"]
        filled = []
        for d in self.store.list("stocks"):
            s, t = d["data"], d["id"].upper()
            r = ranks.get(t) or ranks.get(_yahoo_symbol(t, {}))
            if r is None:
                continue
            patch = {"rsRank": str(r), "rsAsOf": res["asOf"]}
            p = prev.get(t)
            if p is not None:
                patch["rsRank4w"] = str(p)
            cur_rs = str(s.get("rs") or "").strip()
            if not cur_rs or s.get("rsSrc") == "computed":
                patch["rs"], patch["rsSrc"] = str(r), "computed"
                if cur_rs != str(r):
                    filled.append(t)
            patch = {k: v for k, v in patch.items() if str(s.get(k, "")) != v}
            if patch:
                self.store.update(f"stocks/{d['id']}", patch)
        return filled

    def run(self, full=False):
        t0 = time.time()
        n_univ = self.refresh_universe()
        px = self.update_prices(full=full)
        res = self.compute()
        if not res:
            return False, f"No price data for the universe ({n_univ} tickers). {px}"
        filled = self.publish(res)
        top = [f"{x['t']} {x['rank']}" for x in res["leaders"] if x["tt"] == 7 and not x.get("onDesk")][:10]
        msg = (f"RS ranks for {res['n']} of {n_univ} tickers as of {res['asOf']} "
               f"({px['updated']} downloaded, {px['revised']} re-downloaded after splits) in {time.time() - t0:.0f}s. "
               f"{len(res['leaders'])} leaders. rs filled on: {', '.join(filled) or 'none'}. "
               f"Top new 7/7 leaders: {', '.join(top) or 'none'}.")
        coverage = res["n"] / max(n_univ, 1)
        if coverage < 0.8:
            msg += f" WARNING: only {coverage:.0%} of the universe has data; ranks are less reliable."
        return True, msg
