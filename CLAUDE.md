# SEPA + CAN SLIM Desk

Juan's trading desk for Mark Minervini's SEPA/VCP method combined with William O'Neil's CAN SLIM, focused on small and mid caps ($300M–$10B; Physical AI, sensors and data-center infrastructure are the mandate, but the watchlist can hold anything). It started as a claude.ai artifact and now also runs locally.

## Ground rules for Claude
- Update data and report only. Never place trades, never change Juan's stops, verdict rules, mode or account settings, and never delete watchlist stocks or positions unless he asks.
- Never invent numbers. Fundamentals come from company releases/SEC filings first. Leave a field blank (and say why in notes) rather than guess. Don't overwrite a value Juan typed (especially `rs`) unless a newer quarter was reported.
- Distribution days and the follow-through state are computed by `desk/regime.py` (`market.src = "computed"`). Override with IBD figures only from a source dated within 3 days (`src: "IBD"`, `srcDate`). RS: `desk/rsrank.py` ranks the Russell 3000 daily (`rsRank`, 1-99). It fills `rs` only when `rs` is empty or `rsSrc == "computed"`; a value Juan typed (`rsSrc` "manual" or absent) is never replaced. You may set `rs` from an IBD RS Rating dated within 14 days only on stocks where `rs` is not computed. `rsPerf`/`rsLineHi` are never copied into `rs`.
- `static/index.html` is the single source of the UI and must keep working on claude.ai too: no build step, no external scripts, everything inline. Use `window.claude.local` to branch for local-only behaviour.

## Layout
- `static/index.html`: the whole desk (UI, verdict rules, Dalio panel, news, Charts tab with the pattern engine and scanner). Identical to the published artifact.
- `static/claude-shim.js`: local stand-in for the artifact runtime (`window.claude.use("db" | "mcp" | "user")`). Injected by the server only.
- `desk/server.py`: serves the page and the API the shim calls (`/api/doc`, `/api/col`, `/api/seq`, `/api/mcp`). Rewrites a few claude.ai-specific UI strings at serve time.
- `desk/db.py`: SQLite document store with the artifact's path model (`collection/doc`).
- `desk/market.py`: daily bars from Yahoo Finance (`yfinance`), Stooq fallback, cached 10 minutes. `SEPA_OFFLINE=1` uses `data/seed/hist` instead (tests).
- `desk/sheets.py`: emulates the Google Drive connector. The page "creates sheets" of tickers; reading them returns the same table the GOOGLEFINANCE sheet produced (feed) or compact bar history (charts, scanner).
- `desk/update.py`: mechanical daily update (prices into stocks/positions, market 50/200-day checks, computed distribution days/FTD, RS line metrics, Dalio momentum `cycleMkt`, chart history). Does not stamp `market.updated` unless index data arrived.
- `desk/regime.py`: O'Neil distribution-day / follow-through state machine on index bars (pure function, unit-tested).
- `desk/scheduler.py`: in-process scheduler started by `serve` (jobs daily, update, news, backup; config `app/settings.schedule`, status `app/jobs`).
- `desk/rsrank.py`: Russell 3000 RS ranking: universe from iShares IWV holdings (weekly; fallback `data/universe.csv`), batched Yahoo closes in SQLite table `px`, IBD weighting 2*C/C63 + C/C126 + C/C189 + C/C252, percentile 1-99, history in `rank_hist`, leaders to `app/leaders`. Symbol overrides in `data/ticker_map.json` ({"ISHARES": "YAHOO"}).
- `desk/backup.py`: SQLite snapshot + JSON backups with rotation; `bundle`/`restore` zip for moving machines.
- `static/local-panel.js`: local-only job status strip, injected by the server (not part of the artifact).
- Auth: `SEPA_TOKEN` (from `.env`, never committed) turns on cookie/Bearer auth and is required for non-loopback binds. Remote access is via `tailscale serve`, not port forwarding.
- `desk/claude_jobs.py`: runs `prompts/*.md` with the Claude Code CLI (`claude -p`), allowed tools limited to `python3 desk.py`, WebSearch, WebFetch, Read.
- `prompts/daily-research.md`, `prompts/news-scan.md`: the research jobs (the old claude.ai scheduled tasks, adapted).
- `data/seed/`: Juan's data exported from claude.ai on 2026-11-05; imported automatically into an empty database.
- `artifact/`: what's needed to republish on claude.ai (capabilities, scheduled-task prompts).
- `tools/test-patterns.js`: tests for the pattern engine (`node tools/test-patterns.js`).

## Commands
- Run the desk: `python3 desk.py serve` then open http://127.0.0.1:8765
- Mechanical update: `python3 desk.py update`
- Data: `python3 desk.py db get app/settings`, `db list stocks`, `db update stocks/GKOS '{"pivot":"242.40"}'`, `db set app/news - <<'JSON' ... JSON`
- Research with Claude Code: `python3 desk.py research` (daily), `python3 desk.py news`
- Full daily routine: `./scripts/daily.sh`
- RS ranking: `python3 desk.py rsrank` (runs inside the daily job), `rsrank --full` (re-download all history), `rsrank NVDA GKOS` (look up ranks)
- Run a scheduled job now: `python3 desk.py job daily|update|rsrank|news|backup`; move machines: `desk.py bundle` / `desk.py restore <zip>` (`restore --once` is the installers' idempotent variant; bundles never include dotfiles such as `.env`)
- Tests: `python3 -m unittest discover -s tests`, `node tools/test-patterns.js`; offline end-to-end: `SEPA_OFFLINE=1 SEPA_DB=/tmp/t.sqlite python3 desk.py serve`

## Data model (all stock/position values are strings)
- `app/settings`: account, riskPct, mode ("strict"|"standard"), market {spx50, spx200, ndx50, ndx200 (booleans), dspx, dndx (strings), ftd (boolean), updated}, feed {fileId, tickers[], built, lastRefresh, lastErrors[]}, histFeed {fileId, tickers[]}, scanFeed {fileIds[], tickers[]}, cycleMkt {asOf, assets {TICKER: {price, ma50, ma200, r3, r6, r12}}} (numbers; returns as fractions), daily/weekly checklists.
- `app/cycle`: Dalio debt-cycle panel {updated, shortCycle, longCycle, policy, readings [{name, value, trend, asOf}], take, sources [{title, url}]}.
- `app/news`: {status (requested|running|done|error), requestedAt, startedAt, updated, window, items [{kind, key, date, title, source, url, impact, take}], summary, quiet [], error}.
- `app/jobs`: {daily|update|news|backup: {status (running|ok|error), day, started, finished, output}}. `app/settings.schedule`: {daily "HH:MM", days [Mon=0..], research bool, news "HH:MM"|"" , backup "HH:MM"}. `app/settings.market` also has src ("computed"|"IBD"), srcDate, calc {spx|ndx: {dd, ddDates[], state, ftdDate, uptrend, src, asOf}}.
- `app/leaders`: {asOf, n (ranked), universe, universeAt, rule, items [{t, sym, name, sector, rank, rank4w, tt (0-7), c, offHi %, m50, m150, m200, m200p, hi, lo (close-based), av (K shares), vr (volume % of avg), mcap ($B), onDesk}]}.
- `app/scan`: last pattern scan {ran, tickers[], results [{t, c, score, rs, pass, name, key, status, pivot, stop, dist}], bad []}.
- `stocks/<TICKER>`: ticker, name, sector, price, ma50, ma150, ma200, ma200prev (200-day 22 sessions ago), hi52, lo52, avgvol (thousand shares), bovol (today's volume % of average), mcap ($B), rs, rsPerf (computed weighted 12-month %), rsLineHi ("1" = RS line at 52-week high), rsRank / rsRank4w / rsAsOf (computed Russell 3000 RS rank, 20 sessions ago, date), rsSrc ("computed" | "manual"), eps0-2, sal0-2, mar0-2 (latest quarter first, YoY %), epsA, roe, catalyst, grpRank, grpTotal, inst, contr, weeks, dry, pivot, stop, target, earn, float, notes, updated, fillReq (queued for Claude to fill fundamentals).
- `positions/<id>`: ticker, entry, shares, istop, stop, price, date, bo, hit20, v50, climax, notes.
- `hist/<TICKER>`: {bars "yyyymmdd,o,h,l,c,volK;...", asOf, n, scan (true when only from a pattern scan)}.

## Desk rules (strict mode = all must pass)
Trend template: price above the 150- and 200-day; 150 above 200; 200-day rising over a month; 50 above 150 and 200; price above the 50; at least 30% above the 52-week low; within 25% of the 52-week high; RS at least 80. CAN SLIM: quarterly EPS and sales growth 25%+ (stricter number wins), annual EPS growth, ROE 17%+, a catalyst, leadership, institutional sponsorship, market in a confirmed uptrend. Entry: within 0–5% above the pivot on volume at least 140% of average. Stops 7–8% max. Positions: sell at the stop, 8% loss rule, raise to breakeven at 2R, O'Neil 20% profit rule (8-week hold if 20% within 3 weeks), avoid holding through earnings within 14 days without a cushion.

## Pattern engine (Charts tab)
Rules from Fidelity/Kirkpatrick "Identifying Chart Patterns" plus O'Neil (cup with handle, flat base, high tight flag) and Minervini (VCP, Stage 1→2 MA squeeze coil). Breakouts count as confirmed only with a close 1%+ past the line on 140%+ volume; "Ready" = within 5% below the pivot; "Extended" = more than 5% past it. Detection is heuristic: change thresholds in `detectPatterns` and re-run `node tools/test-patterns.js`.
