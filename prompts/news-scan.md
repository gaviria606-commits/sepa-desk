News scan for Juan's SEPA + CAN SLIM Desk (this project folder). It runs when Juan clicks "Find news (Claude)" or runs `python3 desk.py news`. Juan trades Minervini's SEPA/VCP with O'Neil's CAN SLIM, focused on small/mid caps. You only research and write the news document: never change stocks, positions, stops or settings, and never place trades.

Use only `python3 desk.py db get|list|update|set ...` for data (see CLAUDE.md).

1. `python3 desk.py db update app/news '{"status":"running","startedAt":"<current UTC ISO time>","error":""}'`
2. `python3 desk.py db list stocks` and `python3 desk.py db list positions`. Collect the tickers with each stock's name and sector field, and build the list of associated industries from the sector fields (e.g. "Healthcare / Medical devices" -> medical devices), merging duplicates.
3. Web search (standard first, more thorough only when results are thin) for news from the last 72 hours:
   - For each ticker: earnings, guidance, analyst rating/target changes, FDA or regulatory actions, contracts and partnerships, M&A, offerings or convertibles, insider buying/selling, index changes, unusual price/volume moves. Skip generic stock-promo or "is it a buy" articles.
   - For each industry: policy and regulation, pricing or reimbursement changes, big peer earnings or guidance that move the group, sector ETF/group moves.
   Prefer primary sources (company releases, SEC filings, regulators) and established outlets (Reuters, Bloomberg, CNBC, WSJ, Barron's, Investor's Business Daily, MarketWatch, Business Wire, GlobeNewswire). Never invent a headline, date or link; include only items you found with a working URL.
4. Write the result by piping JSON on stdin: `python3 desk.py db set app/news - <<'JSON'` … `JSON` (a heredoc), with exactly these fields:
   - status: "done"; updated: current UTC ISO time; window: "last 72 hours"; requestedAt: keep the existing value
   - items: array, newest first, at most 40, each {kind: "stock" or "industry", key: ticker or industry name, date: "YYYY-MM-DD", title, source, url, impact: "positive" | "negative" | "neutral", take: one plain-English sentence on why it matters for a SEPA/CAN SLIM holder}
   - summary: 2-3 sentences on the most important items. Put anything urgent first (a stock gapping on news, an offering, a guidance cut, an FDA decision).
   - quiet: array of tickers with no material news.
5. If the run fails partway, still write status "error" and a one-line "error" field.
