import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dexbot.breakout import RULES, Obs, RecordedQuotes, backtest, signals

T0 = datetime(2026, 11, 18, 22, 0, tzinfo=timezone.utc)
LIQ = 50_000


def series(prices, vols, mint="M", liq=LIQ):
    return [Obs(T0 + timedelta(seconds=30 * i), mint, p, v, liq) for i, (p, v) in enumerate(zip(prices, vols))]


BASE_P, BASE_V = [1.0] * 20, [30_000] * 20


class Signal(unittest.TestCase):
    def test_immediate_needs_price_and_volume(self):
        self.assertEqual(len(signals(series(BASE_P + [1.01], BASE_V + [70_000]), "immediate")), 1)
        self.assertEqual(signals(series(BASE_P + [1.01], BASE_V + [50_000]), "immediate"), [])      # volume 1.67x
        self.assertEqual(signals(series(BASE_P + [1.004], BASE_V + [70_000]), "immediate"), [])     # < +0.5 %

    def test_needs_20_prior_and_universe(self):
        self.assertEqual(signals(series([1.0] * 19 + [1.5], [30_000] * 19 + [90_000]), "immediate"), [])
        self.assertEqual(signals(series(BASE_P + [1.01], BASE_V + [70_000], liq=39_000), "immediate"), [])

    def test_no_lookahead(self):
        obs = series(BASE_P + [1.01, 5.0, 0.1], BASE_V + [70_000, 70_000, 70_000])
        a = signals(obs, "immediate")
        b = signals(obs[:21], "immediate")
        self.assertEqual(a[0], b[0])                                  # future rows do not change the first decision

    def test_retest_touch_then_rebreak(self):
        p = BASE_P + [1.02, 1.003, 1.01]
        v = BASE_V + [70_000, 30_000, 30_000]
        d = signals(series(p, v), "retest")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["ts"], T0 + timedelta(seconds=30 * 22))
        self.assertEqual(d[0]["level"], 1.0)

    def test_retest_invalid_below_floor_and_window(self):
        self.assertEqual(signals(series(BASE_P + [1.02, 0.97, 1.01], BASE_V + [70_000, 30_000, 30_000]), "retest"), [])
        p = BASE_P + [1.02] + [1.03] * 10 + [1.0, 1.01]               # touch only after 10 observations
        self.assertEqual(signals(series(p, BASE_V + [70_000] + [30_000] * 12), "retest"), [])


def quotes(buys=(), sells=()):
    b, s = defaultdict(list), defaultdict(list)
    for x in buys:
        b[x[0]].append(x[1:])
    for x in sells:
        s[x[0]].append(x[1:])
    return RecordedQuotes(b, s)


class Backtest(unittest.TestCase):
    def setUp(self):
        self.obs = series(BASE_P + [1.01, 1.0, 0.85, 0.85], BASE_V + [70_000] * 4)
        self.dec = signals(self.obs, "immediate")
        self.t = lambda i: T0 + timedelta(seconds=30 * i)

    def test_no_buy_quote_no_entry(self):
        r = backtest(self.obs, self.dec, quotes(), 0)
        self.assertEqual((r["entries"], r["closed"], r["net"]), (0, 0, None))
        self.assertEqual(len(r["skipped"]), 1)

    def test_latency_uses_quote_at_or_after_decision_plus_latency(self):
        q = quotes(buys=[("M", self.t(20), 14.0, 13.98)])            # at decision only
        self.assertEqual(backtest(self.obs, self.dec, q, 0)["entries"], 1)
        self.assertEqual(backtest(self.obs, self.dec, q, 30)["entries"], 0)
        q2 = quotes(buys=[("M", self.t(20) + timedelta(seconds=95), 14.0, 13.98)])  # > 60 s after decision + 30
        self.assertEqual(backtest(self.obs, self.dec, q2, 30)["entries"], 0)

    def test_stop_filled_only_by_sell_quote_for_exact_amount(self):
        tokens = 13.98                                                  # entry px = (14 - 0.02) / 13.98 = 1.0
        q = quotes(buys=[("M", self.t(20), 14.0, tokens)], sells=[("M", self.t(22), tokens, 11.5, "PASS")])
        r = backtest(self.obs, self.dec, q, 0)
        self.assertEqual(r["closed"], 1)
        self.assertAlmostEqual(r["net"], 11.5 - 0.02 - 14.0)
        q_wrong = quotes(buys=[("M", self.t(20), 14.0, tokens)], sells=[("M", self.t(22), tokens / 2, 11.5, "PASS")])
        r2 = backtest(self.obs, self.dec, q_wrong, 0)
        self.assertEqual((r2["closed"], r2["open"], r2["net"]), (0, 1, None))   # no candle/price fill invented
        self.assertGreater(r2["pending_exit_attempts"], 0)

    def test_failed_sell_is_imputed_loss(self):
        q = quotes(buys=[("M", self.t(20), 14.0, 13.98)], sells=[("M", self.t(22), 13.98, None, "FAIL")])
        r = backtest(self.obs, self.dec, q, 0)
        self.assertEqual((r["closed"], r["failed_exits"]), (1, 1))
        self.assertAlmostEqual(r["imputed"], 14.0)
        self.assertAlmostEqual(r["net"], -14.0)

    def test_take_profit_partial(self):
        obs = series(BASE_P + [1.01, 2.1, 0.85], BASE_V + [70_000] * 3)
        dec = signals(obs, "immediate")
        q = quotes(buys=[("M", self.t(20), 14.0, 13.98)],
                   sells=[("M", self.t(21), 13.98 * 0.4, 11.0, "PASS"), ("M", self.t(22), 13.98 * 0.6, 7.0, "PASS")])
        r = backtest(obs, dec, q, 0)
        self.assertEqual(r["closed"], 1)
        self.assertAlmostEqual(r["net"], 11.0 + 7.0 - 0.04 - 14.0)

    def test_rules_fixed(self):
        self.assertEqual((RULES["size_usd"], RULES["net_fee_usd"], RULES["hard_stop"], RULES["time_stop_s"]),
                         (14.0, 0.02, 0.12, 1500))


if __name__ == "__main__":
    unittest.main()
