import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("v6_gate", os.path.join(ROOT, "scripts", "v6_gate.py"))
G6 = importlib.util.module_from_spec(spec); spec.loader.exec_module(G6)
S0 = datetime(2026, 12, 1, 21, 0, tzinfo=timezone.utc)          # 23:00 Bucharest


def setup(d, sessions, n, pnl, n_base=0, multi=0):
    os.makedirs(os.path.join(d, "logs", "runs")); os.makedirs(os.path.join(d, "state"))
    for i in range(sessions):
        a = S0 + timedelta(days=i)
        json.dump({"started_utc": a.isoformat(), "ended_utc": (a + timedelta(hours=8)).isoformat(), "fingerprint": "f",
                   "baseline_draws": [{"ts": (a + timedelta(hours=1)).replace(tzinfo=None).isoformat(), "eligible_options": 2}] * (multi // max(sessions, 1))},
                  open(os.path.join(d, "logs", "runs", f"capture-v6_{i}.json"), "w"))
    tr = lambda k, p: [{"cost": 14.0, "proceeds": 14.0 + p, "closed": (S0 + timedelta(hours=1 + j * sessions * 24 / max(k, 1))).replace(tzinfo=None).isoformat()} for j in range(k)]
    json.dump({"trades": tr(n, pnl)}, open(os.path.join(d, "state", "paper_state_capture-v6.json"), "w"))
    json.dump({"trades": tr(n_base, -0.1)}, open(os.path.join(d, "state", "paper_state_random-baseline-v6.json"), "w"))


class T(unittest.TestCase):
    def test_sessions_counted_and_phase_file_separate(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 7, 30, 0.5, n_base=30, multi=21)
            s = G6.status(os.path.join(d, "state"), os.path.join(d, "logs"), S0 + timedelta(days=7))
            self.assertEqual(s["days_with_12h_coverage"], 7)                 # 7 sessions with >= 6 h
            self.assertIn("replication starts", s["verdict"])
            self.assertTrue(os.path.exists(os.path.join(d, "state", "v6_phase.json")))
            self.assertFalse(os.path.exists(os.path.join(d, "state", "v5_phase.json")))

    def test_insufficient(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 14, 2, 1.0)
            s = G6.status(os.path.join(d, "state"), os.path.join(d, "logs"), S0 + timedelta(days=14, hours=1))
            self.assertIn("INSUFICIENT", s["verdict"])


if __name__ == "__main__":
    unittest.main()
