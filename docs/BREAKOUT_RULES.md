# Breakout signal - two variants (PAPER ONLY, offline). Rules fixed BEFORE any comparison run.

Sources: definitions after komilovmDev/Breakout-and-Retest-Strategy @ dff73b0 (MIT): level = highest price of the
previous N observations excluding the current one; retest = price back inside a tolerance band around the level.
Three-step idea (breakout -> retest within a bar limit -> entry on re-break) after abTuring13/breakout-tester @ 20e98f7
(no licence: idea only, no code). Volume confirmation and execution are ours.

Data unit: one observation = one 30-s snapshot row of a pair (price, vol5m, liquidity) as logged by our runners.
Decisions use only observations at or before the decision time (no look-ahead).

| Rule | Value |
|---|---|
| Universe at decision | liquidity >= $40,000 and 5 m volume >= $25,000 on the decision observation |
| Level | max price of the previous 20 observations of the pair (current excluded); needs >= 20 prior observations |
| Breakout | price > level x 1.005 AND vol5m > 2 x median vol5m of the same previous 20 observations |
| Variant A - immediate | decision at the breakout observation |
| Variant B - retest | after a breakout at level L, within the next 10 observations: a touch (L x 0.98 <= price <= L x 1.005), then the first later observation with price > L x 1.005 = decision; price < L x 0.98 before that = invalid; window over = no trade |
| Size | $14 per entry (20 % of a $70 paper balance), no compounding (variants comparable) |
| Entry fill | the recorded BUY quote for $14 of that mint at the first time >= decision + latency, within 60 s; none -> no entry |
| Exits (trigger from observations) | 40 % at 2x, 30 % at 4x, 25 % trailing on the rest after 4x, hard stop -12 %, time stop 25 min |
| Exit fill | the recorded SELL quote / simulation for the exact token amount at the first time >= trigger + latency, within 60 s; FAIL -> imputed loss ($0 proceeds, reported apart); no quote -> no fill, position stays open (retried at the next observation) |
| Costs | the quote already includes pool fees and price impact; + $0.02 network fee per swap; nothing added twice |
| Latency scenarios | 0 s, 30 s, 60 s |
| One position per pair at a time; no re-entry while open | |

Never: a price from candles or observations used as a fill; an exit invented when no quote exists.
