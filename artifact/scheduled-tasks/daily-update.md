# "SEPA Desk daily update" (claude.ai scheduled task trig_018CLJZHef975KciQuwHQ6Ac)

Schedule: `CRON_TZ=Pacific/Auckland 15 10 * * 2-6`, automatic approval, push notification when something needs action.

The claude.ai prompt does the same work as the local routine, with claude.ai tools:

1. Price feed: reads app/settings.feed.fileId (a Google Sheet of GOOGLEFINANCE formulas: Ticker, Price, High52, Low52, AvgVolume, Volume, MarketCap, EPS, MA50, MA150, MA200, MA200_1mAgo, AsOf, R3M, R6M, R12M) with the Google Drive connector, rebuilding it when watchlist/position tickers or the macro ETFs (SPY, QQQ, IWM, TLT, IEF, TIP, GLD, DBC, UUP) are missing. Locally this is `python3 desk.py update`.
2. Updates each stock and position from the feed.
3. Market check (SPY/QQQ vs 50/200-day; IBD distribution days and follow-through day from a source dated within 3 days).
4. Dalio cycle: writes settings.cycleMkt from the ETF rows and refreshes app/cycle after major releases or weekly.
5. Earnings and news per watchlist stock.
6. Fundamentals queue (fillReq).
7. Rule evaluation (READY / BUY / trend-template failures; position flags).
8. Report under 200 words, sent as a push notification.

Steps 3–8 are written out in full, adapted for the local tools, in `../../prompts/daily-research.md`. To change the claude.ai task, ask Claude in a claude.ai/Cowork conversation to update the "SEPA Desk daily update" scheduled task.
