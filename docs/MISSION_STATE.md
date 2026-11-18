# MISSION STATE - read this first in every session; resume the first unfinished task

Goal line: `PREGATIT PENTRU DECIZIA DE PILOT REAL` only with net paper advantage in validation AND replication (pre-registered),
plus execution and risk tests passed, and no unresolved critical defect. Never from a shadow, offline tests or one green session.

| # | Stage | State | Evidence | SHA / run | Next exact step |
|---|---|---|---|---|---|
| 1 | v5 collection (approved) | RUNNING as DIAGNOSTIC only - not a pilot candidate while D8 is open (rules unchanged) | data branch `paper-data-v5`; gate in Summary | run 44393958999; code pinned `b999e47` | read `state/v5_phase.json` on `paper-data-v5`; act on its verdict |
| 1a | v5 known defect D8 (LP check) | OPEN in v5 (unchanged by instruction); FIXED in v6 | LP status = max over ALL markets; ANT: main pool LP supply 31,379.58 held by the creator (RPC), masked by a $0.03 locked pool. v6 requires traded-pool LP burned on chain (supply 0; G5fg verified) | found on `work/v6-capture` | any v5 result must be read with this caveat; not a pilot basis until resolved |
| 2 | Screenshot fidelity (v6) | DONE offline; 3 rules NEVERIFICAT LIVE (wallet list missing, dev linked wallets, bundle) | `docs/AUDIT_CAPTURE.md` v6 table; `tests/test_v6_rules.py` (9), `tests/test_v6_runner.py` (e2e) | branch `work/v6-capture` | none until a verified wallet list exists |
| 2a | v6 shadow (v5 trades filtered) | SHADOW PARTIAL - indicator only | `scripts/v6_shadow.py`, `docs/PREREG_V6.md` | `a0d2350` (+ relabel on this branch) | never used as validation or pilot condition |
| 3 | Data sufficiency for an independent v6 | INSUFFICIENT in v5 data -> own runner built (`dexbot/v6_runner.py`, full decision record per cycle) | v5 JSONL rows hold: ts, mint, pair, dex, age_h, price, liquidity, vol5m, holders, signals (names), verdict, reason. MISSING: buys/sells 5 m, vol_avg5m_1h, holders_10m, dev %, top10 raw, LP per pool, bundle flag, mint/freeze flags, creator, wallet events, v6-size buy quotes per tranche, v6-size sell simulations, v6 exit quotes | - | independent v6 needs its own collection -> prepare runner + workflow (not started: needs new approval) |
| 4 | v6 validation (independent) | PREFLIGHT PASSED (resume, no duplicates, separate from v5; 7qp7... = LP_UNKNOWN: Meteora DAMM v2 has no LP mint) -> VALIDATION STARTING on paper-data-v6 | `docs/PREREG_V6_INDEPENDENT.md`, `scripts/v6_gate.py`, `.github/workflows/collect-v6.yml` | code `7132899`, fingerprint `c717981470d4b5b7`, workflow `5ab3086` | 2 test runs -> verify resume/no duplicates/separation -> add schedule -> start on paper-data-v6 |
| 5 | v6 replication | NOT STARTED | - | - | automatic after a passing validation, same rules |
| 6 | Execution without money | DONE (offline) | `tests/test_execution.py` (9) | `be76e01` | wire into the strategy runner only for a pilot |
| 7 | Risk limits | DONE (offline) | `tests/test_execution.py` Risk (3) | `be76e01` | pilot values proposed, not approved |
| 8 | Pilot decision | NOT READY | needs 1/4 + 5 + 6 + 7 and D8 resolved | - | - |


## v6 fingerprint audit (2026-11-18, read-only check against git history)
| Item | Value |
|---|---|
| f9f64f071c181687 | `V6_RULES` at 215dd5e (21:24 UTC) |
| c717981470d4b5b7 | `V6_RULES` at d97073d (21:42:14 UTC); computed == frozen at 7132899 |
| Fields changed | `universe`: LP ">= 95 % locked/burned" -> "BURNED verified on chain (LP mint supply 0); lock without verifiable 30-day date fails"; `exits`: "DEV_SELL (creator balance fell), LP_PULLED (liquidity < 50 %: proxy)" -> "DEV_SELL only on a confirmed sell swap; balance drop / liquidity drop = log only" |
| Why | D8 (ANT: unlocked main-pool LP held by the creator, masked by a locked dust pool) and the instruction that a % locked is not a 30-day proof and that balance/liquidity drops are not proof of sell/pull |
| When vs data | before ANY v6 data: first test run 21:42:57, validation start 22:01:16 |
| Prereg | `docs/PREREG_V6_INDEPENDENT.md` at 7132899 states c717981470d4b5b7 and the same rules; v6_gate.py unchanged since 215dd5e (criteria identical) |
| After the pin | 70cb18d: documentation only (Meteora DAMM v2 LP_UNKNOWN limitation) |
| Verdict | NO deviation: validation runs the configuration fixed before it started |

## v6 validation data eligibility
| Run | Status | Eligible |
|---|---|---|
| 44407410908 (22:01-22:16 UTC) | crashed after 15 min: ZeroDivisionError on priceChange.h1 = -100 (infra, not a rule); no meta / run-history file written, 0 trades, 0 positions | NO - excluded (the gate counts only runs with a meta file, so it is already outside every window) |
| next runs | code pinned at ef5a17e = 7132899 + infra fix only, same fingerprint | yes |

v5 note: the same -100 % case costs v5 that cycle (caught, logged ERROR) - a data gap to read in the v5 diagnostic; v5 code unchanged.

## Check 7132899 -> ef5a17e (read-only, 2026-11-18 ~22:30 UTC)
| Item | Result |
|---|---|
| Code changed | `dexbot/live.py`: `price_1h_ago` is None when priceChange.h1 <= -100 (was a division by zero); `dexbot/v6_runner.py`: a failed feed step is logged (ERROR) and the cycle skipped instead of ending the run. Rest: workflow file, docs, tests |
| Unchanged (git diff empty) | `dexbot/variants.py` (V6_RULES), `scripts/v6_gate.py` (criteria), `dexbot/v6.py` (screenshot rules), `dexbot/execsim.py` (quotes / sell simulation), `dexbot/agents.py` (sizer, exits, equity), `dexbot/bot.py` |
| Effective fingerprint | ef5a17e: frozen == computed == `c717981470d4b5b7` (same as 7132899) |
| Screenshot rules / execution / pre-registered criteria | not changed. Only effect: a pair with a -100 % 1 h change now gets "1 h price unknown" (no-chase rule not applicable - such a pair cannot be +300 %) instead of crashing the run |
| Run 44407410908 in the sample of run 44410378519? | NO. Data branch `paper-data-v6` (commit d190224) holds no `logs/runs/*.json` for it; the gate run on that branch returns `VALIDATION / COLLECT / "no run yet"`, so the window starts at the first run with a meta file and its coverage counts 0 |
| Carried state | its saved state equals a fresh v6 state: cash 70.0, 0 trades, 0 positions, 0 pending, traded [] (Momentum and baseline). Resuming from it is identical to a clean start; no trade, balance or coverage from it enters any window |
| Its files | `logs/night_*_20261118_220154.log`, `logs/v6_collect_20261118_220154.jsonl` stay on the branch as diagnostics only; the gate reads state trades + `logs/runs/`, the report reads the latest run's own meta / jsonl |
| Verdict | PASS - collection and conditional replication continue unchanged |

## BLOCAT - ACOPERIRE NEVERIFICATA (v6 validation, recorded 2026-11-18 ~22:50 UTC, before any v6 result)
* Pre-registered coverage = ">= 6 h of collection inside 23:00-07:00" per session; the gate counts run start -> end.
* Why eligible coverage cannot be reconstructed from the collected files (code at ef5a17e):
  1. `LiveFeed.step` skips a failed DexScreener chunk (`except Exception: continue`) with no log; if every chunk fails,
     no error is raised at all.
  2. v6 decision rows (`v6_collect_*.jsonl`) carry the cycle time, not the data fetch time; cached pairs (<= 120 s)
     are written again under the new cycle time. A new timestamp does not prove new data.
  3. Only whole-step failures are identifiable (`ERROR feed step failed` in the Momentum log).
* Consequence: any gate PASS for v6 is NOT accepted as evidence until coverage is verified; the gate can still advance
  VALIDATION -> REPLICATION automatically (workflow not modified, as instructed) - flagged as a blocker; a replication
  started that way is not evidence either.
* Measurable part: `scripts/v6_coverage_audit.py <data dir>` -> per session gate minutes, cycles with rows, failed cycles,
  max supported minutes (upper bound), and whether the difference flips the 6 h threshold.
* Resolution path (prepared and tested, NOT deployed - the running test must not change): per-pair `fetched_at`,
  `fresh_this_cycle`, per-cycle `cycle_feed` (ok / partial / failed) in the rows and `cycle_status` in the run meta;
  `scripts/v6_coverage_audit.py` then counts only fully successful cycles (tests: `tests/test_coverage_recording.py`).
  Using it needs a NEW validation window (the current window's data stays diagnostic) = a decision for the owner.

## FIRST TASK ON RESUME
After run 44410378519 writes `logs/runs/capture-v6_*.json` on `paper-data-v6` (expected ~04:05 UTC 2026-11-19):
1. `git fetch origin paper-data-v6`; extract it; run `python scripts/v6_gate.py` there -> confirm the window opened with
   this run (`since` = its `started_utc`, `phase` VALIDATION).
2. Run `python scripts/v6_coverage_audit.py <dir>`; list failed cycles and the gate-vs-supported difference per session.
3. Record both here. Status stays BLOCAT - ACOPERIRE NEVERIFICATA (reason above) unless the recording fix is approved.
