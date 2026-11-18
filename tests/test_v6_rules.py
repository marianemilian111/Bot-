import unittest
from datetime import datetime, timezone

from dexbot import v6
from dexbot.agents import Logger
from dexbot.config import Config
from dexbot.models import Pair, Trade
from dexbot.signals_free import WalletBuy

MAIN, DUST = "MAINPOOL", "DUSTPOOL"


def pair(**k):
    d = dict(addr="M", symbol="T", source="pump.fun", dex_id="pumpswap", pair_addr=MAIN, age_s=2 * 3600, price=1.0,
             liquidity=60_000, vol5m=40_000, vol_avg5m_1h=5_000, buys5m=80, sells5m=20, holders=900, holders_10m=800,
             price_1h_ago=0.9, pullback_pct=0, dev_pct=1, top10_pct=20, bundled=False, lp_status="burned",
             lp_by_market={MAIN: 100.0}, mint_frozen=False, mint_auth_open=False, honeypot=False)
    d.update(k)
    return Pair(**d)


class Universe(unittest.TestCase):
    def test_dex_whitelist(self):
        s = v6.V6Scanner(Config())
        self.assertIsNone(s.universe_fail(pair()))
        for ok in ("pumpfun", "raydium", "meteora", "meteoradbc"):
            self.assertIsNone(s.universe_fail(pair(dex_id=ok)), ok)
        self.assertIn("DEX", s.universe_fail(pair(dex_id="orca")))
        self.assertIn("DEX", s.universe_fail(pair(dex_id=None)))

    def test_lp_judged_on_traded_pool_D8(self):
        """Real case (ANT, RugCheck 2026-11-18): main pump AMM pool ~0 % locked, dust Meteora pool 100 %."""
        s, r = v6.V6Scanner(Config()), v6.V6RugCheck(Config())
        p = pair(lp_by_market={MAIN: 3.2e-10, DUST: 100.0}, lp_status="burned")   # v1/v5 'max over markets' says burned
        self.assertIn("traded pool", s.universe_fail(p))
        self.assertFalse(r.check(p)[0])
        self.assertIn("UNKNOWN", s.universe_fail(pair(lp_by_market={DUST: 100.0})))   # traded pool not reported -> reject
        self.assertTrue(r.check(pair())[0])

    def test_age_window(self):
        s = v6.V6Scanner(Config())
        self.assertIsNone(s.universe_fail(pair(age_s=60)))
        self.assertIn("older", s.universe_fail(pair(age_s=6 * 3600 + 1)))


class Signals(unittest.TestCase):
    def test_volume_average_for_young_pools(self):
        s = v6.V6Scanner(Config())
        # pool 10 min old: h1 volume = 12 * 5,000 = 60,000 over 2 windows -> average 30,000; 40,000 is not > 5x
        self.assertNotIn("VOL_SPIKE", s.signals(pair(age_s=600)))
        self.assertIn("VOL_SPIKE", s.signals(pair(age_s=600, vol5m=160_000)))
        self.assertIn("VOL_SPIKE", s.signals(pair(age_s=7200, vol5m=26_000)))   # >= 1 h: 26,000 > 5 x 5,000

    def test_wallet_signals_and_unknown(self):
        now = 1_000_000.0
        ws = v6.WalletSignals([{"address": a, "kind": "smart"} for a in ("w1", "w2", "w3")] + [{"address": "k1", "kind": "kol"}])
        ev = lambda w, t: WalletBuy(w, "", "src", "sig", "M", 1.0, 0.1, t, t, 0)
        for w in ("w1", "w2"):
            ws.add(ev(w, now - 60))
        self.assertEqual(ws.hook("M", now), (2, False))
        ws.add(ev("w3", now - 590)); ws.add(ev("k1", now - 30))
        self.assertEqual(ws.hook("M", now), (3, True))
        self.assertEqual(ws.hook("M", now + 600)[0], 0)                          # outside the 10-min window
        s = v6.V6Scanner(Config())
        self.assertIn("SMART_MONEY", s.signals(pair(smart_buyers_10m=3)))
        empty = v6.WalletSignals([])
        self.assertIn("LISTA LIPSA", empty.status)
        self.assertEqual(empty.hook("M", now), (0, False))                        # UNKNOWN never becomes a signal


class Exits(unittest.TestCase):
    def test_dev_sell_and_lp_pull_triggers(self):
        bal = {"v": 1000.0}
        rpc = lambda m, p: {"value": [{"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": bal["v"]}}}}}}]}
        w = v6.DevLpWatch(rpc)
        p = pair(creator="DEV")
        w.on_entry(p)
        self.assertFalse(w.apply(pair(creator="DEV")).dev_sold)
        bal["v"] = 400.0
        self.assertTrue(w.apply(pair(creator="DEV")).dev_sold)
        self.assertTrue(w.apply(pair(creator="DEV", liquidity=20_000)).lp_pulled)
        def broken(m, p): raise OSError("rpc down")
        w2 = v6.DevLpWatch(broken); w2.on_entry(p)
        self.assertFalse(w2.apply(pair(creator="DEV")).dev_sold)                  # unknown is not a trigger

    def test_pullback(self):
        self.assertAlmostEqual(v6.pullback_from_candles(0.8, [[0, 1, 1.0, 0.5, 0.9, 1]]), 0.2)
        self.assertIsNone(v6.pullback_from_candles(0.8, []))


class Session(unittest.TestCase):
    def test_session_and_drawdown_pause(self):
        self.assertEqual(v6.session_of(datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)), "2026-11-20")
        self.assertIsNone(v6.session_of(datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)))
        log = Logger("/tmp/v6_session_test.log")
        t0 = datetime(2026, 11, 20, 21, 30, tzinfo=timezone.utc)
        e = v6.SessionEquity(70, t0, log)
        e.check_drawdown(t0, 100, 0.30)
        e.check_drawdown(t0, 69, 0.30)
        self.assertTrue(e.paused)
        e.check_drawdown(datetime(2026, 11, 21, 21, 5, tzinfo=timezone.utc), 69, 0.30)   # next session
        self.assertFalse(e.paused)

    def test_recap(self):
        t = lambda s, pnl: Trade(s, datetime(2026, 1, 1), datetime(2026, 1, 1), 10, 10 + pnl, [])
        r = v6.recap([t("A", 5), t("B", -2), t("A", 1)], 70, 74)
        self.assertEqual((r["trades"], r["best"][0], r["worst"][0], r["top3"][0][0]), (3, "A", "B", "A"))
        self.assertEqual(v6.recap([], 70, 70)["trades"], 0)


if __name__ == "__main__":
    unittest.main()
