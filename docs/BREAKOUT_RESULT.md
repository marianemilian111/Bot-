# Breakout immediate vs retest - first offline comparison (PAPER ONLY; rules in BREAKOUT_RULES.md, unchanged)

Data used (existing only): v1/v2 runs 44303696774 + 44312261755, paper-data-v5-test, paper-data-v6-test, paper-data-v6
(crashed run, diagnostic). 4,454 observations after de-duplicating (mint, ts), 93 mints, 2026-11-18 12:25-22:16 UTC.
`paper-data-v5` and `paper-data-v6b` are not on the remote yet (no data to read).
Command: `python scripts/breakout_compare.py <all jsonl>`.

| Variant | Latency | Signals | Entries | Closed | Net PnL closed |
|---|---|---|---|---|---|
| Immediate | 0 / 30 / 60 s | 0 | 0 | 0 | N/A |
| Retest | 0 / 30 / 60 s | 0 | 0 | 0 | N/A |

Open positions 0, failed exits 0, imputed losses $0 (all variants, all latencies).

Why 0 signals (diagnostic, not a rule change): 2,801 observations had >= 20 prior observations; 119 broke the level by
> 0.5 %, 42 of them also with volume > 2x median, but none was inside the universe at that moment (only 59 of 4,454
observations had liquidity >= $40k and 5 m volume >= $25k; 847 had liquidity null = unknown -> outside).

Missing even if signals existed: no file holds a recorded quote at a simulated time for these sizes:
* BUY: `tokens_out` for `usd_in` = 14.0 per (mint, time >= decision + latency, <= +60 s);
* SELL: `usd_out` + `status` for `amount_in` = exact held tokens per (mint, time >= trigger + latency, <= +60 s).
Without them the engine makes no entry and no exit (never a fill from a candle or an observed price).
