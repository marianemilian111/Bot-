# Pre-registration - capture-v6, INDEPENDENT paper test (conditionally approved 2026-11-18: paper only, paper-data-v6, cost 0, <= 14 + 14 days)

Code: branch `work/v6-capture` (`dexbot/v6.py`, `dexbot/v6_runner.py`, `scripts/v6_gate.py`), rules fingerprint
`c717981470d4b5b7` (`V6_RULES` in `dexbot/variants.py`; checked at start, any change aborts). v5 is not touched.

## What makes it independent of v5
Own discovery and feed, own balance ($70 paper), own positions, own entries and exits, own Jupiter quotes per tranche
at v6 sizes and own sell simulations of the exact v6 amounts. Per cycle it records the full decision input (raw signal
inputs, safety values, traded-pool LP, verdict) in `logs/v6_collect_*.jsonl`. No v5 fill is reused.

## Rules (fixed now; interpretations included)
See `V6_RULES` and `docs/AUDIT_CAPTURE.md`. Summary: age <= 6 h; DEX pumpfun/pumpswap/raydium*/meteora*; liquidity >= $40k;
5 m volume >= $25k; >= 300 holders; mint/freeze closed; LP of the TRADED pool BURNED, verified on chain (LP mint supply
== 0 via RPC; a "locked" percentage without a verifiable 30-day date fails - evidence: G5fg supply 0 = burned, ANT
supply 31,379.58 held by the creator = unlocked, masked in v5 by a $0.03 locked pool);
dev < 5 % (creator address); top 10 < 35 % raw (AMM included); bundle proxy; sell simulation PASS; >= 2 of
VOL_SPIKE / BUY_RATIO / HOLDER_GROWTH / SMART_MONEY / KOL (wallet signals UNKNOWN without a verified list);
no chase with a 10 % pullback from the 1 h high; entries 23:00-07:00 Europe/Bucharest; equity sizer 20/30/40 %, x0.5 after
2 losses, compounding, max 2 open; exits 2x/4x/trailing 25 %, -12 %, 25 min, DEV_SELL (confirmed swap); 30 % session
drawdown pause; 07:00 recap. Exit triggers: DEV_SELL only when a creator transaction is a confirmed SELL swap of the
mint (a balance drop alone is logged DEV_BALANCE_DROP, no exit); LP pull cannot happen with burned LP (a >= 50 %
liquidity drop is logged LIQ_DROP_50, no exit). NEVERIFICAT LIVE: smart money / KOL (no verified wallet list ->
UNKNOWN, never counted), dev wallets beyond the creator address, bundle (RugCheck risk-name proxy).
This is the screenshot strategy with these stated limits - not a "faithful copy".

## Baseline, sample, success, stop
* Baseline: paired random draw among same-cycle pairs that pass universe + safety, same sizing and exits. NETESTABIL
  unless >= 30 closed baseline trades and >= 20 draws with more than one option.
* Window complete: >= 30 closed v6 trades AND >= 7 sessions with >= 6 h of collection inside 23:00-07:00 Bucharest.
* Success per window: net PnL after costs incl. imputed > 0; net without the best trade > 0; above a testable baseline;
  drawdown < $21 (30 % of $70); no trade > 30 % of net profit.
* VALIDATION -> REPLICATION (new sessions, same commit and fingerprint) -> `AVANTAJ PAPER REPLICAT` only if both pass.
  Fail -> `FARA AVANTAJ`; NETESTABIL baseline -> `NU TRECE: reper NETESTABIL`; 14 days without a complete window ->
  `INSUFICIENT`. Implemented and tested in `scripts/v6_gate.py`.
* Expectation stated before data: v6 is stricter than v5 (>= 2 signals, DEX list, 8 h session), so INSUFICIENT is likely.

## The single request this needs (not sent to GitHub, not running)
A second collection workflow, identical in mechanics to `collect-v5.yml` but running `python run.py --v6 --resume`,
state on its own data branch `paper-data-v6`, its own gate (`scripts/v6_gate.py`), code pinned to the commit carrying
this document, every 6 h, 345 min per job. Duration: up to 14 days for validation + up to 14 days for replication,
stopping itself at the gate verdict. Cost: $0 (public repository, GitHub-hosted runners; earlier runs billed 0 ms).
Risk: two collectors share the public APIs (GeckoTerminal ~30 calls/min per IP, Jupiter keyless 0.5 RPS); throttling is
logged, never filled in.

## Known limitation found in the preflight (before any validation data; rules unchanged)
Meteora DAMM v2 pools have no LP mint (RugCheck `mintLP` = system address; example pool 9v8KHSTe..., run 44404310078),
so an on-chain burn cannot be verified -> LP UNKNOWN -> not eligible. In practice v6 can only enter pump.fun / PumpSwap
and Raydium pools whose LP mint supply is 0. Reported as a coverage limit, not relaxed.

## Amendment log (infrastructure only; rules and criteria unchanged)
* 2026-11-18 ~22:20 UTC: validation run 44407410908 crashed (division by zero on a -100 % 1 h price change). Fixed in
  ef5a17e (no rule change, fingerprint c717981470d4b5b7 unchanged); collection code pinned to it. The crashed run has no
  meta file and no trades; it is excluded from every window.

## Amendment B - clean validation window (prepared 2026-11-18 ~23:30 UTC; needs the owner's approval to publish)
* Reason: the collection code in use (ef5a17e) records neither the fetch time per row nor the feed status per cycle, so
  the coverage the gate counts (run start -> end) cannot be verified (MISSION_STATE: ACOPERIRE NERECONSTRUCTIBILA).
* Change (measurement only; strategy rules and criteria unchanged, fingerprint c717981470d4b5b7): coverage per session =
  30-s cycles whose whole feed step succeeded (`cycle_status` == ok, from 3ea5dc4); a run without cycle_status counts 0.
  Everything else (>= 7 sessions with >= 6 h, >= 30 closed, success criteria, 14 + 14 days, NETESTABIL rule) unchanged.
* New window on a NEW data branch `paper-data-v6b`, clean state. Data on `paper-data-v6` (collected with ef5a17e) is
  diagnostic only and never enters a v6b window.
