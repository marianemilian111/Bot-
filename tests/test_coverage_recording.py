import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from dexbot import live
from dexbot.variants import V1, make

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("aud", os.path.join(ROOT, "scripts", "v6_coverage_audit.py"))
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)


def row(a):
    return {"chainId": "solana", "pairCreatedAt": (time.time() - 7200) * 1000, "baseToken": {"address": a, "symbol": a},
            "priceUsd": "1", "liquidity": {"usd": 50000}, "volume": {"m5": 1, "h1": 12}, "txns": {"m5": {"buys": 1, "sells": 1}},
            "dexId": "pumpswap", "pairAddress": "P" + a}


class T(unittest.TestCase):
    def test_partial_chunk_failure_is_recorded(self):
        f = live.LiveFeed(make(V1)); f._discover = lambda: None
        f.watch = [f"M{i}" for i in range(45)]                     # 2 chunks
        calls = {"n": 0}
        def get(u):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("429")
            return {"pairs": [row(f"M{i}") for i in range(30)]}
        with mock.patch.object(live, "_get", get):
            f.step(datetime.now())
        self.assertEqual(f.step_status["chunks"], 2); self.assertEqual(f.step_status["failed"], 1)

    def test_audit_uses_ok_cycles_when_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "logs", "runs"))
            # session 2026-11-20 (Bucharest): 21:00-05:00 UTC; 7 h run but only 600 ok cycles of 30 s = 5 h
            cs = [[f"2026-11-20T{21 + (i // 120):02d}:{(i // 2) % 60:02d}:{30 * (i % 2):02d}+00:00", "ok" if i < 600 else "partial"] for i in range(3 * 120)]
            cs += [[f"2026-11-21T0{(i // 120)}:{(i // 2) % 60:02d}:{30 * (i % 2):02d}+00:00", "ok" if i < 0 else "failed"] for i in range(4 * 120)]
            json.dump({"run_id": "r", "started_utc": "2026-11-20T21:00:00+00:00", "ended_utc": "2026-11-21T04:00:00+00:00",
                       "cycle_status": cs, "interval_s": 30}, open(os.path.join(d, "logs", "runs", "capture-v6_r.json"), "w"))
            out = A.audit(d)
            s = out["sessions"]["2026-11-20"]
            self.assertTrue(s["gate_counts_session"])                     # gate: 7 h of run time
            self.assertFalse(s["verified_counts"])                        # verified: < 6 h of ok cycles
            self.assertIn("BLOCAT", out["status"])


if __name__ == "__main__":
    unittest.main()
