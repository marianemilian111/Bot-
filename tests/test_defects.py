"""Regression tests for defects confirmed offline in the end-to-end audit (D1 stale data, D2 null liquidity, D3 holder timing)."""
import time
import unittest
from datetime import datetime
from unittest import mock

from dexbot import live
from dexbot.bot import STALE_S, Bot
from dexbot.models import Pair
from dexbot.variants import V1, make


def row(price, liq, vol=1):
    return {"chainId": "solana", "pairCreatedAt": (time.time() - 7200) * 1000, "baseToken": {"address": "M", "symbol": "T"},
            "priceUsd": str(price), "liquidity": liq, "volume": {"m5": vol, "h1": 12}, "txns": {"m5": {"buys": 1, "sells": 1}},
            "dexId": "pumpswap", "pairAddress": "P"}


class StubFeed:
    def __init__(self, p):
        self.p = p

    def step(self, now):
        pass

    def snapshot(self):
        return [self.p]

    def get(self, a):
        return self.p


class T(unittest.TestCase):
    def test_d2_null_liquidity_does_not_crash(self):
        f = live.LiveFeed(make(V1)); f.watch = ["M"]; f._discover = lambda: None
        with mock.patch.object(live, "_get", lambda u: {"pairs": [row(1.1, {"usd": None}), row(1.0, {"usd": 50000})]}):
            f.step(datetime.now())
        self.assertEqual(f.snapshot()[0].liquidity, 50000)

    def test_d3_holder_history_uses_fetch_time_once(self):
        f = live.LiveFeed(make(V1)); t0 = time.time()
        f.sec["M"] = (t0 - 290, {"holders": 500})
        for _ in range(3):
            f._to_pair("M", row(1.0, {"usd": 50000}, vol=30000))
        self.assertEqual(list(f.holder_hist["M"]), [(t0 - 290, 500)])

    def test_d1_no_decision_on_stale_data(self):
        cfg = make(V1)
        p = Pair(addr="M", symbol="T", source="x", age_s=600, price=0.5, liquidity=50_000, vol5m=1, vol_avg5m_1h=1, buys5m=1,
                 sells5m=1, holders=500, holders_10m=500, price_1h_ago=1, pullback_pct=0, fetched_at=time.time() - STALE_S - 5)
        bot = Bot(cfg, StubFeed(p), datetime.now(), logdir="/tmp")
        bot.ex.enter(Pair(**{**p.__dict__, "price": 1.0, "fetched_at": time.time()}), 200, ["a", "b"], datetime.now())
        bot.tick(datetime.now())                                   # price -50 % but stale: no stop executed
        self.assertIn("M", bot.ex.positions)
        self.assertTrue(any(" STALE " in l for l in bot.log.tail))
        p.fetched_at = time.time()                                 # fresh data: the stop is applied
        bot.tick(datetime.now())
        self.assertNotIn("M", bot.ex.positions)


if __name__ == "__main__":
    unittest.main()


class FeedRobustness(unittest.TestCase):
    def test_minus_100_percent_h1_does_not_crash(self):
        """Run 44407410908 crashed on priceChange.h1 == -100 (division by zero)."""
        f = live.LiveFeed(make(V1)); f.watch = ["M"]; f._discover = lambda: None
        r = row(1.0, {"usd": 50000}); r["priceChange"] = {"h1": -100}
        with mock.patch.object(live, "_get", lambda u: {"pairs": [r]}):
            f.step(datetime.now())
        self.assertIsNone(f.snapshot()[0].price_1h_ago)

    def test_v6_runner_survives_a_failing_feed_step(self):
        import os, tempfile
        from dexbot import v6_runner as R
        from dexbot.variants import V6_RULES, make_v6

        class Bad:
            def __init__(self, cfg, **kw): self.cache, self.watch, self.disc_errors = {}, [], {}
            def step(self, now): raise ZeroDivisionError("float division by zero")
            def snapshot(self): return []
            def get(self, a): return None
            def pin(self, a): pass
            def sources(self, a): return []
            def refresh(self, a): return None

        with tempfile.TemporaryDirectory() as d, mock.patch.object(R.time, "sleep", lambda s: None):
            mom, _ = R.run_v6(make_v6(), V6_RULES, "fp", 0.001, interval_s=0, out_dir=os.path.join(d, "logs"),
                              state_dir=os.path.join(d, "state"), wallets_path=os.path.join(d, "x.json"), feed_factory=Bad,
                              rpc=lambda m, p: {})
            self.assertTrue(os.path.exists(os.path.join(d, "logs", "v6_meta.json")))      # run completed and saved meta
            self.assertTrue(any("feed step failed" in l for l in mom.log.tail))
