# End-to-end audit (offline, branch work/e2e-audit, from 478a434)

| # | Area | Finding | Evidence | Status |
|---|---|---|---|---|
| D1 | Stale data | A failed DexScreener fetch left old prices in the cache with no age; entries/exits could use them | offline repro: price kept, no timestamp | **fixed**: `Pair.fetched_at`; data > 120 s never used for decisions; `STALE` log line; candidate list in the runner filtered too |
| D2 | Feed robustness | `liquidity: {"usd": null}` next to another pool raised `TypeError` and dropped the whole feed step | offline repro: crash | **fixed** (null-safe comparison) |
| D3 | Holder growth signal | Holder history stamped with the cycle time, not the RugCheck read time (up to 300 s off), and duplicated each cycle | offline repro: 290 s offset | **fixed**: one entry per RugCheck read, at read time |
| D4 | Top-10 safety filter | RugCheck `topHolders` includes the AMM pool account (labelled `AMM`, equals the pool address). Coded top-10 for BUCKZ: 99.82 % vs 0.05 % without the pool; one of 3 liquid >72 h pools flips (46.3 % -> 9.6 %) | live RugCheck reports | **documented, not changed** (changing it alters a safety filter; needs a separate decision) |
| D5 | v5 wiring | Generic runner assumed the v3 feed (`v3_pools`) -> crash at the end of a v5 run | live wiring check | **fixed** + offline end-to-end test |
| D6 | v3 run: 15 pairs "liquidity UNKNOWN" | not reproducible on a small live sample; per-cycle logs are only in an artifact on a non-approved host | - | **unexplained** |
| D7 | Reporting | No per-day PnL, no-trade days or worst day | code review | **added** to the report (Momentum + baseline) |
| ok | Sell-simulation holder | excludes the pool account correctly (owner == market pubkey) | live reports | no change |
| ok | Tranches / costs | 2 x $25 each with own quote; quote already includes pool fee and impact; +$0.02/swap; exits simulate the exact amount | tests | no change |
| ok | Exit log | `logs/exit_attempts.jsonl` (478a434): UTC ms, raw amount sent, quote vs proceeds, null for missing | tests | no change |

All tests pass offline (43). Decisions and PnL for unchanged inputs were verified identical before/after 478a434.
