import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("v5_gate", os.path.join(ROOT, "scripts", "v5_gate.py"))
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


def setup(d, days, n_mom, pnl_mom, n_base=0, pnl_base=0.0, multi=0, start=T0):
    os.makedirs(os.path.join(d, "logs", "runs"), exist_ok=True); os.makedirs(os.path.join(d, "state"), exist_ok=True)
    for i in range(days):                                     # one 23 h run per day
        a = start + timedelta(days=i)
        draws = [{"ts": (a + timedelta(hours=1)).replace(tzinfo=None).isoformat(), "eligible_options": 2}] * (multi // max(days, 1))
        json.dump({"started_utc": a.isoformat(), "ended_utc": (a + timedelta(hours=23)).isoformat(), "baseline_draws": draws},
                  open(os.path.join(d, "logs", "runs", f"{G.V5}_{i}.json"), "w"))
    def tr(n, pnl):
        return [{"cost": 50.0, "proceeds": 50.0 + pnl[k % len(pnl)], "closed": (start + timedelta(hours=2 + k * (days * 24 - 4) / max(n, 1))).replace(tzinfo=None).isoformat()}
                for k in range(n)]
    json.dump({"trades": tr(n_mom, pnl_mom)}, open(os.path.join(d, "state", f"paper_state_{G.V5}.json"), "w"))
    json.dump({"trades": tr(n_base, [pnl_base])}, open(os.path.join(d, "state", f"paper_state_{G.V5B}.json"), "w"))


class T(unittest.TestCase):
    def run_gate(self, d, now):
        return G.status(os.path.join(d, "state"), os.path.join(d, "logs"), now)

    def test_no_runs_collect(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_gate(d, T0)["action"], "COLLECT")

    def test_insufficient_after_14_days(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 14, 3, [5.0])
            s = self.run_gate(d, T0 + timedelta(days=14, hours=1))
            self.assertEqual(s["action"], "STOP"); self.assertIn("INSUFICIENT", s["verdict"])
            self.assertEqual(self.run_gate(d, T0 + timedelta(days=15))["action"], "STOP")      # stays stopped

    def test_validation_fail_stops(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 7, 30, [-2.0, 1.0], n_base=30, pnl_base=0.0, multi=21)
            s = self.run_gate(d, T0 + timedelta(days=7))
            self.assertEqual((s["action"], s["verdict"]), ("STOP", "FARA AVANTAJ IN TEST"))

    def test_pass_then_replication_needs_new_data(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 7, 30, [3.0, 2.0, -1.0], n_base=30, pnl_base=-0.5, multi=21)
            s = self.run_gate(d, T0 + timedelta(days=7))
            self.assertEqual(s["action"], "COLLECT"); self.assertIn("replication starts", s["verdict"])
            s2 = self.run_gate(d, T0 + timedelta(days=7, hours=1))                    # validation trades do not count again
            self.assertEqual((s2["phase"], s2["closed"]), ("REPLICATION", 0))

    def test_single_big_winner_fails(self):
        ok, checks, _ = G.criterion([{"cost": 50, "proceeds": 150, "closed": "2026-12-01T01:00:00"}] +
                                    [{"cost": 50, "proceeds": 49, "closed": "2026-12-01T02:00:00"}] * 29,
                                    [{"cost": 50, "proceeds": 40, "closed": "x"}] * 30, 25)
        self.assertFalse(ok); self.assertFalse(checks["no_trade_gt_30pct_of_net"]); self.assertFalse(checks["net_without_best_gt_0"])


if __name__ == "__main__":
    unittest.main()


class PreregConformance(unittest.TestCase):
    def test_drawdown_is_measured_against_the_250_start(self):
        # peak 350 then down to 270: 80 $ below peak (> 75 $ = 30 % of start) -> fails, although only 23 % of the peak
        mom = [{"cost": 50, "proceeds": 150, "closed": "2026-12-01T01:00:00"}, {"cost": 50, "proceeds": 50 - 80, "closed": "2026-12-01T02:00:00"}]
        ok, checks, nums = G.criterion(mom, [], 0)
        self.assertFalse(checks["max_drawdown_lt_30pct_of_start"]); self.assertEqual(nums["max_drawdown_usd"], 80)

    def test_netestabil_baseline_is_not_success_and_labelled(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 7, 30, [3.0, 2.0, 1.0], n_base=5, pnl_base=-1.0, multi=21)       # baseline < 30 closed
            s = G.status(os.path.join(d, "state"), os.path.join(d, "logs"), T0 + timedelta(days=7))
            self.assertEqual(s["action"], "STOP"); self.assertIn("NETESTABIL", s["verdict"])
            self.assertFalse(s["checks"]["baseline_testable"])

    def test_mixed_rule_sets_invalidate_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, 2, 1, [1.0])
            for i, p in enumerate(sorted(os.listdir(os.path.join(d, "logs", "runs")))):
                f = os.path.join(d, "logs", "runs", p); m = json.load(open(f)); m["fingerprint"] = f"fp{i}"; json.dump(m, open(f, "w"))
            s = G.status(os.path.join(d, "state"), os.path.join(d, "logs"), T0 + timedelta(days=2))
            self.assertEqual(s["action"], "STOP"); self.assertIn("INVALID", s["verdict"])
