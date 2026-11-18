# v6 "capture" strategy - SHADOW PARTIAL definition (PAPER ONLY, separate from v5)

> **Status: SHADOW PARTIAL.** v5 trades filtered by the v6 rules. It is not a faithful v6 run, not a validation, not
> evidence of profit and not a condition for a pilot: it reuses v5 fills at v5 sizes and cannot see candidates that
> v5 did not trade. The independent v6 test is defined in docs/PREREG_V6_INDEPENDENT.md (pending).

Fixed on 2026-11-18 before any v5 trade exists (v5 validation window opened 20:43 UTC with 0 closed positions).
Code: `scripts/v6_shadow.py` (tests: `tests/test_v6_shadow.py`). v5 code, rules and collection are not changed.

## Method: shadow of the v5 collection (no new collection, no new source)
Every screenshot entry rule that the code can observe is v1's (>= 2 distinct signals, same universe and safety), and
v5 = the same with >= 1 signal. So every v6 entry is also a v5 entry: v6 is evaluated on the v5 Momentum trades that
meet the extra v6 rules, re-sized with the screenshot sizer. Limitation measured and reported: if a v5 position took a
slot (max 2 open) that a v6-eligible pair would have used, that v6 trade is missing (v5 logs these as SKIPPED).

## Rules fixed before the test (interpretations of points the screenshot leaves open)
* Universe and safety: as v5 (age <= 6 h, liquidity >= $40k, 5 m volume >= $25k, >= 300 holders, mint/freeze closed,
  LP >= 95 % locked/burned [duration not verifiable], dev < 5 % [creator address], top 10 < 35 % **raw, AMM account
  included** [no attribution method fixed], bundle proxy, sell simulation of the exact amount = PASS).
* DEX: only pump.fun (pumpfun/pumpswap), Raydium, Meteora pools.
* Signals: VOL_SPIKE (5 m volume > 5 x h1/12 for every age, as v5 measures it), BUY_RATIO (> 70 % of >= 20 txns),
  HOLDER_GROWTH (>= +10 % over >= 10 min). Smart money and KOL: UNKNOWN (no verified wallet list) -> never counted.
  Entry needs >= 2 distinct signals and RUG CHECK PASS. No-chase: +300 % in 1 h without a >= 10 % pullback from the
  1 h high -> skip (high not available live -> every +300 % pair is skipped).
* Session: entries only 23:00-07:00 Europe/Bucharest (DST-aware); exits at any time; 07:00 recap per session
  (start/end balance, best and worst trade, number of trades, top 3 coins by PnL).
* Sizer: $70 paper balance; size = 20 % of current realised equity for 2 signals, 30 % for 3 (40 % needs 4 signals,
  not observable); x 0.5 after two consecutive losses until a win; profits compound; max 2 open.
* Daily drawdown: realised equity <= 70 % of that session's realised peak -> no new entries for the rest of the session.
* Execution and exits: v5's (2 tranches, each quoted; 2.5 % cap; 40 % at 2x, 30 % at 4x, 25 % trailing; -12 %;
  25 min; FAIL -> imputed $0; UNKNOWN -> retry). Per-trade result = v5 trade return with the fixed $0.02/swap fee
  re-applied at the v6 size (price impact measured at $50 >= impact at the smaller v6 size: conservative).
  Dev-sell / LP-pull exits: LIPSA in the live feed (not modelled).

## Baseline, sample, criterion
* Baseline: v5 random-baseline trades opened in the same cycle as a v6 entry, re-sized identically. NETESTABIL unless
  >= 30 paired baseline trades and >= 20 of those draws had more than one eligible option.
* Validation = sessions inside the v5 VALIDATION window; replication = sessions inside the v5 REPLICATION window.
* Minimum per window: >= 30 closed v6 trades AND >= 7 sessions with >= 6 h of collection inside 23:00-07:00.
* Success (each window): net PnL after costs incl. imputed > 0; net without the best trade > 0; above the baseline
  (testable); max drawdown < 30 % of $70 (< $21 from peak); no trade > 30 % of net profit.
  No verdict from this shadow can mark an advantage; it is an indicator only. Reported per session: net, trades, worst
  session, sessions without data; imputed losses and failed exits separately; zero closed -> PnL N/A.
* If v5 collection stops early (INSUFICIENT / FARA AVANTAJ), v6 gets no more data -> v6 `INSUFICIENT`. Because the v6
  entry set is a subset of v5's, v6 can never have more closed trades than v5.
