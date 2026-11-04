# SEPA + CAN SLIM Desk

Your Minervini SEPA + O'Neil CAN SLIM desk, running on your own computer, with all your data from claude.ai. It's the same page (Today, Watchlist, Charts, Positions, Rulebook), but prices come straight from Yahoo Finance instead of Google Sheets, your data lives in a local database, and Claude Code does the research jobs.

## Start it

You need Python 3.9+ and, for the research and news features, Claude Code.

```bash
cd sepa-desk
python3 -m pip install -r requirements.txt
python3 desk.py serve
```

Open http://127.0.0.1:8765. The first run loads your watchlist, settings, cycle panel and chart history from `data/seed/`. Click **Update finance data** to pull fresh prices for every stock, the indexes and the Dalio ETFs.

## Use it with Claude Code

Open this folder in Claude Code. `CLAUDE.md` tells Claude how the desk is built, the data model and the rules (it only updates data and reports, never trades). Things you can ask:

- "Run the daily research" (or run `python3 desk.py research`)
- "Add NVT to the watchlist and fill its fundamentals"
- "Change the coil detector to allow a 20% range"
- "Scan these tickers for VCPs: …"

## Daily routine (automatic)

`desk.py serve` has a built-in scheduler, so the home PC only needs the server running. Defaults (desk timezone `SEPA_TZ`, default Pacific/Auckland):

| Job | When | What |
|---|---|---|
| `daily` | 10:15 Tue–Sat (after the US close) | prices, market regime, RS line, Dalio momentum, chart history, then the Claude research job; report to `reports/<date>.md` |
| `backup` | 02:00 daily | SQLite snapshot + JSON export to `backups/`, newest 14 kept |
| `news` | off | set a time to run the news scan automatically |

Change it with `python3 desk.py db update app/settings '{"schedule": {"daily": "10:15", "days": [1,2,3,4,5], "news": "07:30", "backup": "02:00", "research": true}}'` (days: Mon=0). A slot missed because the PC was asleep runs as soon as the server is up again that day. Status and output of each run: the panel at the bottom-right of the page, `GET /api/jobs`, or `python3 desk.py db get app/jobs`. Run one now: `python3 desk.py job daily`.

## Market regime (computed, not looked up)

Every update computes O'Neil distribution days and follow-through days from S&P 500 / Nasdaq Composite bars (`^GSPC`, `^IXIC`; SPY/QQQ volume as fallback) in `desk/regime.py`, and fills `dspx`, `dndx` and `ftd` with `src: "computed"`. The research job compares against IBD and overrides only with a source dated in the last 3 days (`src: "IBD"`). The full computed view, including dates of each active distribution day, is in `app/settings.market.calc`. It is a model of IBD's rules: run it beside IBD for a few weeks before trusting it alone.

Each stock also gets `rsPerf` (IBD-style weighted 12-month performance, %) and `rsLineHi` ("1" when the stock/SPY line is at a 52-week high). Your `rs` field is never touched.

## RS ranking (Russell 3000)

After each daily update the server ranks every Russell 3000 stock on IBD's RS formula (40% weight on the latest quarter: `2·C/C63 + C/C126 + C/C189 + C/C252`) and converts it to a 1–99 percentile. The universe comes from iShares' IWV holdings file (refreshed weekly); prices from Yahoo in batches of 100.

- **First run** downloads ~14 months for ~3,000 tickers: allow 10–15 minutes and ~40 MB of disk. Later runs fetch one month in batches (2–3 minutes) and re-download a ticker's history only when a split or revision is detected.
- **Watchlist:** every stock gets `rsRank` and its 4-week change (▲/▼ in the RS column). `rs` is filled automatically when empty; a value you type is kept and the computed rank shows in brackets beside it. Clear the field to switch a stock back to automatic.
- **Leaders panel** (Watchlist tab): RS ≥ 80, price ≥ $10, 50-day volume ≥ 300K, with Minervini's 7 price checks. Filters for 7/7, your $0.3–10B mandate, RS rising, and **+ Watchlist** (prices and RS prefilled, fundamentals queued for Claude).
- `python3 desk.py rsrank` runs it now; `rsrank GKOS NVDA` looks up ranks; `rsrank --full` re-downloads everything.
- If a symbol never gets data (iShares and Yahoo spell it differently), add it to `data/ticker_map.json`: `{"BRKB": "BRK-B"}`. If the iShares download stops working, drop a `data/universe.csv` with a `Ticker` column.

Differences from IBD to expect: IBD ranks ~6,000+ US stocks, including non-Russell names, ADRs and recent IPOs, so the same stock can read a few points different. The ordering is what matters for the 80+ rule.

## Run it on your home PC, reach it from anywhere

1. Get the project onto the PC (git clone, or unzip a bundle; see "Moving between machines").
2. Install as an always-on service:
   - Windows: `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`
   - macOS / Linux: `./scripts/install-unix.sh`

   Both create `.venv`, install dependencies, write `.env` with a random `SEPA_TOKEN` (printed once: save it in your password manager), restore any `sepa-desk-*.zip` bundle found next to the project, and start the server at logon/boot with auto-restart.
3. Remote access with [Tailscale](https://tailscale.com) (free for personal use): install it on the PC and your phone/laptop, then on the PC run `tailscale serve --bg 8765`. Open `https://<pc-name>.<tailnet>.ts.net` from any of your devices and sign in with the token. Nothing is exposed to the public internet, TLS is automatic, and the server keeps listening on 127.0.0.1 only.
4. Install Claude Code on the PC and run `claude` once to sign in, so the research jobs work unattended.
5. Set the PC to never sleep while plugged in.

Security model: with `SEPA_TOKEN` set, every page and API call needs a session cookie (HttpOnly, SameSite=Strict, 30 days) or `Authorization: Bearer <token>`; writes from other origins are rejected (CSRF); `/api/health` is the only public route. The server refuses to bind to a non-loopback address without a token. Don't port-forward 8765 on your router: use Tailscale.

## Moving between machines

```bash
python3 desk.py bundle            # sepa-desk-YYYYMMDD-HHMM.zip: code + every document, chart history included
python3 desk.py restore <zip>     # on the other machine, after installing
```

The bundle never contains `.env` (your token) or the SQLite file. Git works too (`data/seed` holds JSON, the database is ignored); run `python3 desk.py export` before committing so `data/seed` reflects your latest data.

## Tests

```bash
python3 -m unittest discover -s tests     # regime, RS ranking, auth/CSRF, scheduler, bundle (offline)
node tools/test-patterns.js               # chart pattern engine
```

## How it maps to the claude.ai version

| claude.ai | Local |
|---|---|
| Artifact database | `data/desk.sqlite` (`python3 desk.py db …`) |
| Google Sheets with GOOGLEFINANCE | Yahoo Finance via `yfinance` (Stooq fallback) |
| Scheduled tasks (daily update, news scan) | built-in scheduler in `desk.py serve`, `claude -p` jobs |
| Google Drive connector calls from the page | answered by the local server (`desk/sheets.py`) |

`static/index.html` is the same file as the published artifact, so you can keep both versions. See `artifact/README.md` for republishing.

## Notes

- Yahoo Finance data is unofficial and can lag or occasionally fail; the server falls back to Stooq and keeps the last good data.
- Market cap comes from Yahoo. RS, group rank and institutional data still come from you or the research job.
- Back up `data/desk.sqlite`, or run `python3 desk.py export backup/` to save your data as JSON.
