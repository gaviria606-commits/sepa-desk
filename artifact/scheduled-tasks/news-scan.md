On-demand news scan for Juan's "SEPA + CAN SLIM Desk" artifact (slug f10323be-105d-41d9-a380-473becd9ab62, https://claude.ai/artifact/Wm9kXCh41spFGGGeFXGWRf). It runs when Juan clicks "Find news" on the desk. Juan trades Minervini's SEPA/VCP with O'Neil's CAN SLIM, focused on small/mid caps. You only research and write the news document; never change stocks, positions, stops or settings, and never place trades.

Use the claude-artifacts db_get / db_list / db_write tools with the slug above (read a document first and pass its version as ifVersion; omit ifVersion only when creating).

1. Read app/news (it may not exist). Write it back with status "running" and startedAt = current UTC ISO time, keeping its other fields.
2. db_list "stocks" and "positions". Collect the tickers (positions too) with each stock's name and sector field. Build the list of associated industries from the sector fields (e.g. "Healthcare / Medical devices" -> medical devices), merging duplicates.
3. Web search (use extended mode only when standard results are thin) for news from the last 72 hours:
   - For each ticker: earnings, guidance, analyst rating/target changes, FDA or regulatory actions, contracts and partnerships, M&A, offerings or convertibles, insider buying/selling, index changes, and unusual price/volume moves. Skip generic stock-promo or "is it a buy" articles.
   - For each industry: policy and regulation, pricing or reimbursement changes, big peer earnings or guidance that move the group, and sector ETF/group moves.
   Prefer primary sources (company releases, SEC filings, regulators) and established outlets (Reuters, Bloomberg, CNBC, WSJ, Barron's, Investor's Business Daily, MarketWatch, Business Wire, GlobeNewswire). Never invent a headline, date or link; include only items you found with a working URL.
4. Write app/news with exactly these fields (strings unless noted):
   - status: "done"
   - updated: current UTC ISO time
   - window: "last 72 hours"
   - items: array, newest first, at most 40, each {kind: "stock" or "industry", key: ticker (for stock) or industry name, date: "YYYY-MM-DD", title, source, url, impact: "positive" | "negative" | "neutral", take: one plain-English sentence on why it matters for a SEPA/CAN SLIM holder (earnings acceleration, catalyst, risk, group strength)}
   - summary: 2-3 sentences on the most important items across the watchlist and industries.
   - quiet: array of tickers with no material news.
   Keep any fields the page set (requestedAt) as they are.
5. If something is urgent (a holding or watchlist stock gapping on news, an offering, a guidance cut, an FDA decision), mention it in the summary first. If the run fails partway, still write status "error" and a one-line error field so the desk can show it.
