"""Regression: GTASZN (A4jXnW...) exit in v6b run 44425403576 - HARD_STOP at 01:00:28 UNKNOWN 'no public holder' while
vol5m = $23,617 (< $25k entry threshold), OK at 01:02 after vol5m rose to $26,441. Same cause blocked every exit of
breakout probe 44438535268 (Dfh5Dz..., vol5m < $25k during 02:28-02:44, 120/120 UNKNOWN, budget exhausted)."""
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import breakout_probe  # noqa: E402
from dexbot import execsim  # noqa: E402
from dexbot.breakout import LiveQuotes  # noqa: E402
from dexbot.config import Config  # noqa: E402
from dexbot.live import LiveFeed  # noqa: E402

MINT = "A4jXnWk1rDRDth6HmDASi45c89n1AguTHivAQPTVpump"
TOKENS = 2741.995655                                   # exact amount v6b tried to sell
POOL = "PoolGTASZN1111111111111111111111111111111111"
ROW = {"chainId": "solana", "pairAddress": POOL, "dexId": "pumpswap", "pairCreatedAt": int((time.time() - 3600) * 1000),
       "baseToken": {"address": MINT, "symbol": "GTASZN"}, "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
       "priceUsd": "0.002146", "priceNative": "0.00001", "liquidity": {"usd": 113916}, "volume": {"m5": 23617, "h1": 300000},
       "txns": {"m5": {"buys": 40, "sells": 30}}, "priceChange": {"h1": 5}}
REPORT = {"topHolders": [{"owner": POOL, "amount": 10 ** 15, "pct": 20},
                         {"owner": "HolderPublic111111111111111111111111111111111", "amount": 50 * 10 ** 9, "pct": 2}],
          "markets": [{"pubkey": POOL, "lp": {"lpLockedPct": 100}, "mintLP": "11111111111111111111111111111111"}],
          "mintAuthority": None, "freezeAuthority": None, "totalHolders": 900, "token": {"supply": 10 ** 15}}


def fake_get(url, timeout=10):
    return REPORT if "rugcheck" in url else {"pairs": [ROW]}


def fake_http(url, data=None, **k):
    if "/quote" in url:
        return 200, {"outAmount": "81987920", "priceImpactPct": "0.008"}
    if "/swap" in url:
        return 200, {"swapTransaction": "AAAA"}
    return 200, {"result": {"value": {"err": None, "logs": []}}}


class ExitHolders(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch("dexbot.live._get", fake_get), mock.patch.object(execsim, "http_json", fake_http),
                  mock.patch.object(execsim, "sol_usd", lambda: 72.0), mock.patch.object(execsim, "token_decimals", lambda m: 6)]
        for x in self.p:
            x.start()
        self.feed = LiveFeed(Config())
        self.feed.cache[MINT] = self.feed._to_pair(MINT, ROW)

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_old_path_reproduces_unknown(self):
        old = self.feed.get(MINT).sell_holders                  # observation below the entry volume threshold
        self.assertIsNone(old)
        r = execsim.sell_simulation(MINT, old, TOKENS)
        self.assertEqual((r["status"], r["stage"]), ("UNKNOWN", "holder lookup"))

    def test_new_path_gets_holders_and_simulates(self):
        h = breakout_probe.exit_holders(self.feed, MINT)
        self.assertTrue(h and h[0][0] == "HolderPublic111111111111111111111111111111111")   # pool excluded
        r = execsim.sell_simulation(MINT, h, TOKENS)
        self.assertEqual((r["status"], r["stage"]), ("OK", "simulateTransaction"))
        self.assertEqual(r["amount_raw"], int(TOKENS * 10 ** 6))                         # exact amount


class LimitedRetries(unittest.TestCase):
    def test_stuck_position_cannot_starve_others(self):
        calls = []

        def sell_fn(mint, holders, tokens):
            calls.append((mint, tokens))
            return {"status": "UNKNOWN", "stage": "holder lookup", "detail": "x"}

        import tempfile
        t0 = datetime(2026, 11, 19, 2, 28, tzinfo=timezone.utc)
        lq = LiveQuotes(os.path.join(tempfile.mkdtemp(), "q.jsonl"), None, sell_fn, lambda m: None, max_sell=120)
        for i in range(40):                                     # 20 min of 30-s retriggers for the stuck position
            t = t0 + timedelta(seconds=30 * i)
            self.assertIsNone(lq.sell("STUCK", 777.40824, t, 60, t))   # UNKNOWN never becomes a fill
        self.assertEqual(sum(1 for c in calls if c[0] == "STUCK"), 8)
        t = t0 + timedelta(minutes=21)
        lq.sell("OTHER", 4427.402911, t, 60, t)
        self.assertIn(("OTHER", 4427.402911), calls)            # budget left for the next position
        self.assertEqual(lq.used_sell, 9)


if __name__ == "__main__":
    unittest.main()
