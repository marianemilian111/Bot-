"""Gap check (offline, stubs only): a submit that LANDS on chain but whose response is lost to a timeout.
Jupiter docs (Ultra execute): resubmitting the SAME signed transaction is a status poll and cannot double execute,
because the signature is the same. The signature of a signed Solana transaction is known BEFORE sending.
Desired behaviour: the journal stores signature + lastValidBlockHeight before the send; after a timeout the order is
resolved by that signature (confirmed / expired), never re-signed or resent as a new transaction while still valid."""
import os
import tempfile
import time
import unittest

from dexbot.execution import ExecutionEngine, Journal, Limits, RiskGuard, RpcError, coid_for


class LossyChain:
    """Executes the transaction, then the response is lost (timeout). No 'find by client id' exists on a real chain."""

    def __init__(self):
        self.sent, self.bal, self.landed = [], {}, {}

    def submit(self, o, q):
        sig = f"sig-{o.coid[:6]}"                      # deterministic: known before sending
        self.sent.append(sig)
        self.landed[sig] = (o.mint, q["out_raw"])
        self.bal[o.mint] = self.bal.get(o.mint, 0) + q["out_raw"]
        raise RpcError("timeout after send")              # response lost

    def status(self, sig):
        if sig in self.landed:
            return {"ok": True, "filled_raw": self.landed[sig][1]}
        return None

    def balance_raw(self, mint):
        return self.bal.get(mint, 0)


class T(unittest.TestCase):
    def mk(self, d, chain):
        return ExecutionEngine(Journal(os.path.join(d, "o.jsonl")), RiskGuard(Limits()),
                               lambda o: {"t": time.time(), "out_raw": 1_000, "impact": 0.0}, lambda o, q: {"ok": True}, chain)

    def test_no_resend_after_timeout(self):
        """Regression: before this fix submit went through the 3x RPC retry and the landed order was sent 3 times.
        Now: one send; the landed order is marked FAILED by recover() and the balance mismatch blocks entries."""
        with tempfile.TemporaryDirectory() as d:
            ch = LossyChain(); e = self.mk(d, ch)
            e.buy("s1", "t1", "M", 25, 5, "2026-12-01")
            e2 = self.mk(d, ch); r = e2.recover()
            self.assertEqual(len(ch.sent), 1)                                        # no duplicate
            self.assertEqual(e2.j.orders[coid_for("s1", "t1")].state, "FAILED")     # but the real fill is lost
            self.assertFalse(r["ok"])                                                # -> STATE_ERROR, entries blocked

    @unittest.expectedFailure
    def test_desired_resolve_by_pre_journaled_signature(self):
        """Gap: with the signature journaled before the send, recover() would confirm the fill without resending."""
        with tempfile.TemporaryDirectory() as d:
            ch = LossyChain(); e = self.mk(d, ch)
            e.buy("s1", "t1", "M", 25, 5, "2026-12-01")
            e2 = self.mk(d, ch); r = e2.recover()
            self.assertEqual(e2.j.orders[coid_for("s1", "t1")].state, "CONFIRMED")
            self.assertTrue(r["ok"]); self.assertEqual(len(ch.sent), 1)


if __name__ == "__main__":
    unittest.main()
