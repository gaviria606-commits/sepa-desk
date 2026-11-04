"""Mechanical market regime: O'Neil distribution days and follow-through days from index bars.

Replaces "web-search IBD and hope the article is from this week" with a deterministic
computation on the same data IBD uses (index close + exchange volume). It is a model of
IBD's rules, not IBD itself, so results can differ by a day here and there; the research
job may still override with a dated IBD figure (see update.py).

Rules implemented
- Distribution day: index closes down >= 0.2% on volume above the prior session.
  It expires after 25 sessions, or once the index closes 5% above that day's close.
- Correction: entered when the active distribution count reaches 6, or the index closes
  10%+ below its highest close since the last follow-through day.
- Rally attempt: first up close after the correction low. It resets if the index closes
  below the rally-attempt low.
- Follow-through day: day 4 or later of the rally attempt, up >= 1.2% on volume above the
  prior session. Confirms a new uptrend and clears older distribution days.
"""

DD_DROP = -0.002
DD_WINDOW = 25
DD_EXPIRE_GAIN = 0.05
FTD_GAIN = 0.012
FTD_MIN_DAY = 4
CORR_DD = 6
CORR_DRAWDOWN = 0.10


def analyse(bars):
    """bars: [{t, c, v, l}] oldest first. Returns a dict with dd (active count), ddDates, uptrend, ftdDate, state."""
    n = len(bars)
    if n < 30:
        return None
    dds = []                 # indexes of distribution days
    uptrend, peak = True, bars[0]["c"]
    low_i = rally_start = None
    ftd_date = None
    for i in range(1, n):
        c, pc = bars[i]["c"], bars[i - 1]["c"]
        v, pv = bars[i].get("v") or 0, bars[i - 1].get("v") or 0
        chg = c / pc - 1 if pc else 0
        # expire old distribution days
        dds = [j for j in dds if i - j < DD_WINDOW and c < bars[j]["c"] * (1 + DD_EXPIRE_GAIN)]
        if chg <= DD_DROP and v > pv > 0:
            dds.append(i)
        if uptrend:
            peak = max(peak, c)
            if len(dds) >= CORR_DD or c <= peak * (1 - CORR_DRAWDOWN):
                uptrend, low_i, rally_start = False, i, None
            continue
        # in correction: track the low and the rally attempt
        if c < bars[low_i]["c"]:
            low_i, rally_start = i, None
            continue
        if rally_start is None:
            if chg > 0:
                rally_start = i
            continue
        day = i - rally_start + 1
        if day >= FTD_MIN_DAY and chg >= FTD_GAIN and v > pv > 0:
            uptrend, peak, ftd_date = True, c, bars[i]["t"]
            dds = []
    state = "uptrend" if uptrend else ("rally attempt" if rally_start is not None else "correction")
    return {"dd": len(dds), "ddDates": [bars[j]["t"] for j in dds], "uptrend": uptrend,
            "ftdDate": ftd_date, "state": state, "asOf": bars[-1]["t"]}
