import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from dexbot import execsim, v3
from dexbot.agents import Equity, Logger
from dexbot.models import Pair
from dexbot.variants import V3, make

T0 = datetime(2026, 1, 1, 12, 0, 5)
DEC, SOLUSD = 6, 150.0


def pair(**k):
    d = dict(addr="MintAAAApump", symbol="TOK", source="pumpswap", age_s=24 * 3600, price=0.001, liquidity=60_000, vol5m=40_000,
             vol_avg5m_1h=5_000, buys5m=80, sells5m=20, holders=900, holders_10m=800, price_1h_ago=0.0009, pullback_pct=0,
             dev_pct=1, top10_pct=20, bundled=False, lp_status="burned", mint_frozen=False, mint_auth_open=False,
             honeypot=None, sell_holders=[("HOLDER", 10 ** 15)])
    d.update(k)
    return Pair(**d)


QUOTE = (200, {"outAmount": "253200000", "priceImpactPct": "0.012"})
CASES = {
    "ok": [QUOTE, (200, {"swapTransaction": "tx"}), (200, {"result": {"value": {"err": None}}})],
    "fail_after_quote": [QUOTE, (200, {"swapTransaction": "tx"}),
                         (200, {"result": {"value": {"err": {"InstructionError": [3, {"Custom": 6001}]},
                                                     "logs": ["Program log: Error: SlippageToleranceExceeded " + "x" * 300]}}})],
    "no_route": [(400, '{"error":"Could not find any route","errorCode":"COULD_NOT_FIND_ANY_ROUTE"}')],
}


def run_case(name, d):
    log = Logger(os.path.join(d, f"night_{name}.log"))
    eq = Equity(250, T0, log)
    ex = v3.SizedExecutor(make(V3, 250), log, eq)
    with mock.patch.object(v3, "buy_quote", lambda m, usd: {"status": "OK", "stage": "BUY_QUOTE", "detail": "", "tokens": usd / 0.00101,
                                                           "fill_px": 0.00101, "quoted_impact": 0.005}):
        ex.enter(pair(), 50, ["VOL_SPIKE", "BUY_RATIO"], T0)
        ex.manage({"MintAAAApump": pair()}, T0)
    seq = iter(CASES[name])
    with mock.patch.object(execsim, "sol_usd", lambda: SOLUSD), mock.patch.object(execsim, "token_decimals", lambda m: DEC), \
            mock.patch.object(execsim, "http_json", lambda *a, **k: next(seq)):
        ex.manage({"MintAAAApump": pair(price=0.0008)}, T0)
    lines = [json.loads(l) for l in open(os.path.join(d, "exit_attempts.jsonl"))]
    return ex, eq, lines


class T(unittest.TestCase):
    def test_three_cases(self):
        with tempfile.TemporaryDirectory() as d:
            ex, eq, l1 = run_case("ok", d)
            e = l1[-1]
            self.assertEqual((e["status"], e["stage"], e["error"]), ("OK", "simulateTransaction", None))
            self.assertEqual(e["quote_out_lamports"], 253200000)
            self.assertAlmostEqual(e["proceeds_usd"], 0.2532 * SOLUSD - execsim.NET_FEE_USD)
            self.assertEqual(e["amount_raw"], int(e["tokens"] * 10 ** DEC))       # what was actually sent
            self.assertIsNone(e["imputed_loss_usd"])
            self.assertTrue(e["ts_utc"].endswith("Z") and len(e["ts_utc"].split(".")[1]) == 4)   # milliseconds + Z
            self.assertAlmostEqual(ex.trades[-1].pnl, 0.2532 * SOLUSD - execsim.NET_FEE_USD - 50)

        with tempfile.TemporaryDirectory() as d:
            ex, eq, l2 = run_case("fail_after_quote", d)
            e = l2[-1]
            self.assertEqual((e["status"], e["class"], e["quote_out_lamports"], e["proceeds_usd"]),
                             ("FAIL", "EXECUTION_LIMIT", 253200000, None))
            self.assertIn("x" * 300, e["error"])                                   # not truncated
            self.assertAlmostEqual(e["imputed_loss_usd"], 50.0)
            self.assertEqual((ex.trades[-1].proceeds, ex.trades[-1].written_off), (0, 50.0))

        with tempfile.TemporaryDirectory() as d:
            ex, eq, l3 = run_case("no_route", d)
            e = l3[-1]
            self.assertEqual((e["status"], e["class"], e["stage"], e["quote_out_lamports"], e["quote_impact"], e["proceeds_usd"]),
                             ("FAIL", "NO_ROUTE", "SELL_QUOTE", None, None, None))
            self.assertIsNotNone(e["amount_raw"])
            for ev in (l1[-1], l2[-1], l3[-1]):
                self.assertNotIn("NaN", json.dumps(ev))

    def test_redaction(self):
        self.assertNotIn("abc123", execsim.redact("https://x?sig=abc123&a=1 Bearer abc123"))


if __name__ == "__main__":
    unittest.main()
