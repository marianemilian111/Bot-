# SIGNALS_FREE - proposal (read-only, PAPER ONLY, nothing scheduled, no workflow yet)

Module: `dexbot/signals_free.py`. It does not change v1, v2 or v3 and never decides a BUY.

## Sources
| Source | Event | Status |
|---|---|---|
| GeckoTerminal `new_pools` + DexScreener `token-pairs/v1` (both hosts already approved) | MIGRATION: token has a launch-curve pool (pumpfun, meteoradbc, launchlab, ...) **and** an AMM pool | enabled, tested live |
| Solana RPC `getSignaturesForAddress` + `getTransaction` (approved) | WALLET_BUY: confirmed swap by signature and mint | code ready; **LISTA LIPSA** (no documented wallet) |
| PumpPortal `wss://pumpportal.fun/api/data` `subscribeMigration` | MIGRATION (faster) | **proposed only, disabled**: host not approved; topic not confirmed from official docs; needs a websocket client (not in the stdlib). Left off if it ever asks for a wallet, credit or payment. `subscribeAccountTrade` is not used. |

## Rules
* MIGRATION puts the AMM pool on a watch list with `migrated_at` (AMM pool creation time) and `detected_at`.
  It is **not** a buy signal. A pool becomes eligible for the v3-style test only when its age is >= 6 h (v3 rule).
* WALLET_BUY accepts only entries in `config/wallets_public.json` that have `address`, `source_url` and `source_note`
  (where the owner made the address public). No wallet is inferred from a name, a ticker or a screenshot, and wallets
  are not picked after the fact because they caught a pump. Confirmation: no transaction error, exactly one non-quote
  mint received, SOL (beyond the fee) or a quote token paid. Transfers and sells are excluded.
  Logged per event: signature, mint, swap time, detection time, delay, price available to us after detection; the
  wallet's own implied price is informational and never our fill.

## Proposed single paper test (needs your approval and a new manual workflow)
* Fresh data only; same $50 size (2 x $25 tranches with their own quotes), same safety filters, same execution and exit
  method as v3 (sized sell simulation, FAIL -> imputed $0 shown separately, UNKNOWN -> retry).
* Candidates = v3 discovery **plus** MIGRATION watch-list pools once they reach 6 h (plus WALLET_BUY only if a documented
  list exists). Entry still requires the v3 rules; the new source only adds pools to look at.
* Report the new source separately: unique candidates it added that v3 discovery did not see, eligible entries from
  them, exits succeeded / failed (class + exact error), net PnL after costs, PnL without the best trade, open positions.
* Limitation: a pool migrated during a 90-minute run cannot reach 6 h inside that run, so in a single short run the
  migration source can only contribute pools that migrated 6-72 h before the start (found via GeckoTerminal new pools
  history is short). A meaningful contribution needs a longer or chained collection, which needs your approval.
