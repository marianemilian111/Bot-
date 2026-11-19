# Breakout probe (immediate vs retest) - prepared, NOT launched. PAPER ONLY.

Rules: `docs/BREAKOUT_RULES.md` (commit 375b78f), unchanged. v6b, v5 and all workflows untouched.
Code: `scripts/breakout_probe.py` (live loop), `dexbot/breakout.py` (Detector + Engine shared with the offline replay,
LiveQuotes provider), `scripts/breakout_compare.py` (replay + verdict). Tests: `tests/test_breakout.py`,
`tests/test_breakout_probe.py` (fake feed and quotes, no network).

| Item | Value |
|---|---|
| Where it runs | this session's container, one background process (no GitHub workflow, no push) |
| Command | `python scripts/breakout_probe.py --out <scratchpad>/probe --minutes 110 --max-buy 60 --max-sell 120 --max-tracked 60` |
| Max duration | 110 min collection + <= 2 min to resolve the last due quotes; stops itself |
| Entries | none in the last 27 min (25 min time stop + 60 s latency + margin); those signals are counted apart |
| Selection | pools with liquidity >= $40k and 5 m volume >= $25k when first seen (max 60 tracked); thresholds re-checked on the signal observation |
| BUY quote | Jupiter quote for $14 only after an eligible signal, at decision + 0 / 30 / 60 s (<= 3 per signal, reused across scenarios when one already fits) |
| SELL | Jupiter quote + unsigned `simulateTransaction` (public holder, sigVerify false) for the exact held amount, only at an exit trigger + latency |
| Request caps | <= 60 buy quote requests, <= 120 sell requests (each = 1 quote + 1 swap build + 1 RPC simulation) -> <= 300 Jupiter calls, paced 2.1 s |
| Other calls | DexScreener ~10 / 30 s, GeckoTerminal 1 / 2 min, RugCheck <= 1 per tracked pool per 5 min, Solana public RPC; all keyless, cost $0 |
| Never | wallet, keys, signing, sending, real money; a fill from a candle or an observed price; an exit without a quote |
| Result | `result.json` = replay of the recorded files, verdict exactly one of: `ZERO SEMNALE ELIGIBILE` / `SEMNALE, DAR FARA COTATII SUFICIENTE` / `POZITII INCHISE` + net PnL after costs per variant and latency; open positions, failed exits and imputed losses apart |
| Not claimed | a 110-min sample is not evidence of an edge; any PnL here is one paper sample, not a validation |
