#!/usr/bin/env python3
"""Offline comparison: breakout immediate vs breakout after retest (rules: docs/BREAKOUT_RULES.md). PAPER ONLY.

Usage: python scripts/breakout_compare.py [--quotes quotes.jsonl] rows1.jsonl [rows2.jsonl ...]
Rows: any collector JSONL with ts, mint, price, vol5m, liquidity (duplicates per (mint, ts) are kept once).
Quotes (optional): JSONL records recorded at real times, never derived from rows:
  {"kind": "buy",  "ts": ..., "mint": ..., "usd_in": 14.0, "tokens_out": ...}
  {"kind": "sell", "ts": ..., "mint": ..., "amount_in": ..., "usd_out": ..., "status": "PASS" | "FAIL"}
Without quotes there are no entries and closed PnL is N/A; the missing fields are named in the output."""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.breakout import Obs, RecordedQuotes, backtest, signals  # noqa: E402

LATENCIES = (0, 30, 60)


def ts_of(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_rows(paths):
    seen, out = set(), []
    for p in paths:
        for line in open(p):
            d = json.loads(line)
            if d.get("price") in (None, 0) or d.get("vol5m") is None or not d.get("mint"):
                continue
            k = (d["mint"], d["ts"])
            if k in seen:
                continue
            seen.add(k)
            out.append(Obs(ts_of(d["ts"]), d["mint"], float(d["price"]), float(d["vol5m"]), d.get("liquidity")))
    out.sort(key=lambda o: (o.ts, o.mint))
    return out


def load_quotes(path):
    buys, sells = defaultdict(list), defaultdict(list)
    if path:
        for line in open(path):
            q = json.loads(line)
            if q["kind"] == "buy":
                buys[q["mint"]].append((ts_of(q["ts"]), float(q["usd_in"]), float(q["tokens_out"])))
            elif q["kind"] == "sell":
                sells[q["mint"]].append((ts_of(q["ts"]), float(q["amount_in"]),
                                         None if q.get("usd_out") is None else float(q["usd_out"]), q.get("status", "PASS")))
    return RecordedQuotes(buys, sells)


def compare(obs, quotes):
    res = []
    for variant in ("immediate", "retest"):
        dec = signals(obs, variant)
        for lat in LATENCIES:
            r = backtest(obs, dec, quotes, lat)
            res.append({"variant": variant, "latency_s": lat, "signals": len(dec), "entries": r["entries"],
                        "closed": r["closed"], "net_pnl": "N/A" if r["net"] is None else round(r["net"], 4),
                        "net_without_best": "N/A" if r["net_without_best"] is None else round(r["net_without_best"], 4),
                        "open": r["open"], "failed_exits": r["failed_exits"], "imputed": round(r["imputed"], 4),
                        "exit_attempts_without_quote": r["pending_exit_attempts"],
                        "entries_skipped_no_buy_quote": len(r["skipped"])})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes")
    ap.add_argument("rows", nargs="+")
    a = ap.parse_args()
    obs = load_rows(a.rows)
    res = compare(obs, load_quotes(a.quotes))
    mints = len({o.mint for o in obs})
    print(json.dumps({"observations": len(obs), "mints": mints,
                      "span_utc": [obs[0].ts.isoformat(), obs[-1].ts.isoformat()] if obs else None,
                      "results": res,
                      "missing_fields": [] if a.quotes else [
                          "recorded BUY quote: tokens_out for usd_in=14.0 per (mint, time >= decision + latency, <= +60 s)",
                          "recorded SELL quote/simulation: usd_out + status for amount_in = exact held tokens per (mint, time >= trigger + latency, <= +60 s)"]},
                     indent=1))


if __name__ == "__main__":
    main()
