# Pre-registration - momentum-v5-1signal (PAPER ONLY)

Fixed BEFORE any validation result exists. Code frozen at commit `445c884` (branch `work/e2e-audit`, local, not pushed), rules fingerprint `d9d4191e906de84d`
(checked at start of every run; any change aborts). v1 `e1d925460ea02be4`, v2 `25db7812f99c7f4d`, v3 `afaf71d4a6d592db` untouched.

## Why this variant (diagnostic data only - never reused for validation)
* v1/v3 runs 44303696774, 44312261755, 44323482756, 44357634172, A/B check (2026-11-18): 0 pairs passed every non-sell
  check under v1/v3 rules. In the v1 snapshots (1 h, 2,406 pair-snapshots) the only pair that passed liquidity, 5 m
  volume and holders was stopped **solely** by `signals >= 2` (27 snapshots).
* The liquidity floor is the dominant reject reason but it is a universe/safety-side filter: not relaxed.
* One change vs v1: `min_signals` 2 -> 1. Everything else = v1 universe and safety, v3 execution.

## Variant (exact)
* Universe: pair age <= 6 h; liquidity >= $40,000; 5 m volume >= $25,000; holders >= 300; mint + freeze authority closed;
  LP burned or locked >= 30 d; dev < 5 %; top 10 < 35 % (as computed today, AMM account included - see audit D4);
  no bundle; no-chase (+300 % in 1 h without >= 10 % pullback -> skip); SELL_SIMULATION of the exact token amount = PASS.
* Signals: VOL_SPIKE (5 m volume > 5 x 1 h average), BUY_RATIO (> 70 %, >= 20 txns), HOLDER_GROWTH (>= 10 % in 10 min);
  >= 1 required. Smart money / KOL unavailable.
* Discovery (never a signal): DexScreener profiles + boosts latest/top, GeckoTerminal new/trending pools.
* Size: $50 per position, 2 x $25 tranches, each with its own Jupiter quote; max 2 open; one entry per pair; $250 virtual.
* Exits: 40 % at 2x, 30 % at 4x, 25 % trailing on the rest, hard stop -12 %, time stop 25 min. Exit fills only via a
  passing sell simulation of the exact amount; FAIL -> position part written off at $0 (imputed, reported separately);
  UNKNOWN -> retried next cycle, no fill. Open positions are never sold at job end.
* Costs: the Jupiter quote already contains pool fees and price impact; + $0.02 per swap network/priority fee. Nothing else
  added (no double counting). Market data older than 120 s is not used for any decision.

## Baseline
Paired random draw: each Momentum entry triggers one draw (logged seed) among pairs that passed the same universe and
safety checks in the same cycle (signals ignored), same size, budget, costs and exits. The comparison is **NETESTABIL**
unless >= 20 draws had more than one eligible option and both sides have >= 30 closed positions.

## Sample and success criterion (test window)
* Minimum: >= 30 closed Momentum positions AND >= 7 distinct UTC days of collection with >= 12 h coverage each.
* Success = all of: (1) net PnL after costs incl. imputed losses > 0; (2) net PnL without the best trade > 0;
  (3) Momentum net > baseline net (baseline testable, see above); (4) max drawdown < 30 % of the $250 start;
  (5) no single trade > 30 % of total net profit. Reported per day: net PnL, closed positions, days without trades,
  worst day; open positions, failed exits and imputed losses separately. A quote is not a proceeds figure.
* Replication: the next >= 7 distinct days, same commit and fingerprint, same criterion -> `AVANTAJ PAPER REPLICAT`
  only if test and replication both pass.
* Stop: < 30 closed positions after 14 calendar days -> `INSUFICIENT` (signal too rare to evaluate); criterion not met
  -> `FARA AVANTAJ IN TEST`. No rule change between test and replication.

## Collection mechanism (needs approval - nothing is running)
`.github/workflows/collect-v5.yml` (in this branch, not pushed): every 6 h, one job of 350 min running
`python run.py --v5 --resume --minutes 345`, state and logs committed to a separate branch `paper-data-v5` with the
built-in token, report in job log + Summary. Known gaps: minutes between jobs, delayed/skipped scheduled runs, shared-IP
rate limits (recorded, never filled in). Cost: free on a public repository (earlier runs billed 0 ms); a private repo's
2,000 free minutes/month would cover ~20 % of 7 days.
