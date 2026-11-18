import json
import os
import subprocess
import sys
import tempfile
import unittest

from dexbot.frozen import fingerprint
from dexbot.variants import FROZEN, V1, V2, make

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compare(cwd):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "compare.py")], cwd=cwd,
                          capture_output=True, text=True)
    return r.stdout or r.stderr


class T(unittest.TestCase):
    def test_variants_pinned_and_v1_untouched(self):
        for v in (V1, V2):
            self.assertEqual(fingerprint(make(v), v), FROZEN[v])
        c1, c2 = make(V1), make(V2)
        self.assertEqual((c1.min_liq, c1.min_vol5m, c1.min_signals), (40_000, 25_000, 2))
        self.assertEqual((c2.min_liq, c2.min_vol5m, c2.min_signals), (20_000, 10_000, 1))
        for f in ("tp1_mult", "tp2_mult", "hard_stop", "time_stop_s", "fee", "max_slippage", "dev_max_pct", "top10_max_pct", "trail_pct"):
            self.assertEqual(getattr(c1, f), getattr(c2, f), f)

    def test_no_run_is_na(self):
        with tempfile.TemporaryDirectory() as d:
            out = compare(d)
        self.assertIn("PnL estimat: N/A", out)

    def test_negative_pnl_shown_and_zero_closed_na(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "logs")); os.makedirs(os.path.join(d, "state"))
            json.dump({"variants": [V1, V2], "cycles": 3, "minutes": 1, "sha": "abcdef1234", "run_id": "1", "jsonl": "c.jsonl",
                       "sellsim_entry": {"OK": 0, "FAIL": 1, "UNKNOWN": 2}, "sellsim_exit": {},
                       "logs": {V1: "a.log", V2: "b.log"}}, open(os.path.join(d, "logs", "run_meta.json"), "w"))
            rows = [{"variant": V1, "mint": "m1", "verdict": "REJECT", "reason": "liquidity $5,000 < $40,000"},
                    {"variant": V2, "mint": "m1", "verdict": "SELL_SIMULATION=UNKNOWN", "reason": "SELL_SIMULATION=UNKNOWN (rejected)"}]
            open(os.path.join(d, "logs", "c.jsonl"), "w").write("\n".join(json.dumps(r) for r in rows))
            base = {"start": 1000.0, "cash": 900.0, "wins": 0, "losses": 1, "loss_streak": 1, "peak": 1000.0, "paused": False,
                    "curve": [["2026-01-01T00:00:00", 1000.0], ["2026-01-01T01:00:00", 900.0]], "positions": {}, "failed_exits": 1}
            json.dump({**base, "trades": [{"symbol": "AAA", "opened": "2026-01-01T00:00:00", "closed": "2026-01-01T00:30:00",
                                           "cost": 100.0, "proceeds": 0.0, "reasons": ["EXIT_FAILED"]}]},
                      open(os.path.join(d, "state", f"paper_state_{V1}.json"), "w"))
            json.dump({**base, "trades": [], "failed_exits": 0}, open(os.path.join(d, "state", f"paper_state_{V2}.json"), "w"))
            out = compare(d)
        self.assertIn("-100.00", out)                       # negative PnL is shown
        self.assertIn("**N/A** (0 closed)", out)            # variant without closed trades
        self.assertIn("liquidity N < N", out)               # top rejecting filter
        self.assertIn("SELL_SIMULATION=UNKNOWN", out)


if __name__ == "__main__":
    unittest.main()


class SignalGate(unittest.TestCase):
    def test_v2_needs_exactly_one_signal_v1_two(self):
        from dexbot.agents import RugCheck, Scanner
        from dexbot.alert import evaluate
        from dexbot.models import Pair
        p = Pair(addr="A", symbol="T", source="x", age_s=600, price=1, liquidity=50_000, vol5m=30_000, vol_avg5m_1h=29_000,
                 buys5m=80, sells5m=20, holders=500, holders_10m=490, price_1h_ago=1, pullback_pct=0, dev_pct=1, top10_pct=20,
                 bundled=False, lp_status="burned", mint_frozen=False, mint_auth_open=False, honeypot=None)
        self.assertEqual(Scanner(make(V1)).signals(p), ["BUY_RATIO"])            # exactly one signal
        self.assertEqual(len(Scanner(make(V2)).scan([p])), 1)                     # v2 flags it
        self.assertEqual(len(Scanner(make(V1)).scan([p])), 0)                     # v1 does not
        c2, c1 = make(V2), make(V1)
        self.assertEqual(evaluate(c2, Scanner(c2), RugCheck(c2), p, skip_sell=True)[0], "CANDIDATE")
        self.assertEqual(evaluate(c1, Scanner(c1), RugCheck(c1), p, skip_sell=True)[1], "signals 1 < 2")
