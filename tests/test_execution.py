"""Execution path without money: stubs only. FakeChain stands in for the network; nothing is signed or sent."""
import os
import tempfile
import time
import unittest

from dexbot.execution import (ExecutionEngine, Journal, Limits, LiveBroker, RiskGuard, RpcError, QUOTE_TTL_S, coid_for)

DAY = "2026-12-01"


class FakeChain:
    """Test double for the network: records submissions, confirms them, tracks balances."""

    def __init__(self):
        self.sent, self.bal, self.pending, self.fail_status, self.lost = [], {}, {}, 0, set()

    def submit(self, o, q):
        self.sent.append(o.coid)
        sig = f"sig{len(self.sent)}"
        raw = q["out_raw"] if o.side == "BUY" else -o.amount_raw
        self.pending[sig] = (o.mint, raw, o.coid)
        return sig

    def status(self, sig):
        if self.fail_status:
            self.fail_status -= 1
            raise RpcError("node timeout")
        mint, raw, _ = self.pending[sig]
        if sig not in self.lost:
            self.bal[mint] = self.bal.get(mint, 0) + raw
            self.lost.add(sig)
        return {"ok": True, "filled_raw": abs(raw), "proceeds_lamports": 123 if raw < 0 else None}

    def find_by_coid(self, coid):
        return next((s for s, v in self.pending.items() if v[2] == coid), None)

    def balance_raw(self, mint):
        return self.bal.get(mint, 0)


def mk(tmp, broker=None, quote=None, sim=None, limits=None, clock=time.time):
    q = quote or (lambda o: {"t": clock(), "out_raw": 1_000_000, "impact": 0.01})
    s = sim or (lambda o, q: {"ok": True})
    return ExecutionEngine(Journal(os.path.join(tmp, "orders.jsonl")), RiskGuard(limits or Limits()), q, s, broker or FakeChain(), clock)


class T(unittest.TestCase):
    def test_signal_quote_sim_submit_confirm_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            o = e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            self.assertEqual((o.state, o.filled_raw), ("CONFIRMED", 1_000_000))       # fill comes from the confirmation
            self.assertTrue(any("simulation ok (not an execution)" in h for h in o.history))
            self.assertEqual(e.recover()["ok"], True)

    def test_live_broker_never_sends(self):
        with tempfile.TemporaryDirectory() as d:
            e = mk(d, LiveBroker())
            o = e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            self.assertEqual((o.state, o.filled_raw, e.position_raw("MINT")), ("DRY_RUN_STOP", None, 0))

    def test_duplicate_signal_sends_once(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("sig-A", "t1", "MINT", 25, 5, DAY); e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            self.assertEqual(len(ch.sent), 1)

    def test_no_sell_route_keeps_position_open(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            e.quote_fn = lambda o: None
            s = e.sell("sig-A", "exit", "MINT", 1_000_000)
            self.assertEqual((s.state, s.reason, e.position_raw("MINT")), ("BLOCKED", "EXIT_NO_ROUTE", 1_000_000))

    def test_expired_quote_requotes_once_then_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            now = [1000.0]
            seq = iter([{"t": now[0] - QUOTE_TTL_S - 1, "out_raw": 1, "impact": 0.0}, {"t": now[0], "out_raw": 7, "impact": 0.0}])
            e = mk(d, FakeChain(), quote=lambda o: next(seq), clock=lambda: now[0])
            self.assertEqual(e.buy("sig-A", "t1", "M", 25, 5, DAY).filled_raw, 7)      # re-quoted, fresh fill
            e.quote_fn = lambda o: {"t": now[0] - 99, "out_raw": 1, "impact": 0.0}
            o = e.buy("sig-B", "t1", "M2", 25, 5, DAY)
            self.assertEqual((o.state, o.reason), ("BLOCKED", "quote expired twice"))

    def test_rpc_error_blocks_new_entries_but_not_exits(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            ch.fail_status = 99
            o = e.buy("sig-B", "t1", "M2", 25, 5, DAY)                                # confirm fails 3x -> state error
            self.assertEqual(o.state, "SUBMITTED"); self.assertIsNotNone(e.g.state_error)
            blocked = e.buy("sig-C", "t1", "M3", 25, 5, DAY)
            self.assertTrue(blocked.reason.startswith("STATE_ERROR"))
            ch.fail_status = 0
            self.assertEqual(e.sell("sig-A", "exit", "MINT", 1_000_000).state, "CONFIRMED")   # exits still allowed

    def test_restart_with_open_position_and_unknown_orders(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("sig-A", "t1", "MINT", 25, 5, DAY)                                   # confirmed, open position
            ch.fail_status = 99
            e.buy("sig-B", "t1", "M2", 25, 5, DAY)                                     # submitted, outcome unknown
            ch.fail_status = 0
            e2 = mk(d, ch)                                                             # "restart": new engine, same journal
            self.assertEqual(e2.position_raw("MINT"), 1_000_000)
            r = e2.recover()
            self.assertEqual(e2.j.orders[coid_for("sig-B", "t1")].state, "CONFIRMED")
            self.assertTrue(r["ok"]); self.assertEqual(len(ch.sent), 2)                # nothing resent
            e2.buy("sig-B", "t1", "M2", 25, 5, DAY)
            self.assertEqual(len(ch.sent), 2)                                          # duplicate protection survives restart

    def test_crash_between_journal_and_submit_is_not_resent(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            from dexbot.execution import Order
            o = Order(coid_for("sig-Z", "t1"), "BUY", "MZ", usd=25); o.state = "SUBMITTING"; e.j.write(o, "crash simulated")
            e2 = mk(d, ch); e2.recover()
            self.assertEqual((e2.j.orders[o.coid].state, len(ch.sent)), ("FAILED", 0))

    def test_balance_mismatch_blocks_entries(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("sig-A", "t1", "MINT", 25, 5, DAY)
            ch.bal["MINT"] = 5                                                         # chain disagrees with the journal
            self.assertFalse(e.reconcile()["ok"])
            self.assertTrue(e.buy("sig-B", "t1", "M2", 25, 5, DAY).reason.startswith("STATE_ERROR"))


class Risk(unittest.TestCase):
    def test_limits_block_entries(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch, limits=Limits(max_total_exposure_usd=60, max_position_usd=50, max_daily_loss_usd=20))
            self.assertTrue(e.buy("s1", "t1", "A", 60, 5, DAY).reason.startswith("POSITION_LIMIT"))
            e.buy("s2", "t1", "A", 25, 5, DAY); e.buy("s3", "t1", "B", 25, 5, DAY)
            self.assertTrue(e.buy("s4", "t1", "C", 25, 5, DAY).reason.startswith("EXPOSURE_LIMIT"))     # 75 > 60
            self.assertTrue(e.buy("s5", "t1", "C", 5, 500, DAY).reason.startswith("STALE_DATA"))
            e.quote_fn = lambda o: {"t": time.time(), "out_raw": 1, "impact": 0.05}
            self.assertTrue(e.buy("s6", "t1", "C", 5, 5, DAY).reason.startswith("SLIPPAGE_LIMIT"))

    def test_daily_loss_stops_entries_without_liquidating(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch, limits=Limits(max_daily_loss_usd=20))
            e.buy("s1", "t1", "A", 25, 5, DAY)
            e.g.record_loss(DAY, 21)                                                   # realised + imputed losses today
            self.assertTrue(e.buy("s2", "t1", "B", 25, 5, DAY).reason.startswith("DAILY_LOSS_LIMIT"))
            self.assertEqual(e.position_raw("A"), 1_000_000)                           # open position NOT liquidated
            self.assertFalse(any(o.side == "SELL" for o in e.j.orders.values()))       # the guard created no sale
            self.assertEqual(e.sell("s1", "exit", "A", 1_000_000).state, "CONFIRMED")  # exit rules still run
            self.assertEqual(e.buy("s3", "t1", "B", 25, 5, "2026-12-02").state, "CONFIRMED")  # new UTC day: entries allowed again

    def test_exposure_falls_after_a_confirmed_sale(self):
        with tempfile.TemporaryDirectory() as d:
            ch = FakeChain(); e = mk(d, ch)
            e.buy("s1", "t1", "A", 25, 5, DAY)
            self.assertAlmostEqual(e.exposure_usd(), 25)
            e.sell("s1", "exit", "A", 400_000)
            self.assertAlmostEqual(e.exposure_usd(), 15)


if __name__ == "__main__":
    unittest.main()
