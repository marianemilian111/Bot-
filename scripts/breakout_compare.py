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
            if not line.strip():
                continue
            q = json.loads(line)
            if q["kind"] == "buy":
                buys[q["mint"]].append((ts_of(q["ts"]), float(q["usd_in"]), float(q["tokens_out"])))
            elif q["kind"] == "sell":
                sells[q["mint"]].append((ts_of(q["ts"]), float(q["amount_in"]),
                                         None if q.get("usd_out") is None else float(q["usd_out"]), q.get("status", "PASS")))
    return RecordedQuotes(buys, sells)


def compare(obs, quotes, entry_cutoff=None):
    res = []
    for variant in ("immediate", "retest"):
        dec = signals(obs, variant)
        for lat in LATENCIES:
            r = backtest(obs, dec, quotes, lat, entry_cutoff=entry_cutoff)
            res.append({"variant": variant, "latency_s": lat, "signals": len(dec), "entries": r["entries"],
                        "closed": r["closed"], "net_pnl": "N/A" if r["net"] is None else round(r["net"], 4),
                        "net_without_best": "N/A" if r["net_without_best"] is None else round(r["net_without_best"], 4),
                        "open": r["open"], "failed_exits": r["failed_exits"], "imputed": round(r["imputed"], 4),
                        "exit_attempts_without_quote": r["pending_exit_attempts"],
                        "entries_skipped_no_buy_quote": r.get("no_buy_quote", len(r["skipped"])),
                        "signals_while_position_open": r.get("busy", 0), "signals_after_entry_cutoff": r.get("window_end", 0),
                        "sell_attempts": r.get("exit_fills", 0) + r.get("failed_exits", 0) + r.get("exit_no_quote", 0),
                        "sell_known_rate": round((r.get("exit_fills", 0) + r.get("failed_exits", 0)) /
                                                 max(1, r.get("exit_fills", 0) + r.get("failed_exits", 0) + r.get("exit_no_quote", 0)), 3),
                        "metrics": metrics(r)})
    return res


# Fixed BEFORE any probe result (docs/BREAKOUT_CRITERIA.md). Not to be changed after seeing data.
CAPITAL_USD = 70.0
PROBE = {"primary_latency_s": 30, "min_entries": 2, "min_feed_ok": 0.90, "min_sell_known": 0.80}
VALIDATION = {"primary_latency_s": 30, "min_closed": 30, "min_net_usd": 7.0, "min_net_pct": 10.0, "max_best_share": 0.30,
              "min_t": 2.0, "max_drawdown_usd": 21.0, "max_failed_exit_rate": 0.05, "max_exit_no_quote_rate": 0.20,
              "min_net_at_60s": 0.0}


def metrics(r):
    """Per-scenario risk/edge metrics from closed trades (imputed losses are inside pnl)."""
    pn = [t["pnl"] for t in sorted(r.get("trades", []), key=lambda t: t["closed"])]
    n = len(pn)
    out = {"closed": n, "net": sum(pn) if n else None}
    if not n:
        return out
    net, best = sum(pn), max(pn)
    cum = peak = dd = 0.0
    for x in pn:
        cum += x; peak = max(peak, cum); dd = max(dd, peak - cum)
    mean = net / n
    sd = (sum((x - mean) ** 2 for x in pn) / (n - 1)) ** 0.5 if n > 1 else None
    exits = r.get("failed_exits", 0) + r.get("exit_fills", 0)
    trig = exits + r.get("exit_no_quote", 0)
    out.update(net_pct_capital=round(net / CAPITAL_USD * 100, 2), best=round(best, 4),
               net_without_best=round(net - best, 4), best_share=round(best / net, 3) if net > 0 else None,
               max_drawdown=round(dd, 4), t=round(mean / (sd / n ** 0.5), 2) if sd else None,
               failed_exit_rate=round(r.get("failed_exits", 0) / exits, 3) if exits else None,
               exit_no_quote_rate=round(r.get("exit_no_quote", 0) / trig, 3) if trig else None)
    return out


def evaluate_validation(by):
    """by[(variant, latency)] -> metrics. Returns per variant PASS / FAIL reasons / INSUFICIENT. Imputed losses count."""
    c, res = VALIDATION, {}
    for v in ("immediate", "retest"):
        m, m60 = by.get((v, c["primary_latency_s"]), {}), by.get((v, 60), {})
        if (m.get("closed") or 0) < c["min_closed"]:
            res[v] = f"INSUFICIENT ({m.get('closed') or 0} < {c['min_closed']} closed)"
            continue
        if m["exit_no_quote_rate"] is not None and m["exit_no_quote_rate"] > c["max_exit_no_quote_rate"]:
            res[v] = "BLOCAT (exit quotes missing too often)"
            continue
        fails = []
        if m["net"] < c["min_net_usd"] or m["net_pct_capital"] < c["min_net_pct"]: fails.append("net")
        if m["net_without_best"] <= 0: fails.append("net without best <= 0")
        if m["best_share"] is None or m["best_share"] > c["max_best_share"]: fails.append("best trade share")
        if m["t"] is None or m["t"] < c["min_t"]: fails.append("t < 2")
        if m["max_drawdown"] > c["max_drawdown_usd"]: fails.append("drawdown")
        if (m["failed_exit_rate"] or 0) > c["max_failed_exit_rate"]: fails.append("failed exits")
        if (m60.get("net") or 0) < c["min_net_at_60s"]: fails.append("net at 60 s latency")
        res[v] = "PASS" if not fails else ("RESPINSA: " if m["net"] <= 0 else "FAIL: ") + ", ".join(fails)
    return res


def evaluate_probe(res, cycles=None):
    """Probe gate (tradeability, not edge)."""
    c = PROBE
    feed_ok = (sum(1 for x in cycles if x.get("feed") == "ok") / len(cycles)) if cycles else None
    if feed_ok is not None and feed_ok < c["min_feed_ok"]:
        return f"BLOCAT: feed complet doar {feed_ok:.0%} din cicluri (< 90 %)"
    if not any(r["signals"] for r in res):
        return "STOP STRATEGIE: zero semnale eligibile cu feed complet"
    prim = [r for r in res if r["latency_s"] == c["primary_latency_s"]]
    if not any(r["entries"] >= c["min_entries"] for r in prim):
        return "BLOCAT: semnale fara suficiente intrari cotate (vezi motivele din quotes.jsonl)"
    known = [r for r in prim if r["sell_attempts"]]
    if known and min(r["sell_known_rate"] for r in known) < c["min_sell_known"]:
        return "BLOCAT: simularile de vanzare raspund UNKNOWN prea des"
    return "CONTINUA PAPER: oportunitati tranzactionabile; edge nedemonstrat (cere validare separata)"


def verdict(res):
    """Exactly one of three outcomes, from the replay of recorded files."""
    if not any(r["signals"] for r in res):
        return "ZERO SEMNALE ELIGIBILE"
    if not any(r["closed"] for r in res):
        return "SEMNALE, DAR FARA COTATII SUFICIENTE (nicio pozitie inchisa; PnL inchis N/A)"
    return "POZITII INCHISE - PnL net dupa costuri pe varianta/latenta in tabel"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes")
    ap.add_argument("--entry-cutoff")
    ap.add_argument("--cycles")
    ap.add_argument("rows", nargs="+")
    a = ap.parse_args()
    obs = load_rows(a.rows)
    cycles = [json.loads(x) for x in open(a.cycles) if x.strip()] if a.cycles and os.path.exists(a.cycles) else None
    res = compare(obs, load_quotes(a.quotes), ts_of(a.entry_cutoff) if a.entry_cutoff else None)
    mints = len({o.mint for o in obs})
    print(json.dumps({"observations": len(obs), "mints": mints,
                      "span_utc": [obs[0].ts.isoformat(), obs[-1].ts.isoformat()] if obs else None,
                      "verdict": verdict(res),
                      "probe_gate": evaluate_probe(res, cycles),
                      "validation": evaluate_validation({(r["variant"], r["latency_s"]): r["metrics"] for r in res}),
                      "results": res,
                      "missing_fields": [] if a.quotes else [
                          "recorded BUY quote: tokens_out for usd_in=14.0 per (mint, time >= decision + latency, <= +60 s)",
                          "recorded SELL quote/simulation: usd_out + status for amount_in = exact held tokens per (mint, time >= trigger + latency, <= +60 s)"]},
                     indent=1))


if __name__ == "__main__":
    main()
