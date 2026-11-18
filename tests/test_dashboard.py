import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*args, cwd=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "dashboard.py"), *args],
                          cwd=cwd, capture_output=True, text=True).stdout


class T(unittest.TestCase):
    def test_no_state_is_na(self):
        with tempfile.TemporaryDirectory() as d:
            out = run(cwd=d)
        self.assertIn("PnL estimat: N/A", out)
        self.assertNotIn("DEMO", out)

    def test_demo_is_stamped_and_ignores_real_files(self):
        out = run("--demo")
        self.assertIn("DEMO - SYNTHETIC NUMBERS", out)
        self.assertGreaterEqual(out.count("(DEMO)"), 5)

    def test_zero_closed_trades_na_and_symbol_sanitised(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "state"))
            json.dump({"start": 1000.0, "cash": 900.0, "wins": 0, "losses": 0, "loss_streak": 0, "peak": 1000.0,
                       "paused": False, "curve": [["2026-01-01T00:00:00", 1000.0]], "trades": [], "failed_exits": 0,
                       "positions": {"a": {"symbol": "EV|IL`<x>", "opened": "2026-01-01T00:00:00", "cost": 100.0,
                                           "tokens": 10.0, "tokens_bought": 10.0, "last_price": 11.0, "stage": 0,
                                           "parts_done": 1}}}, open(os.path.join(d, "state", "paper_state.json"), "w"))
            out = run(cwd=d)
        self.assertIn("0 tranzactii inchise - PnL estimat: N/A", out)
        self.assertIn("| EVILx |", out)
        self.assertNotIn("`<", out)


if __name__ == "__main__":
    unittest.main()
