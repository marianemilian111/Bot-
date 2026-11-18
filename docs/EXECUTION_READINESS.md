# Execution readiness (no money: nothing signs, nothing sends)

## Built and tested offline (`dexbot/execution.py`, `tests/test_execution.py`)
| Path | Behaviour | Test |
|---|---|---|
| signal -> quote -> simulation -> submit -> confirm -> reconcile | fill only from a broker confirmation; "simulation ok (not an execution)" is journaled | `test_signal_quote_sim_submit_confirm_reconcile` |
| real broker | `LiveBroker.submit` raises `ExecutionDisabled` -> order ends `DRY_RUN_STOP`, no fill, no position | `test_live_broker_never_sends` |
| duplicate orders | client order id = hash(signal, tranche); a second call returns the first order; survives restart | `test_duplicate_signal_sends_once`, restart test |
| no sell route | `EXIT_NO_ROUTE`, position stays open, no invented sale | `test_no_sell_route_keeps_position_open` |
| expired quote (> 15 s) | one re-quote; stale twice -> blocked, never filled on an old quote | `test_expired_quote_requotes_once_then_blocks` |
| RPC error | 3 retries, then STATE_ERROR: new entries blocked, exits still run | `test_rpc_error_blocks_new_entries_but_not_exits` |
| restart with open position / unknown orders | journal reload rebuilds positions; SUBMITTED orders confirmed, SUBMITTING not found -> FAILED, nothing resent | `test_restart_with_open_position_and_unknown_orders`, `test_crash_between_journal_and_submit_is_not_resent` |
| balance mismatch | journal vs chain differ -> STATE_ERROR, entries blocked | `test_balance_mismatch_blocks_entries` |

## Risk limits (`RiskGuard`) - block NEW entries only; never liquidate
Proposed pilot values: position <= $50, total exposure <= $100, daily loss (realised + imputed) <= $25, slippage <= 2.5 %,
market data <= 120 s old; any state error (RPC, unknown submit outcome, balance mismatch) blocks entries.
Tested: each limit blocks (`test_limits_block_entries`); daily loss blocks entries, the open position is **not** sold
by the guard and its exit rules still run; entries resume on the next UTC day (`test_daily_loss_stops_entries_without_liquidating`).

## Missing before any real pilot (not built, needs the separate pilot approval)
1. Wallet and key custody (dedicated low-balance wallet, signer isolated from the bot process).
2. Transaction signing and sending (`sendTransaction`), blockhash expiry (`lastValidBlockHeight`), priority fee policy.
3. Confirmation and balance reads against the real wallet (`getSignatureStatuses`, `getTokenAccountsByOwner`).
4. A reliable RPC: the public endpoint is rate-limited; a paid RPC would be a cost and is not approved.
5. Wiring this engine into the strategy runner (the v5 paper test uses `SizedExecutor`, which only simulates).
6. Kill switch and alerting outside the job (today: job log + Summary only).
7. Account-creation / rent costs and a SOL fee reserve in the cost model.
