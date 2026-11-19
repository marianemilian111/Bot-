# Breakout - decision criteria, fixed BEFORE any probe or validation result (PAPER ONLY)

Rules of the signal: `docs/BREAKOUT_RULES.md` @ 375b78f (unchanged). Implemented in `scripts/breakout_compare.py`
(`PROBE`, `VALIDATION`, `evaluate_probe`, `evaluate_validation`, `metrics`); tests in `tests/test_breakout_probe.py`.
Capital: $70 paper, $14 per entry (20 %), no compounding. Primary scenario: 30 s latency; 60 s must not lose.

## Costs (counted once)
BUY: the Jupiter quote is for $13.98 of SOL; the remaining $0.02 is the network fee -> cost = $14.00. SELL: the quote
output for the exact held amount (pool fees and price impact already inside), minus $0.02. Latency is modelled by WHEN
the quote is taken (decision/trigger + 0/30/60 s), not by an extra cost. No extra slippage is added on top of a quote.
A quote is not a fill: it is the best available evidence without signing.

## Stage 1 - probe (110 min, <= 60 buy + <= 120 sell requests): are there tradeable opportunities?
| Outcome | Condition |
|---|---|
| BLOCAT (feed) | < 90 % of cycles with a complete feed |
| STOP STRATEGIE | complete feed and zero eligible signals -> on this universe the rules give no trades |
| BLOCAT (quotes) | signals but < 2 quoted entries in every variant at 30 s, or sell simulations UNKNOWN > 20 % |
| CONTINUA PAPER | >= 2 quoted entries in a variant at 30 s and sell answers known >= 80 %. NOT an edge |

## Stage 2 - validation (needs a separate approval; up to 14 days), per variant, at 30 s latency
| Criterion | Threshold | Why |
|---|---|---|
| Closed trades | >= 30 | below this one or two trades decide the sum |
| Net PnL after all costs, imputed losses included | >= +$7.00 and >= +10 % of $70 | below this the result is within the noise of fees and slippage |
| Net without the best trade | > 0 | not one lucky position |
| Best trade share of net | <= 30 % | same |
| t of per-trade net PnL (mean / standard error) | >= 2.0 | consistent, not luck; with typical spreads this is the binding criterion |
| Max drawdown of closed PnL | <= $21 (30 % of $70) | maximum accepted paper loss |
| Failed exits (sell simulation FAIL) | <= 5 % of exit fills; each is a $0 write-off inside net | a failing exit is a real total loss; above 5 % = RESPINSA for risk even with profit |
| Exit triggers without any quote | <= 20 %, otherwise BLOCAT | open positions cannot be valued |
| Net at 60 s latency | >= 0 | the edge must survive slower execution |
| Replication | same criteria on new days, same commit | protects against choosing the better of two variants by chance |
Losing after costs with >= 30 closes -> RESPINSA. No closes -> PnL closed N/A. Open positions, failed exits and imputed
losses are always reported apart. No extrapolation to a larger size (price impact must be tested separately).

## Money decision
CANDIDAT LA PILOT only after Stage 2 + replication PASS and executor controls verified (signing/sending still unbuilt).
Never from Stage 1.
