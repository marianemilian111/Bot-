import unittest
from unittest import mock

from dexbot import sellsim
from dexbot.config import Config
from dexbot.frozen import FROZEN_FINGERPRINT, fingerprint
from dexbot.agents import Equity, Executor, Logger, RugCheck
from dexbot.models import Pair
from datetime import datetime

T0 = datetime(2026, 1, 1, 23, 0)


class T(unittest.TestCase):
    def test_frozen(self):
        self.assertEqual(fingerprint(Config()), FROZEN_FINGERPRINT)
        self.assertNotEqual(fingerprint(Config(hard_stop=0.2)), FROZEN_FINGERPRINT)

    def _sim(self, quote, swap, rpc):
        seq = iter([quote, swap, rpc])
        with mock.patch.object(sellsim, "http_json", lambda *a, **k: next(seq)):
            return sellsim.simulate_sell("M", ("owner", 10 ** 9))

    def test_sell_simulation_states(self):
        q, sw = (200, {"routePlan": []}), (200, {"swapTransaction": "x"})
        self.assertEqual(self._sim(q, sw, (200, {"result": {"value": {"err": None}}}))[0], "OK")
        self.assertEqual(self._sim(q, sw, (200, {"result": {"value": {"err": {"InstructionError": [2, "x"]}}}}))[0], "FAIL")
        self.assertEqual(self._sim(q, sw, (None, "timeout"))[0], "UNKNOWN")
        self.assertEqual(self._sim((200, {"x": 1}), sw, (None, ""))[0], "UNKNOWN")     # quote without route plan is still not a pass
        self.assertEqual(sellsim.simulate_sell("M", None)[0], "UNKNOWN")

    def test_quote_alone_never_passes(self):
        # even with a good quote, a failed build/simulation can never become OK
        self.assertNotEqual(self._sim((200, {"routePlan": []}), (500, "err"), (200, {}))[0], "OK")

    def test_exit_gate(self):
        cfg = Config()
        log = Logger("/tmp/dexbot_test2.log")
        eq = Equity(1000, T0, log)
        ex = Executor(cfg, log, eq)
        mk = lambda **k: Pair(**{**dict(addr="A", symbol="T", source="raydium", age_s=100, price=1.0, liquidity=60_000,
                                         vol5m=1, vol_avg5m_1h=1, buys5m=1, sells5m=1, holders=1, holders_10m=1,
                                         price_1h_ago=1, pullback_pct=0), **k})
        ex.enter(mk(), 200, ["a", "b"], T0)
        ex.sell_gate = lambda p: "UNKNOWN"
        ex.manage({"A": mk(price=0.8)}, T0)                    # stop hit but sale unverifiable: no fill invented
        self.assertIn("A", ex.positions)
        ex.sell_gate = lambda p: "FAIL"
        ex.manage({"A": mk(price=0.8)}, T0)                    # sale blocked: written off, counted as a loss
        self.assertNotIn("A", ex.positions)
        self.assertEqual((ex.failed_exits, eq.losses), (1, 1))
        self.assertAlmostEqual(ex.trades[-1].pnl, -100.0)   # only part 1 was filled

    def test_skip_sell(self):
        r = RugCheck(Config())
        p = Pair(addr="A", symbol="T", source="x", age_s=1, price=1, liquidity=1, vol5m=1, vol_avg5m_1h=1, buys5m=1,
                 sells5m=1, holders=1, holders_10m=1, price_1h_ago=1, pullback_pct=0, dev_pct=1, top10_pct=1,
                 bundled=False, lp_status="burned", mint_frozen=False, mint_auth_open=False, honeypot=None)
        self.assertTrue(r.check(p, skip_sell=True)[0])
        self.assertFalse(r.check(p)[0])


if __name__ == "__main__":
    unittest.main()
