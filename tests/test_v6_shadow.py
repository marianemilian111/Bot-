import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("v6", os.path.join(ROOT, "scripts", "v6_shadow.py"))
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build(d, trades, enters, dex):
    os.makedirs(os.path.join(d, "state")); os.makedirs(os.path.join(d, "logs", "runs"))
    json.dump({"trades": trades}, open(os.path.join(d, "state", f"paper_state_{S.V5}.json"), "w"))
    json.dump({"trades": []}, open(os.path.join(d, "state", f"paper_state_{S.V5B}.json"), "w"))
    with open(os.path.join(d, "logs", f"night_{S.V5}_x.log"), "w") as f:
        for ts, mint, sig in enters:
            f.write(f"{ts} ENTER    T mint={mint} part 1/2 size=$25.00 price_after_detection=1 quote_fill=1 tokens=1 delay=1s net_fee=$0.02 signals={sig}\n")
    with open(os.path.join(d, "logs", "v3_collect_x.jsonl"), "w") as f:
        for m, x in dex.items():
            f.write(json.dumps({"variant": S.V5, "mint": m, "dex": x}) + "\n")


def tr(mint, opened, closed, proceeds, written_off=0.0, reasons=("HARD_STOP",)):
    return {"addr": mint, "symbol": mint, "opened": opened, "closed": closed, "cost": 50.0, "proceeds": proceeds,
            "written_off": written_off, "reasons": list(reasons)}


class T(unittest.TestCase):
    def test_session_boundaries_bucharest(self):
        # November: Bucharest = UTC+2
        self.assertIsNone(S.session_of(utc("2026-11-20T20:59:00")))           # 22:59 local
        self.assertEqual(S.session_of(utc("2026-11-20T21:00:00")), "2026-11-20")   # 23:00 local
        self.assertEqual(S.session_of(utc("2026-11-21T04:59:00")), "2026-11-20")   # 06:59 local
        self.assertIsNone(S.session_of(utc("2026-11-21T05:00:00")))           # 07:00 local

    def test_selection_and_sizing(self):
        with tempfile.TemporaryDirectory() as d:
            trades = [tr("A", "2026-11-20T21:10:00", "2026-11-20T21:30:00", 40.0),     # loss, 2 signals, in session
                      tr("B", "2026-11-20T21:40:00", "2026-11-20T22:00:00", 45.0),     # loss
                      tr("C", "2026-11-20T22:10:00", "2026-11-20T22:30:00", 75.0),     # win, 3 signals, after 2 losses -> halved
                      tr("D", "2026-11-20T22:40:00", "2026-11-20T23:00:00", 60.0),     # 1 signal -> excluded
                      tr("E", "2026-11-20T12:00:00", "2026-11-20T12:20:00", 60.0),     # outside session
                      tr("F", "2026-11-20T23:10:00", "2026-11-20T23:30:00", 0.0, 50.0, ["EXIT_FAILED"]),  # orca -> excluded
                      tr("G", "2026-11-20T23:40:00", "2026-11-21T00:00:00", 0.0, 50.0, ["EXIT_FAILED"])]  # failed exit
            enters = [("2026-11-20 21:10:00", "A", "VOL_SPIKE+BUY_RATIO"), ("2026-11-20 21:40:00", "B", "VOL_SPIKE+BUY_RATIO"),
                      ("2026-11-20 22:10:00", "C", "VOL_SPIKE+BUY_RATIO+HOLDER_GROWTH"), ("2026-11-20 22:40:00", "D", "BUY_RATIO"),
                      ("2026-11-20 12:00:00", "E", "VOL_SPIKE+BUY_RATIO"), ("2026-11-20 23:10:00", "F", "VOL_SPIKE+BUY_RATIO"),
                      ("2026-11-20 23:40:00", "G", "VOL_SPIKE+BUY_RATIO")]
            build(d, trades, enters, {"A": "pump.fun", "B": "raydium", "C": "meteora", "D": "pump.fun", "E": "pump.fun", "F": "orca", "G": "pump.fun"})
            r = S.replay(d)
            self.assertEqual([t["mint"] for t in r["trades"]], ["A", "B", "C", "G"])
            self.assertEqual(r["excluded"]["<2 signals"], 1); self.assertEqual(r["excluded"]["outside session"], 1)
            self.assertEqual(r["excluded"]["dex not allowed"], 1)
            a, b, c, g = r["trades"]
            self.assertEqual(a["size"], 14.0)                                       # 20 % of 70
            self.assertAlmostEqual(c["size"], round((70 + a["pnl"] + b["pnl"]) * 0.30 * 0.5, 2))   # 30 % halved after 2 losses
            self.assertLess(a["pnl"], 0); self.assertGreater(c["pnl"], 0)
            self.assertAlmostEqual(g["proceeds"], 0.0); self.assertAlmostEqual(g["imputed"], g["size"])   # failed exit -> imputed
            self.assertIn("PnL", S.report(r))

    def test_no_trades_is_na(self):
        with tempfile.TemporaryDirectory() as d:
            build(d, [], [], {})
            self.assertIn("PnL: N/A", S.report(S.replay(d)))


if __name__ == "__main__":
    unittest.main()
