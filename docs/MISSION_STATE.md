# MISSION STATE - read this first in every session; resume the first unfinished task

Goal line: `PREGATIT PENTRU DECIZIA DE PILOT REAL` only with net paper advantage in validation AND replication (pre-registered),
plus execution and risk tests passed, and no unresolved critical defect. Never from a shadow, offline tests or one green session.

| # | Stage | State | Evidence | SHA / run | Next exact step |
|---|---|---|---|---|---|
| 1 | v5 validation (approved collection) | RUNNING (workflow, not the agent) | data branch `paper-data-v5`; gate in Summary | run 44393958999; code pinned `b999e47` | read `state/v5_phase.json` on `paper-data-v5`; act on its verdict |
| 1a | v5 known defect D8 (LP check) | OPEN - v5 left unchanged by instruction | LP status = max over ALL markets: a $0.03 locked pool masks an unlocked main pool (ANT 8J2V..., live RugCheck) | found on `work/v6-capture` | any v5 result must be read with this caveat; not a pilot basis until resolved |
| 2 | Screenshot fidelity (v6) | DONE offline; 3 rules NEVERIFICAT LIVE (wallet list missing, dev linked wallets, bundle) | `docs/AUDIT_CAPTURE.md` v6 table; `tests/test_v6_rules.py` (9), `tests/test_v6_runner.py` (e2e) | branch `work/v6-capture` | none until a verified wallet list exists |
| 2a | v6 shadow (v5 trades filtered) | SHADOW PARTIAL - indicator only | `scripts/v6_shadow.py`, `docs/PREREG_V6.md` | `a0d2350` (+ relabel on this branch) | never used as validation or pilot condition |
| 3 | Data sufficiency for an independent v6 | INSUFFICIENT in v5 data -> own runner built (`dexbot/v6_runner.py`, full decision record per cycle) | v5 JSONL rows hold: ts, mint, pair, dex, age_h, price, liquidity, vol5m, holders, signals (names), verdict, reason. MISSING: buys/sells 5 m, vol_avg5m_1h, holders_10m, dev %, top10 raw, LP per pool, bundle flag, mint/freeze flags, creator, wallet events, v6-size buy quotes per tranche, v6-size sell simulations, v6 exit quotes | - | independent v6 needs its own collection -> prepare runner + workflow (not started: needs new approval) |
| 4 | v6 validation (independent) | READY, NOT STARTED - needs approval | `docs/PREREG_V6_INDEPENDENT.md`, gate `scripts/v6_gate.py` (tests) | fingerprint `f9f64f071c181687` | the single request in PREREG_V6_INDEPENDENT.md |
| 5 | v6 replication | NOT STARTED | - | - | automatic after a passing validation, same rules |
| 6 | Execution without money | DONE (offline) | `tests/test_execution.py` (9) | `be76e01` | wire into the strategy runner only for a pilot |
| 7 | Risk limits | DONE (offline) | `tests/test_execution.py` Risk (3) | `be76e01` | pilot values proposed, not approved |
| 8 | Pilot decision | NOT READY | needs 1/4 + 5 + 6 + 7 and D8 resolved | - | - |
