"""Daily price history and quote data.

Primary source: Yahoo Finance via the `yfinance` package.
Fallback: Stooq's free daily CSV (no key needed) when yfinance fails.
Results are cached in SQLite so the UI and scripts don't hammer the sources.
"""
import csv
import io
import json
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta

HISTORY_DAYS = 440          # ~300 trading days: enough for the 200-day average plus a year of chart
CACHE_SECONDS = 10 * 60     # re-download a ticker at most every 10 minutes
INFO_SECONDS = 12 * 3600    # market cap / EPS refresh twice a day


class Market:
    def __init__(self, db_path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS bars (ticker TEXT PRIMARY KEY, fetched REAL, source TEXT, rows TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS info (ticker TEXT PRIMARY KEY, fetched REAL, data TEXT)")
        self.conn.commit()

    # ---------- history ----------
    def history(self, ticker, force=False):
        """Return a list of bars [{t:'YYYY-MM-DD', o,h,l,c, v(shares)}], oldest first. Empty list if unavailable."""
        t = ticker.upper().strip()
        with self.lock:
            row = self.conn.execute("SELECT fetched, rows FROM bars WHERE ticker=?", (t,)).fetchone()
        if row and not force and time.time() - row[0] < CACHE_SECONDS:
            return json.loads(row[1])
        bars, source = [], None
        sources = ((self._offline_history, "offline"),) if os.environ.get("SEPA_OFFLINE") else \
            ((self._yf_history, "yahoo"), (self._stooq_history, "stooq"))
        for fn, name in sources:
            try:
                bars = fn(t)
                if bars:
                    source = name
                    break
            except Exception:
                continue
        if bars:
            with self.lock:
                self.conn.execute(
                    "INSERT INTO bars(ticker, fetched, source, rows) VALUES(?,?,?,?) "
                    "ON CONFLICT(ticker) DO UPDATE SET fetched=excluded.fetched, source=excluded.source, rows=excluded.rows",
                    (t, time.time(), source, json.dumps(bars)),
                )
                self.conn.commit()
            return bars
        return json.loads(row[1]) if row else []

    def _offline_history(self, t):
        """SEPA_OFFLINE=1: read the sample history shipped in data/seed/hist (for tests without network)."""
        f = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed", "hist", f"{t}.json")
        if not os.path.exists(f):
            return []
        with open(f) as fh:
            s = json.load(fh).get("bars", "")
        out = []
        for row in s.split(";"):
            p = row.split(",")
            if len(p) >= 6:
                out.append({"t": f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:]}", "o": float(p[1]), "h": float(p[2]), "l": float(p[3]),
                            "c": float(p[4]), "v": int(float(p[5]) * 1000)})
        return out

    def _yf_history(self, t):
        import yfinance as yf  # imported lazily so the server starts even if it's missing
        start = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
        df = yf.Ticker(t).history(start=start, interval="1d", auto_adjust=False, actions=False)
        out = []
        for idx, r in df.iterrows():
            o, h, l, c, v = (float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]), float(r["Volume"]))
            if not (c > 0 and h > 0 and l > 0):
                continue
            out.append({"t": idx.strftime("%Y-%m-%d"), "o": round(o, 4), "h": round(h, 4), "l": round(l, 4), "c": round(c, 4), "v": int(v)})
        return out

    def _stooq_history(self, t):
        sym = t.lower().replace(".", "-") + ".us"
        url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
        with urllib.request.urlopen(url, timeout=20) as resp:
            text = resp.read().decode("utf-8", "replace")
        cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            try:
                if r["Date"] < cutoff:
                    continue
                out.append({"t": r["Date"], "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
                            "c": float(r["Close"]), "v": int(float(r.get("Volume") or 0))})
            except (KeyError, ValueError):
                continue
        return out

    # ---------- market cap / EPS ----------
    def info(self, ticker):
        t = ticker.upper().strip()
        with self.lock:
            row = self.conn.execute("SELECT fetched, data FROM info WHERE ticker=?", (t,)).fetchone()
        if row and time.time() - row[0] < INFO_SECONDS:
            return json.loads(row[1])
        data = {}
        try:
            import yfinance as yf
            fi = yf.Ticker(t).fast_info
            mc = fi.get("market_cap") if hasattr(fi, "get") else getattr(fi, "market_cap", None)
            if mc:
                data["marketCap"] = float(mc)
        except Exception:
            pass
        with self.lock:
            self.conn.execute(
                "INSERT INTO info(ticker, fetched, data) VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET fetched=excluded.fetched, data=excluded.data",
                (t, time.time(), json.dumps(data)),
            )
            self.conn.commit()
        return data


# ---------- calculations shared by the feed, the update script and the charts ----------
def sma(values, n, back=0):
    end = len(values) - back
    if end < n:
        return None
    return sum(values[end - n:end]) / n


def ret(closes, sessions):
    j = len(closes) - 1 - sessions
    if j < 0 or not closes[j]:
        return None
    return closes[-1] / closes[j] - 1


def feed_row(bars, info=None):
    """The same columns the Google Sheet feed produced."""
    if not bars:
        return None
    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    year = bars[-252:]
    return {
        "Price": closes[-1],
        "High52": max(b["h"] for b in year),
        "Low52": min(b["l"] for b in year),
        "AvgVolume": sma(vols, 50) or (sum(vols) / len(vols)),
        "Volume": vols[-1],
        "MarketCap": (info or {}).get("marketCap"),
        "EPS": None,
        "MA50": sma(closes, 50),
        "MA150": sma(closes, 150),
        "MA200": sma(closes, 200),
        "MA200_1mAgo": sma(closes, 200, back=22),
        "AsOf": bars[-1]["t"],
        "R3M": ret(closes, 63),
        "R6M": ret(closes, 126),
        "R12M": ret(closes, 252),
    }


def bars_string(bars):
    """Compact chart history: 'yyyymmdd,o,h,l,c,volK;...' (volume in thousands, like the Sheets version)."""
    return ";".join(
        f"{b['t'].replace('-', '')},{round(b['o'], 2)},{round(b['h'], 2)},{round(b['l'], 2)},{round(b['c'], 2)},{round(b['v'] / 1000)}"
        for b in bars
    )
