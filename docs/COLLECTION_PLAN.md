# 7-day free collection plan (PROPOSAL - nothing scheduled, nothing enabled)

Goal: >= 7 days of live, point-in-time data and >= 30 closed paper trades for the frozen `momentum-v1`,
with no personal computer, no paid service, no wallet.

## Method: chained GitHub Actions runs, state kept on a data branch
* A separate workflow `collect.yml` (NOT in the repo yet) with `schedule` + `workflow_dispatch`, one job of ~5h45m
  (`timeout-minutes: 350`), cron every 6 h. `concurrency: {group: collect, cancel-in-progress: false}` prevents overlap.
* Each run: check out `collector-data` (orphan branch) -> `python run.py --collect --minutes 345 --no-entry-last-min 30`
  -> commit `state/paper_state.json` (positions, cash, trades, peak, traded set) and `logs/collect_*.jsonl`,
  `logs/night_*.log` back to `collector-data` with the built-in `GITHUB_TOKEN` (`contents: write`). The next run loads
  the state, so open positions, equity and the loss streak continue.
* The frozen fingerprint (`dexbot/frozen.py`) is checked at start; any rule/filter change aborts the run.
* Selection/validation split is decided BEFORE starting: e.g. days 1-3 selection (no change allowed afterwards except
  discarding the variant), days 4-10 validation on the frozen config. Only one variant (momentum) is implemented.

## Cost
Free only if the repo is public (unlimited minutes). A private repo has ~2,000 free minutes/month; 7 days = 10,080 min,
so it would NOT fit (it would cover about 20% of the time). Repo visibility is the decision to make before approving.

## Intervals NOT covered (stated up front)
* Between runs: runner start-up + checkout, a few minutes every 6 h, longer if GitHub queues the job.
* Scheduled workflows are best-effort: runs can start late or be skipped under load. Skipped run = 6 h gap.
* Last 30 min of each run: entries are disabled so positions can close; ~8% of the time has no new entries.
* Scheduled workflows only run from the default branch, and GitHub disables them after 60 days without repo activity.
* Rate limits from shared runner IPs (DexScreener 300/min, RugCheck/GeckoTerminal/public RPC unspecified): a blocked
  source = those intervals are lost and are recorded as ERROR/UNKNOWN, never filled in.
* DexScreener discovery only lists tokens with profiles/boosts (a biased sample of new launches); pump.fun launches that
  never get a profile are not observed. This biases the sample and is a limitation of the data, not fixable here.
* Dev-sell / LP-pull events are not observable from these sources in paper mode (only price stops apply).
* Smart-money / KOL wallets are unavailable (no list provided); only 3 signals are possible.
* Whether the public RPC keeps accepting datacenter IPs for `simulateTransaction` is unverified on runners.

## Verdict rule (unchanged)
>= 30 closed paper trades over >= 7 days of validation data, net PnL > 0 after modelled costs and failed exits,
better than the baseline with the same safety filters, max drawdown < 30%. Otherwise INSUFICIENT or NEGATIV.
The baseline (random entries passing the same filters) is NOT implemented yet.
