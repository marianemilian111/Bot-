import json
import os
import tempfile
import time
import unittest
from unittest import mock

from dexbot import v3, v6_runner as R
from dexbot.models import Pair
from dexbot.variants import V6_RULES, make_v6


class Feed:
    """Stub market: one pumpswap pair with 2 signals; price falls 20 % after 3 cycles."""

    def __init__(self, cfg, wallet_hook=None, **kw):
        self.cfg, self.n, self.cache, self.watch, self.disc_errors = cfg, 0, {}, ["M"], {}

    def _p(self):
        price = 1.0 if self.n < 4 else 0.8
        return Pair(addr="M", symbol="TOK", source="pump.fun", dex_id="pumpswap", pair_addr="POOL", age_s=3600, price=price,
                    liquidity=60_000, vol5m=40_000, vol_avg5m_1h=5_000, buys5m=80, sells5m=20, holders=900, holders_10m=890,
                    price_1h_ago=0.9, pullback_pct=0, dev_pct=1, top10_pct=20, bundled=False, lp_status="burned",
                    lp_by_market={"POOL": 100.0}, lp_burned=True, mint_frozen=False, mint_auth_open=False, honeypot=None,
                    sell_holders=[("H", 10 ** 15)], creator=None, fetched_at=time.time())

    def step(self, now):
        self.n += 1; self.cache["M"] = self._p()

    def snapshot(self): return list(self.cache.values())
    def get(self, a): return self.cache.get(a)
    def pin(self, a): pass
    def sources(self, a): return []
    def refresh(self, a): self.cache["M"] = self._p(); return self.cache["M"]


def bq(m, usd):
    return {"status": "OK", "stage": "BUY_QUOTE", "detail": "", "tokens": usd / 1.005, "fill_px": 1.005, "quoted_impact": 0.004}


def ss(m, h, tokens):
    return {"status": "OK", "stage": "simulateTransaction", "detail": "err=null", "proceeds_usd": tokens * 0.79, "quoted_impact": 0.01}


class T(unittest.TestCase):
    def run_once(self, d, resume=False):
        with mock.patch.object(R, "session_of", lambda t: "2026-11-20"), mock.patch.object(R, "buy_quote", bq), \
                mock.patch.object(R, "sell_simulation", ss), mock.patch.object(v3, "buy_quote", bq), \
                mock.patch.object(v3, "sell_simulation", ss), mock.patch.object(R.time, "sleep", lambda s: None):
            return R.run_v6(make_v6(), V6_RULES, "fp", 0.004, interval_s=0, out_dir=os.path.join(d, "logs"),
                            state_dir=os.path.join(d, "state"), resume=resume, wallets_path=os.path.join(d, "none.json"),
                            feed_factory=Feed, rpc=lambda m, p: {"value": []}, no_entry_last_min=0)

    def test_end_to_end_entry_stop_exit_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            mom, base = self.run_once(d)
            self.assertEqual(len(mom.ex.trades), 1)
            t = mom.ex.trades[0]
            self.assertAlmostEqual(t.cost, 14.0)                          # 20 % of $70 for 2 signals
            self.assertIn("HARD_STOP", t.reasons)
            self.assertLess(t.pnl, 0)
            self.assertAlmostEqual(mom.eq.cash, 70 + t.pnl)
            meta = json.load(open(os.path.join(d, "logs", "v6_meta.json")))
            self.assertIn("LISTA LIPSA", meta["wallets"])
            rows = [json.loads(l) for l in open(os.path.join(d, "logs", meta["jsonl"]))]
            for k in ("buys5m", "sells5m", "vol_avg5m_1h", "holders_10m", "dev_pct", "top10_pct_raw", "lp_traded_pool", "bundled"):
                self.assertIn(k, rows[0])                                 # full decision record per cycle
            mom2, _ = self.run_once(d, resume=True)
            self.assertEqual(len(mom2.ex.trades), 1)                      # same pair not re-entered after restart
            self.assertAlmostEqual(mom2.eq.cash, mom.eq.cash)


if __name__ == "__main__":
    unittest.main()
