import dataclasses
import unittest
from datetime import datetime
from unittest import mock

from dexbot import v3
from dexbot.agents import Equity, Logger
from dexbot.models import Pair
from dexbot.variants import V1, V3, V3_RULES, assert_v3, make

T0 = datetime(2026, 1, 1, 12, 0)


def pair(**k):
    d = dict(addr="M", symbol="TOK", source="pumpswap", age_s=24 * 3600, price=0.001, liquidity=60_000, vol5m=40_000,
             vol_avg5m_1h=5_000, buys5m=80, sells5m=20, holders=900, holders_10m=800, price_1h_ago=0.0009, pullback_pct=0,
             dev_pct=1, top10_pct=20, bundled=False, lp_status="burned", mint_frozen=False, mint_auth_open=False,
             honeypot=None, sell_holders=[("H", 10 ** 15)])
    d.update(k)
    return Pair(**d)


class T(unittest.TestCase):
    def test_rules_pinned_v1_thresholds_kept(self):
        c, c1 = make(V3, 250), make(V1)
        assert_v3(c)
        for f in ("min_liq", "min_vol5m", "min_signals", "min_holders", "hard_stop", "time_stop_s", "tp1_mult", "trail_pct"):
            self.assertEqual(getattr(c, f), getattr(c1, f), f)
        self.assertEqual((c.max_age_s, V3_RULES["min_age_s"], V3_RULES["position_usd"]), (72 * 3600, 6 * 3600, 50.0))

    def test_age_window(self):
        s = v3.V3Scanner(make(V3))
        self.assertIn("younger", s.universe_fail(pair(age_s=3 * 3600)))
        self.assertIn("older than 72h", s.universe_fail(pair(age_s=80 * 3600)))
        self.assertIsNone(s.universe_fail(pair()))
        self.assertIn("liquidity", s.universe_fail(pair(liquidity=39_000)))      # floor not lowered

    def _ex(self):
        cfg = make(V3, 250)
        log = Logger("/tmp/dexbot_v3_test.log")
        eq = Equity(250, T0, log)
        return v3.SizedExecutor(cfg, log, eq), eq

    def test_fill_from_quote_and_exit_only_via_simulation(self):
        ex, eq = self._ex()
        with mock.patch.object(v3, "buy_quote", lambda m, usd: {"status": "OK", "stage": "BUY_QUOTE", "detail": "", "tokens": usd / 0.00101,
                                                               "fill_px": 0.00101, "quoted_impact": 0.005}):
            self.assertTrue(ex.enter(pair(), 50, ["A", "B"], T0))
            ex.manage({"M": pair()}, T0)                                       # part 2
        self.assertEqual(eq.cash, 200)
        with mock.patch.object(v3, "sell_simulation", lambda m, h, t: {"status": "UNKNOWN", "stage": "SELL_QUOTE", "detail": "http=None"}):
            ex.manage({"M": pair(price=0.0008)}, T0)                           # stop hit, sale unverifiable: no fill
        self.assertIn("M", ex.positions)
        with mock.patch.object(v3, "sell_simulation", lambda m, h, t: {"status": "OK", "stage": "simulateTransaction", "detail": "err=null",
                                                                    "proceeds_usd": 38.0, "quoted_impact": 0.01}):
            ex.manage({"M": pair(price=0.0008)}, T0)
        self.assertNotIn("M", ex.positions)
        self.assertAlmostEqual(ex.trades[-1].pnl, -12.0)
        self.assertEqual(ex.trades[-1].written_off, 0.0)

    def test_fail_written_off_separately(self):
        ex, eq = self._ex()
        with mock.patch.object(v3, "buy_quote", lambda m, usd: {"status": "OK", "stage": "BUY_QUOTE", "detail": "", "tokens": usd / 0.001,
                                                               "fill_px": 0.001, "quoted_impact": 0.0}):
            ex.enter(pair(), 50, ["A", "B"], T0)
        with mock.patch.object(v3, "sell_simulation", lambda m, h, t: {"status": "FAIL", "stage": "simulateTransaction", "detail": "err=X"}):
            ex.manage({"M": pair(price=0.0008)}, T0)
        t = ex.trades[-1]
        self.assertEqual((t.proceeds, round(t.written_off, 2), ex.failed_exits), (0, 25.0, 1))

    def test_fixed_size(self):
        z = v3.FixedSizer(50, 2)
        self.assertEqual(z.size(250, 250, 2, 0, 3)[0], 50)
        self.assertEqual(z.size(250, 40, 2, 0, 0)[0], 0)
        self.assertEqual(z.size(250, 250, 2, 2, 0)[0], 0)


if __name__ == "__main__":
    unittest.main()
