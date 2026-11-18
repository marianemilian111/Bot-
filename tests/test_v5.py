import os
import tempfile
import unittest
from datetime import datetime

from dexbot import v3
from dexbot.agents import Equity, Logger, RugCheck
from dexbot.bot import Bot
from dexbot.collector import load_state, save_state
from dexbot.models import Pair
from dexbot.variants import V1, V5, V5_RULES, assert_v5, make


def pair(**k):
    d = dict(addr="M", symbol="T", source="pumpswap", age_s=2 * 3600, price=0.001, liquidity=60_000, vol5m=40_000,
             vol_avg5m_1h=30_000, buys5m=80, sells5m=20, holders=900, holders_10m=890, price_1h_ago=0.0009, pullback_pct=0,
             dev_pct=1, top10_pct=20, bundled=False, lp_status="burned", mint_frozen=False, mint_auth_open=False, honeypot=None)
    d.update(k)
    return Pair(**d)


class T(unittest.TestCase):
    def test_single_change_vs_v1(self):
        import dataclasses
        c1, c5 = make(V1), make(V5, 250)
        diff = {k for k in dataclasses.asdict(c1) if getattr(c1, k) != getattr(c5, k)} - {"start_balance"}
        self.assertEqual(diff, {"min_signals"})
        assert_v5(c5)

    def test_universe_and_one_signal(self):
        s = v3.V3Scanner(make(V5, 250)); s.rules = V5_RULES
        self.assertIsNone(s.universe_fail(pair(age_s=600)))             # no minimum age in v5 (v1 window)
        self.assertEqual(s.universe_fail(pair(age_s=7 * 3600)), "older than 6h")
        self.assertIn("liquidity", s.universe_fail(pair(liquidity=39_000)))
        self.assertEqual(s.signals(pair()), ["BUY_RATIO"])               # exactly one signal
        self.assertEqual(len(s.scan([pair()])), 1)
        self.assertTrue(RugCheck(make(V5, 250)).check(pair(), skip_sell=True)[0])

    def test_resume_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make(V5, 250)
            b = Bot(cfg, None, datetime.now(), logdir=d, strategy_id=V5)
            b.eq.cash, b.traded = 200.0, {"M"}
            path = os.path.join(d, "s.json")
            save_state(b, path)
            b2 = Bot(cfg, None, datetime.now(), logdir=d, strategy_id=V5)
            self.assertTrue(load_state(b2, path))
            self.assertEqual((b2.eq.cash, b2.traded), (200.0, {"M"}))


if __name__ == "__main__":
    unittest.main()


class Wiring(unittest.TestCase):
    def test_run_v5_completes_offline(self):
        """End-to-end run of the v5 runner with a stub feed (no network): writes meta, states and a report."""
        import json
        import subprocess
        import sys
        from unittest import mock
        from dexbot import v3 as V

        class Feed:
            def __init__(self, cfg, **kw): self.watch, self.disc_errors = ["M"], {}
            def pin(self, a): pass
            def step(self, now): pass
            def snapshot(self): return []
            def get(self, a): return None
            def sources(self, a): return []
            def refresh(self, a): return None

        with tempfile.TemporaryDirectory() as d:
            with mock.patch("dexbot.live.LiveFeed", Feed):
                V.run_v5(0.02, early_stop_min=99, out_dir=os.path.join(d, "logs"), state_dir=os.path.join(d, "state"))
            meta = json.load(open(os.path.join(d, "logs", "v5_meta.json")))
            self.assertEqual((meta["strategy"], meta["fingerprint"]), (V5, "d9d4191e906de84d"))
            out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "scripts", "v3_report.py"), "logs/v5_meta.json"], cwd=d, capture_output=True, text=True).stdout
            self.assertIn("PnL: N/A", out)


class Persistence(unittest.TestCase):
    def test_resume_keeps_balance_positions_and_trades(self):
        import json
        from unittest import mock
        from dexbot import v3 as V
        from dexbot.models import Position, Trade

        class Feed:
            def __init__(self, cfg, **kw): self.watch, self.disc_errors = [], {}
            def pin(self, a): pass
            def step(self, now): pass
            def snapshot(self): return []
            def get(self, a): return None
            def sources(self, a): return []
            def refresh(self, a): return None

        with tempfile.TemporaryDirectory() as d:
            st, lg = os.path.join(d, "state"), os.path.join(d, "logs")
            with mock.patch("dexbot.live.LiveFeed", Feed), mock.patch.object(V.time, "sleep", lambda s: None):
                mom, base = V.run_v5(0.002, early_stop_min=99, out_dir=lg, state_dir=st)
                # simulate what a first run could leave behind: one closed trade and one open position
                mom.ex.trades.append(Trade("A", datetime(2026, 12, 1, 1), datetime(2026, 12, 1, 2), 50.0, 61.0, ["TP1_2x"], 0.0, "MA"))
                mom.ex.positions["MB"] = Position("MB", "B", datetime(2026, 12, 1, 3), 50.0, ["VOL_SPIKE"], tokens=10.0,
                                                  tokens_bought=10.0, cost=50.0, parts_done=2, last_price=5.0)
                mom.eq.cash, mom.traded = 161.0, {"MA", "MB"}
                save_state(mom, os.path.join(st, f"paper_state_{V5}.json"))
                mom2, base2 = V.run_v5(0.002, early_stop_min=99, out_dir=lg, state_dir=st, resume=True)
            self.assertEqual(mom2.eq.cash, 161.0)
            self.assertEqual([t.symbol for t in mom2.ex.trades], ["A"])
            self.assertIn("MB", mom2.ex.positions)                       # open position carried over, not sold
            self.assertEqual(mom2.traded, {"MA", "MB"})
            self.assertEqual(len(os.listdir(os.path.join(lg, "runs"))), 2) # one meta per run for coverage
