"""A local stand-in for the Google Drive / Google Sheets connector.

The desk page was written for claude.ai, where it creates Google Sheets full of
GOOGLEFINANCE formulas and reads them back. Locally, the browser shim sends those
same connector calls here. We remember which tickers each "sheet" holds and, when
the page reads it, compute the same table from Yahoo Finance data. The page code
doesn't need to know the difference.
"""
import base64
import csv
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from .market import bars_string, feed_row

FEED_COLS = ["Ticker", "Price", "High52", "Low52", "AvgVolume", "Volume", "MarketCap", "EPS",
             "MA50", "MA150", "MA200", "MA200_1mAgo", "AsOf", "R3M", "R6M", "R12M"]


class Sheets:
    def __init__(self, store, market):
        self.store = store      # DocStore: sheets are kept under the "_sheets" collection
        self.market = market

    # ----- connector tools -----
    def create_file(self, inp):
        text = inp.get("textContent") or ""
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            raise ValueError("empty sheet")
        header = [h.strip() for h in rows[0]]
        kind = "hist" if header[:2] == ["Ticker", "Bars"] else "feed"
        tickers = [r[0].strip().upper() for r in rows[1:] if r and r[0].strip()]
        sid = "local-" + uuid.uuid4().hex[:16]
        self.store.set(f"_sheets/{sid}", {"kind": kind, "tickers": tickers, "title": inp.get("title", "")})
        return {"id": sid, "title": inp.get("title", ""), "mimeType": "application/vnd.google-apps.spreadsheet"}

    def trash_file(self, inp):
        sid = inp.get("fileId")
        if sid and self.store.get(f"_sheets/{sid}"):
            self.store.delete(f"_sheets/{sid}")
        return {}

    def read_file_content(self, inp):
        sheet = self._sheet(inp.get("fileId"))
        return {"fileContent": self._markdown(sheet), "title": sheet.get("title", "")}

    def download_file_content(self, inp):
        sheet = self._sheet(inp.get("fileId"))
        csv_text = self._csv(sheet)
        return {"content": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"), "mimeType": "text/csv"}

    # ----- helpers -----
    def _sheet(self, sid):
        doc = self.store.get(f"_sheets/{sid}") if sid else None
        if not doc:
            raise KeyError(f"No local sheet with id {sid}. Click Update finance data (or Refresh chart data) to rebuild it.")
        return doc["data"]

    def _fetch_all(self, tickers):
        with ThreadPoolExecutor(max_workers=8) as ex:
            return dict(zip(tickers, ex.map(self.market.history, tickers)))

    def _markdown(self, sheet):
        tickers = sheet["tickers"]
        hist = self._fetch_all(tickers)
        lines = ["| " + " | ".join(FEED_COLS) + " |", "| " + " | ".join([":-:"] * len(FEED_COLS)) + " |"]
        for t in tickers:
            row = feed_row(hist.get(t) or [], self.market.info(t) if hist.get(t) else None)
            cells = [t]
            for col in FEED_COLS[1:]:
                v = row.get(col) if row else None
                if v is None:
                    cells.append("#N/A")
                elif isinstance(v, float):
                    cells.append(f"{v:.6g}" if abs(v) < 1e6 else f"{v:.0f}")
                else:
                    cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        return "## Table Context\n### Table Sample Data:\n" + "\n".join(lines)

    def _csv(self, sheet):
        tickers = sheet["tickers"]
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\r\n")
        if sheet["kind"] == "hist":
            hist = self._fetch_all(tickers)
            w.writerow(["Ticker", "Bars"])
            for t in tickers:
                bars = hist.get(t) or []
                w.writerow([t, bars_string(bars) if bars else "#N/A"])
        else:
            hist = self._fetch_all(tickers)
            w.writerow(FEED_COLS)
            for t in tickers:
                row = feed_row(hist.get(t) or [])
                w.writerow([t] + [("" if not row or row.get(c) is None else row.get(c)) for c in FEED_COLS[1:]])
        return out.getvalue()
