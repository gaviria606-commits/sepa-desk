"""The mechanical part of the daily routine: prices, market check, Dalio momentum, chart history.

This is what "Update finance data" does in the page, done from the command line so
scripts and Claude Code can run it without a browser. Research steps (IBD
distribution days, earnings, fundamentals, macro readings) live in prompts/daily-research.md.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import regime
from .market import bars_string, feed_row

MACRO = ["SPY", "QQQ", "IWM", "TLT", "IEF", "TIP", "GLD", "DBC", "UUP"]
INDEXES = {"spx": ("^GSPC", "SPY"), "ndx": ("^IXIC", "QQQ")}   # index first (real exchange volume), ETF fallback
IBD_FRESH_DAYS = 3


def rs_metrics(bars, bench):
    """IBD-style weighted performance (40% last quarter, 20% each earlier quarter) and whether the
    RS line (stock / SPY) is at a 52-week high: Minervini's 'RS line leads price' tell."""
    if len(bars) < 64 or len(bench) < 64:
        return {}
    c = [b["c"] for b in bars]
    def r(q):
        j = len(c) - 1 - 63 * q
        return c[-1] / c[j] - 1 if j >= 0 and c[j] else None
    parts = [(0.4, r(1)), (0.2, r(2)), (0.2, r(3)), (0.2, r(4))]
    have = [(w, x) for w, x in parts if x is not None]
    perf = sum(w * x for w, x in have) / sum(w for w, _ in have) if have else None
    bmap = {b["t"]: b["c"] for b in bench}
    line = [b["c"] / bmap[b["t"]] for b in bars[-252:] if bmap.get(b["t"])]
    out = {}
    if perf is not None:
        out["rsPerf"] = str(round(perf * 100, 1))
    if len(line) >= 60:
        out["rsLineHi"] = "1" if line[-1] >= max(line) * 0.995 else "0"
    return out


def _r(v, d=2):
    return None if v is None else round(v, d)


def watch_tickers(store):
    t = {d["data"].get("ticker", d["id"]).upper() for d in store.list("stocks")}
    t |= {d["data"].get("ticker", "").upper() for d in store.list("positions") if d["data"].get("ticker")}
    return sorted(x for x in t if x)


def run(store, market, log=print):
    tickers = watch_tickers(store)
    allt = sorted(set(tickers) | set(MACRO) | {i for i, _ in INDEXES.values()})
    with ThreadPoolExecutor(max_workers=8) as ex:
        hist = dict(zip(allt, ex.map(lambda t: market.history(t, force=True), allt)))
    rows, bad = {}, []
    for t in allt:
        if t.startswith("^"):
            continue
        bars = hist.get(t) or []
        if not bars:
            bad.append(t)
            continue
        rows[t] = feed_row(bars, market.info(t) if t in tickers else None)

    # stocks
    n = 0
    for d in store.list("stocks"):
        s, t = d["data"], d["data"].get("ticker", d["id"]).upper()
        r = rows.get(t)
        if not r:
            continue
        upd = {"price": _r(r["Price"]), "hi52": _r(r["High52"]), "lo52": _r(r["Low52"]), "ma50": _r(r["MA50"]),
               "ma150": _r(r["MA150"]), "ma200": _r(r["MA200"]), "ma200prev": _r(r["MA200_1mAgo"]),
               "avgvol": round(r["AvgVolume"] / 1000) if r["AvgVolume"] else None,
               "bovol": round(r["Volume"] / r["AvgVolume"] * 100) if r["AvgVolume"] else None,
               "mcap": _r(r["MarketCap"] / 1e9) if r.get("MarketCap") else None, "updated": r["AsOf"]}
        upd.update(rs_metrics(hist.get(t) or [], hist.get("SPY") or []))
        patch = {k: str(v) for k, v in upd.items() if v is not None and str(s.get(k, "")) != str(v)}
        if patch:
            store.update(f"stocks/{d['id']}", patch)
            n += 1
    # positions
    for d in store.list("positions"):
        t = (d["data"].get("ticker") or "").upper()
        if t in rows:
            p = str(_r(rows[t]["Price"]))
            if str(d["data"].get("price")) != p:
                store.update(f"positions/{d['id']}", {"price": p})

    # chart history for the watchlist
    for t in tickers:
        bars = hist.get(t) or []
        if bars:
            s = bars_string(bars)
            cur = store.get(f"hist/{t}")
            if not cur or cur["data"].get("bars") != s:
                store.set(f"hist/{t}", {"bars": s, "asOf": bars[-1]["t"], "n": len(bars),
                                        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # market check + Dalio momentum
    settings = (store.get("app/settings") or {}).get("data", {})
    market_doc = dict(settings.get("market") or {})
    spy, qqq = rows.get("SPY"), rows.get("QQQ")
    if spy and spy["MA50"] and spy["MA200"]:
        market_doc["spx50"] = spy["Price"] > spy["MA50"]
        market_doc["spx200"] = spy["Price"] > spy["MA200"]
    if qqq and qqq["MA50"] and qqq["MA200"]:
        market_doc["ndx50"] = qqq["Price"] > qqq["MA50"]
        market_doc["ndx200"] = qqq["Price"] > qqq["MA200"]
    apply_regime(market_doc, hist)
    if spy and qqq:   # only a check that actually got index data counts as "checked today"
        market_doc["updated"] = datetime.now(ZoneInfo("Pacific/Auckland")).strftime("%Y-%m-%d")
    else:
        log("WARNING: no SPY/QQQ data; the market check was NOT refreshed and keeps its old date.")
    cyc = {"asOf": None, "assets": {}}
    for t in MACRO:
        r = rows.get(t)
        if r:
            cyc["assets"][t] = {"price": r["Price"], "ma50": r["MA50"], "ma200": r["MA200"],
                                "r3": r["R3M"], "r6": r["R6M"], "r12": r["R12M"]}
            cyc["asOf"] = r["AsOf"]
    feed = dict(settings.get("feed") or {})
    now = datetime.now()
    feed.update({"lastRefresh": f"{now.month}/{now.day}/{now.year}, {now.strftime('%I:%M %p').lstrip('0')} (local)", "lastErrors": bad})
    store.update("app/settings", {"market": market_doc, "cycleMkt": cyc, "feed": feed})
    log(f"Updated {n} stock(s); {len(tickers)} watchlist/position tickers; no data for: {', '.join(bad) or 'none'}")
    return {"updated": n, "bad": bad}


def apply_regime(market_doc, hist):
    """Fill dspx/dndx/ftd from the computed regime, unless a dated IBD figure (set by the research
    job as src="IBD", srcDate=YYYY-MM-DD) is fresher than IBD_FRESH_DAYS. The computed view is
    always kept under market.calc so the two can be compared."""
    calc = {}
    for key, (idx, etf) in INDEXES.items():
        bars = hist.get(idx) or []
        src = idx
        if len(bars) < 30 or not any(b.get("v") for b in bars[-30:]):
            bars, src = hist.get(etf) or [], etf
        a = regime.analyse(bars)
        if a:
            a["src"] = src
            calc[key] = a
    if not calc:
        return
    market_doc["calc"] = calc
    try:
        ibd_age = (date.today() - date.fromisoformat(market_doc.get("srcDate", ""))).days
    except ValueError:
        ibd_age = None
    if market_doc.get("src") == "IBD" and ibd_age is not None and ibd_age <= IBD_FRESH_DAYS:
        return
    if "spx" in calc:
        market_doc["dspx"] = str(calc["spx"]["dd"])
    if "ndx" in calc:
        market_doc["dndx"] = str(calc["ndx"]["dd"])
    market_doc["ftd"] = all(c["uptrend"] for c in calc.values())
    market_doc["src"] = "computed"
