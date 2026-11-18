import unittest
from datetime import datetime, timedelta

from dexbot.agents import Equity, Executor, Logger, RugCheck, Scanner, Sizer
from dexbot.config import Config
from dexbot.models import Pair

T0 = datetime(2026, 1, 1, 23, 0)


def pair(**kw):
    d = dict(addr="A", symbol="TST", source="raydium", age_s=1800, price=1.0, liquidity=60_000, vol5m=40_000,
             vol_avg5m_1h=5_000, buys5m=80, sells5m=20, holders=500, holders_10m=400, price_1h_ago=0.9,
             pullback_pct=0.0, dev_pct=2.0, top10_pct=20.0, bundled=False, lp_status="burned",
             mint_frozen=False, mint_auth_open=False, honeypot=False)
    d.update(kw)
    return Pair(**d)


def mk(tmp="/tmp/dexbot_test.log"):
    cfg = Config()
    log = Logger(tmp)
    eq = Equity(1000, T0, log)
    return cfg, log, eq, Executor(cfg, log, eq)


class T(unittest.TestCase):
    def test_signals_and_universe(self):
        s = Scanner(Config())
        self.assertEqual(set(s.signals(pair())), {"VOL_SPIKE", "BUY_RATIO", "HOLDER_GROWTH"})
        self.assertIsNone(s.universe_fail(pair()))
        self.assertIn("liquidity", s.universe_fail(pair(liquidity=39_000)))
        self.assertIn("holders", s.universe_fail(pair(holders=299)))
        self.assertIn("LP", s.universe_fail(pair(lp_status="unlocked")))

    def test_rugcheck_fails_closed(self):
        r = RugCheck(Config())
        self.assertTrue(r.check(pair())[0])
        for kw in (dict(dev_pct=5.0), dict(top10_pct=35.0), dict(bundled=True), dict(lp_status="locked_short"),
                   dict(honeypot=True), dict(honeypot=None), dict(dev_pct=None)):
            self.assertFalse(r.check(pair(**kw))[0], kw)

    def test_sizer(self):
        z = Sizer(Config())
        self.assertEqual(z.size(1000, 1000, 2, 0, 0)[0], 200)
        self.assertEqual(z.size(1000, 1000, 4, 0, 0)[0], 400)
        self.assertEqual(z.size(1000, 1000, 2, 0, 2)[0], 100)     # halved after 2 losses
        self.assertEqual(z.size(1000, 1000, 2, 2, 0)[0], 0)       # max 2 open

    def test_ladder_and_trail(self):
        cfg, log, eq, ex = mk()
        p = pair()
        self.assertTrue(ex.enter(p, 200, ["A", "B"], T0))
        now = T0 + timedelta(seconds=10)
        ex.manage({"A": pair()}, now)                              # part 2
        pos = ex.positions["A"]
        self.assertEqual(pos.parts_done, 2)
        ex.manage({"A": pair(price=2.3)}, now + timedelta(seconds=10))
        self.assertEqual(pos.stage, 1)
        ex.manage({"A": pair(price=5.0)}, now + timedelta(seconds=20))
        self.assertEqual(pos.stage, 2)
        ex.manage({"A": pair(price=3.5)}, now + timedelta(seconds=30))   # 30% off peak -> trail
        self.assertNotIn("A", ex.positions)
        self.assertEqual(eq.wins, 1)

    def test_stops(self):
        cfg, log, eq, ex = mk()
        ex.enter(pair(), 200, ["A", "B"], T0)
        ex.manage({"A": pair(price=0.85)}, T0 + timedelta(seconds=10))
        self.assertNotIn("A", ex.positions)
        self.assertEqual(eq.losses, 1)
        ex.enter(pair(addr="B"), 200, ["A", "B"], T0)
        ex.manage({"B": pair(addr="B", dev_sold=True, price=0.9)}, T0 + timedelta(seconds=10))
        self.assertNotIn("B", ex.positions)
        ex.enter(pair(addr="C"), 200, ["A", "B"], T0)
        ex.manage({"C": pair(addr="C")}, T0 + timedelta(seconds=10))
        ex.manage({"C": pair(addr="C")}, T0 + timedelta(minutes=26))
        self.assertNotIn("C", ex.positions)

    def test_slippage_cap(self):
        cfg, log, eq, ex = mk()
        self.assertFalse(ex.enter(pair(liquidity=40_000), 4000, ["A", "B"], T0))
        self.assertEqual(eq.cash, 1000)

    def test_drawdown_pause(self):
        cfg, log, eq, ex = mk()
        eq.check_drawdown(T0, 1000, .3)
        eq.check_drawdown(T0, 690, .3)
        self.assertTrue(eq.paused)
        eq.check_drawdown(T0 + timedelta(days=1), 690, .3)
        self.assertFalse(eq.paused)


if __name__ == "__main__":
    unittest.main()
