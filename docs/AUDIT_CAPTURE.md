# Audit: strategy from the screenshot vs. the code (branch work/e2e-audit @ be76e01)

Legend: IMPLEMENTAT = in code and tested; DIFERIT = present but not as written; LIPSA = absent; NEVERIFICAT = present, not
verifiable with the approved sources.

| # | Rule (screenshot) | Status | Where / why |
|---|---|---|---|
| U1 | pairs launched or migrated in the last 6 h | IMPLEMENTAT | `Config.max_age_s`, age = pool creation (`pairCreatedAt`); a migration starts a new pool age |
| U2 | only pump.fun, Raydium, Meteora | DIFERIT | no DEX whitelist in v1/v5 (Orca, etc. accepted) |
| U3 | liquidity >= $40k, 5 m volume >= $25k, >= 300 holders | IMPLEMENTAT | `Scanner.universe_fail`; holders = RugCheck `totalHolders` (refreshed every 5 min) |
| U4 | no frozen mint, no mint authority | IMPLEMENTAT | RugCheck `freezeAuthority` / `mintAuthority` |
| U5 | no unlocked LP | DIFERIT | `lpLockedPct >= 95` is treated as "burned"; lock duration is not checked |
| S1 | new pairs | IMPLEMENTAT | discovery (profiles, boosts, GeckoTerminal) - discovery only |
| S2 | volume spike > 5x 1 h average | DIFERIT | average = h1 volume / 12, which includes the current window; for pools younger than 1 h the h1 figure is the whole life |
| S3 | buy share > 70 % | IMPLEMENTAT | by transaction count over 5 min (>= 20 txns), not by USD volume |
| S4 | holder growth | IMPLEMENTAT (threshold interpreted) | >= +10 % vs >= 10 min earlier; history keyed to RugCheck read time (fix D3) |
| S5 | smart money 3 of 40 wallets in 10 min | LIPSA -> UNKNOWN | no verified wallet list (LISTA LIPSA); never counted |
| S6 | KOL wallets that actually bought | LIPSA -> UNKNOWN | same; WALLET_BUY parser exists (`signals_free.py`) but no documented addresses |
| R1 | dev wallet < 5 % | DIFERIT | RugCheck `creatorBalance / supply` = creator address only, not linked wallets |
| R2 | top 10 holders < 35 % | IMPLEMENTAT (raw) | AMM pool account included (raw value kept); no attribution method fixed, so not excluded |
| R3 | no bundled launch | DIFERIT | proxy: RugCheck risk names containing "bundl"/"snip" |
| R4 | LP burned or locked >= 30 d | DIFERIT | see U5 (duration not verified) |
| R5 | sell simulation must pass | IMPLEMENTAT (v3/v5) / DIFERIT (v1/v2) | v3/v5: exact position amount; v1/v2 path: small fixed amount |
| Z1 | size from current equity 20-40 %, max 2 open | IMPLEMENTAT (v1) / DIFERIT (v3/v5: fixed $50) | `Sizer`: 2 signals 20 %, 3 -> 30 %, >= 4 -> 40 % (only 3 signals observable -> max 30 %) |
| Z2 | -50 % size after two consecutive losses | IMPLEMENTAT (v1) | `Sizer` + `Equity.loss_streak` |
| Z3 | compound profits | IMPLEMENTAT (v1) | size from current equity |
| E1 | max slippage 2.5 %, 2 tranches quoted separately | IMPLEMENTAT (v3/v5) / DIFERIT (v1: modelled, not quoted) | `SizedExecutor` |
| E2 | 40 % at 2x, 30 % at 4x, 30 % trailing 25 % | IMPLEMENTAT | `Executor.manage` |
| E3 | hard stop -12 %, time stop 25 min | IMPLEMENTAT (interpreted) | time stop applies until the 4x stage; checked every 30 s cycle |
| E4 | exit if dev sells or LP pulled | LIPSA (live) | `dev_sold` / `lp_pulled` exist only in the simulator; the live feed never sets them |
| L1 | log ENTER / EXIT / FILTERED with reason, size, fill, PnL | IMPLEMENTAT | bot logs; `logs/exit_attempts.jsonl` (raw amount, quote, proceeds, error) on the exit-logging branch |
| L2 | equity curve on every close | IMPLEMENTAT | `Equity.on_close` |
| L3 | session 23:00-07:00 with 07:00 recap | DIFERIT | recap exists only in `run()` (sim/live), container local time, no fixed time zone; not in the sized runner |
| X1 | >= 2 distinct signals + RUG CHECK PASS | IMPLEMENTAT (v1/v3) | v5 uses >= 1 by pre-registration |
| X2 | no chase: +300 % in 1 h without pullback | DIFERIT | live `pullback_pct` is unknown -> every +300 % pair is skipped (strict) |
| X3 | daily drawdown 30 % from peak -> pause entries | IMPLEMENTAT (interpreted) | mark-to-market equity, day boundary = container local date |

Conclusion: the screenshot strategy, written faithfully, is v1's entry logic (>= 2 signals) with equity-based sizing.
Its entry set is a strict subset of v5's (v5 needs >= 1 signal, same universe and safety).
