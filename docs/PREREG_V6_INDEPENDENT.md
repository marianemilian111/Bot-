# Pre-registration - capture-v6, INDEPENDENT paper test (not started: needs its own approval)

Code: branch `work/v6-capture` (`dexbot/v6.py`, `dexbot/v6_runner.py`, `scripts/v6_gate.py`), rules fingerprint
`f9f64f071c181687` (`V6_RULES` in `dexbot/variants.py`; checked at start, any change aborts). v5 is not touched.

## What makes it independent of v5
Own discovery and feed, own balance ($70 paper), own positions, own entries and exits, own Jupiter quotes per tranche
at v6 sizes and own sell simulations of the exact v6 amounts. Per cycle it records the full decision input (raw signal
inputs, safety values, traded-pool LP, verdict) in `logs/v6_collect_*.jsonl`. No v5 fill is reused.

## Rules (fixed now; interpretations included)
See `V6_RULES` and `docs/AUDIT_CAPTURE.md`. Summary: age <= 6 h; DEX pumpfun/pumpswap/raydium*/meteora*; liquidity >= $40k;
5 m volume >= $25k; >= 300 holders; mint/freeze closed; LP of the traded pool >= 95 % (duration not verifiable);
dev < 5 % (creator address); top 10 < 35 % raw (AMM included); bundle proxy; sell simulation PASS; >= 2 of
VOL_SPIKE / BUY_RATIO / HOLDER_GROWTH / SMART_MONEY / KOL (wallet signals UNKNOWN without a verified list);
no chase with a 10 % pullback from the 1 h high; entries 23:00-07:00 Europe/Bucharest; equity sizer 20/30/40 %, x0.5 after
2 losses, compounding, max 2 open; exits 2x/4x/trailing 25 %, -12 %, 25 min, DEV_SELL, LP_PULLED (proxy); 30 % session
drawdown pause; 07:00 recap.

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
