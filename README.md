# DEX pair bot (Solana) - paper trading

Six agents: SCANNER (flags only) -> RUG CHECK (fail-closed) -> SIZER -> EXECUTOR, with LOGGER and EQUITY.
All thresholds are in `dexbot/config.py`.

    python3 run.py --sim --quiet     # simulated 23:00 -> 07:00 night in ~6 s, prints the 07:00 recap
    python3 run.py --sim --seed 3    # live dashboard while it runs
    python3 run.py --live            # real-time, live DexScreener/RugCheck data, paper fills, stops at 07:00
    python3 -m unittest discover -s tests

Outputs: `logs/night_*.log` (every ENTER / EXIT / FILTERED / PAUSE line), `dashboard.txt`, `state.json`.

## Read this before trusting any number
* **Paper only.** No wallet, keys or swaps. Fills are modelled from pool depth + 0.3% fee per leg.
  Going live means adding a Jupiter swap executor + a signing wallet behind `Executor._buy/_sell`.
* **`--sim` results mean nothing.** The synthetic market is built by `dexbot/sim.py` with assumptions
  (runner frequency, signal lead time) that are far kinder than reality. Use it to test the plumbing only.
* **`--live` is untested against the real APIs** and has gaps: honeypot sell-simulation, smart-money /
  KOL wallets (needs a wallet tracker such as Helius) and pump.fun launch discovery are not wired.
  Unknown data = FAIL, so gaps mean fewer entries, never riskier ones. With no smart/KOL feed and the
  honeypot check missing, live mode will not enter anything until those are added.
* Interpretations I chose: time stop applies until the runner stage (after 4x the 25% trail governs);
  size tier 20/30/40% by 2/3/4+ signals; "pullback" = >=10% off the 1h high; daily peak resets at midnight;
  one entry per pair; open positions at 07:00 are closed at market for the recap.
