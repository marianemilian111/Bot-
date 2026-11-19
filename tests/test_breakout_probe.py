import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import breakout_probe  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


@dataclass
class P:
    addr: str
    price: float
    vol5m: float
    liquidity: Optional[float]
    fetched_at: float
    sell_holders: Optional[list] = None


class FakeFeed:
    """Mint A: flat 20 cycles then breakout with volume, then falls (hard stop). Mint B: never reaches the thresholds."""

    def __init__(self, clock, prices):
        self.clock, self.prices, self.i, self.cache, self.pinned = clock, prices, 0, {}, set()
        self.step_status = {"chunks": 1, "failed": 0}

    def step(self, now):
        px = self.prices[min(self.i, len(self.prices) - 1)]
        vol = 70_000 if self.i >= 20 else 30_000
        ts = self.clock().timestamp()
        self.cache = {"A": P("A", px, vol, 50_000, ts, [("holder", 10 ** 15)]), "B": P("B", 1.0, 1_000, 5_000, ts)}
        self.i += 1

    def snapshot(self):
        return list(self.cache.values())

    def get(self, a):
        return self.cache.get(a)

    def pin(self, a):
        self.pinned.add(a)


def run_probe(prices, buy_ok=True, sell_status="OK", max_buy=60, max_sell=120, minutes=60):
    clk = Clock()
    calls = {"buy": [], "sell": []}

    def buy_fn(mint, usd):
        calls["buy"].append((clk(), mint, usd))
        return {"status": "OK", "tokens": 13.98} if buy_ok else {"status": "FAIL", "stage": "BUY_QUOTE", "detail": "no route"}

    def sell_fn(mint, holders, tokens):
        calls["sell"].append((clk(), mint, tokens, holders))
        if sell_status == "OK":
            return {"status": "OK", "quote_out_lamports": 10 ** 9, "sol_usd": 10.0, "proceeds_usd": 9.98}
        return {"status": sell_status, "stage": "simulateTransaction", "detail": "x"}

    d = tempfile.mkdtemp()
    meta = breakout_probe.run(d, minutes, max_buy, max_sell, 60, FakeFeed(clk, prices), buy_fn, sell_fn,
                              clock=clk, sleep=clk.sleep)
    for f in ("obs.jsonl", "quotes.jsonl"):
        if not os.path.exists(os.path.join(d, f)):
            open(os.path.join(d, f), "w").close()
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "breakout_compare.py"), "--entry-cutoff",
                          meta["entry_cutoff_utc"], "--quotes", os.path.join(d, "quotes.jsonl"), os.path.join(d, "obs.jsonl")],
                         capture_output=True, text=True)
    return meta, json.loads(out.stdout), calls, d


FLAT, UP, DOWN = [1.0] * 20, [1.02], [0.85] * 5


class Probe(unittest.TestCase):
    def test_closed_positions_and_replay_matches_live(self):
        meta, res, calls, d = run_probe(FLAT + UP + DOWN)
        self.assertTrue(res["verdict"].startswith("POZITII INCHISE"))
        imm = {r["latency_s"]: r for r in res["results"] if r["variant"] == "immediate"}
        self.assertEqual(imm[0]["closed"], 1)
        self.assertAlmostEqual(imm[0]["net_pnl"], 10.0 - 0.02 - 14.0, places=6)
        for k, live in meta["live_engine"].items():                     # offline replay == live engine
            v, lat = k.split("/")
            rep = next(r for r in res["results"] if r["variant"] == v and r["latency_s"] == int(lat))
            self.assertEqual((live["closed"], live["open"], live["entries"]), (rep["closed"], rep["open"], rep["entries"]))
        self.assertTrue(all(c[1] == "A" and c[2] == 14.0 for c in calls["buy"]))      # only for the signalled pool
        self.assertTrue(all(abs(c[2] - 13.98) < 1e-9 for c in calls["sell"]))        # exact held amount
        self.assertLessEqual(len(calls["buy"]), 3)                                   # t+0, t+30, t+60 at most

    def test_zero_signals_no_quote_requests(self):
        meta, res, calls, _ = run_probe(FLAT * 3)
        self.assertEqual(res["verdict"], "ZERO SEMNALE ELIGIBILE")
        self.assertEqual((calls["buy"], calls["sell"], meta["tracked"]), ([], [], 1))  # B never tracked

    def test_signals_without_quotes(self):
        meta, res, calls, _ = run_probe(FLAT + UP + DOWN, buy_ok=False)
        self.assertTrue(res["verdict"].startswith("SEMNALE, DAR FARA COTATII"))
        self.assertTrue(all(r["net_pnl"] == "N/A" for r in res["results"]))

    def test_unknown_sell_keeps_position_open_and_caps_hold(self):
        meta, res, calls, _ = run_probe(FLAT + UP + DOWN, sell_status="UNKNOWN", max_sell=4)
        self.assertLessEqual(len(calls["sell"]), 4)
        self.assertTrue(all(r["closed"] == 0 for r in res["results"]))
        self.assertTrue(any(r["open"] for r in res["results"]))

    def test_failed_sell_is_imputed(self):
        meta, res, calls, _ = run_probe(FLAT + UP + DOWN, sell_status="FAIL")
        imm0 = next(r for r in res["results"] if r["variant"] == "immediate" and r["latency_s"] == 0)
        self.assertEqual((imm0["closed"], imm0["failed_exits"], imm0["net_pnl"]), (1, 1, -14.0))

    def test_buy_cap(self):
        meta, res, calls, _ = run_probe(FLAT + UP + DOWN, max_buy=1)
        self.assertEqual(len(calls["buy"]), 1)

    def test_no_entry_after_cutoff(self):
        meta, res, calls, _ = run_probe(FLAT + UP + DOWN, minutes=20)          # whole run is after the cutoff
        self.assertEqual(calls["buy"], [])
        self.assertTrue(any(r["signals_after_entry_cutoff"] for r in res["results"]))


if __name__ == "__main__":
    unittest.main()
