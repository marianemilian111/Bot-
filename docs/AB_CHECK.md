# A/B candidate check (read-only) - 2026-11-18, snapshots 18:58 and ~19:08 UTC

Script: `scripts/ab_check.py` (commit bd8cd95). Sources: GeckoTerminal, DexScreener, RugCheck, Solana RPC (already approved).
Thresholds as v1/v3: liquidity >= $40,000 and 5 m volume >= $25,000 in the same snapshot; safety = holders >= 300,
mint/freeze closed, LP burned/locked >= 30 d, dev < 5 %, top 10 < 35 %, no bundle (sell simulation not run).
Signals observable: VOL_SPIKE, BUY_RATIO; HOLDER_GROWTH needs 10 min of history (UNKNOWN in snapshot 1).

| Group | Pools | liq+vol (snap 1 / snap 2) | + safety pass | signals 0 / 1 / >= 2 (of safe) |
|---|---|---|---|---|
| A: 6-72 h, SIGNALS_FREE only | 0 | 0 / 0 | 0 | 0 / 0 / 0 |
| B: > 72 h in v3 discovery | 17 (14 priced) | 2 / 3 | 0 | 0 / 0 / 0 |

A: SIGNALS_FREE produced 1 event (MIGRATION_CANDIDATE, meteoradbc -> meteora, 0.21 h old) - outside 6-72 h.
B: the 3 liquid pools (age 30 d, 739 d, 492 d) all failed top-10 concentration (46 %, 70 %, 59 %) and had 0 observable
signals (holder growth NO / UNKNOWN). Caveat: RugCheck top holders may include pool or exchange accounts, which would
overstate concentration for old liquid tokens; this measurement was not changed.

Decision: STOP (no candidate in A or B). A single 10-minute window; not a statement about other hours.
