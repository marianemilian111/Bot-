#!/usr/bin/env python3
"""Per-variant funnel from a finished run's files (cwd = folder holding logs/ and state/).
observed -> security data -> filters passed -> entry sell simulation -> positions -> PnL, plus:
* migrated pools added by the extended discovery (GeckoTerminal new/trending, DexScreener top boosts), deduped by pool address;
* why a filter-passing pair did not reach the entry simulation;
* every SELL_SIMULATION FAIL/UNKNOWN with its stage and exact error (no automatic 'honeypot' label);
* PnL of executed simulated sales kept apart from the cost written off by failed exits; open positions at the end."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.variants import V1, V2, make  # noqa: E402

EXT = {"gt_new_pools", "gt_trending_pools", "ds_boosts_top"}
BASE = {"ds_profiles", "ds_boosts_latest"}
meta = json.load(open("logs/run_meta.json"))
rows = [json.loads(l) for l in open(os.path.join("logs", meta["jsonl"]))]
norm = lambda r: re.sub(r"[\$\d][\d,\.]*", "N", r)[:70]


def stage(detail):
    d = detail.lower()
    return ("holder lookup" if "holder" in d else "SELL_QUOTE (Jupiter quote)" if d.startswith("sell_quote") else
            "swap build (Jupiter)" if d.startswith("swap build") else "simulateTransaction (RPC)" if "simulatetransaction" in d else "?")


def clean(s, n=160):
    return re.sub(r"[`|<>]", "", s)[:n]


o = [f"# Trace - run {meta.get('run_id')} commit {(meta.get('sha') or '?')[:8]} ({meta['minutes']:g} min, {meta['cycles']} cycles)", ""]
d = meta.get("discovery")
if d:
    o += ["## Discovery (sources pick tokens to look at; never a buy signal)", "",
          f"GeckoTerminal migrated pools seen, deduped by pool address: **{d['gt_pools_unique']}** {d['gt_pools_by_source']}  ",
          f"Tokens per source: {d['tokens_by_source']}  ", f"Discovery errors: {d['errors'] or 'none'}", ""]

for v in (V1, V2):
    cfg = make(v)
    R = [r for r in rows if r["variant"] == v]
    by = collections.defaultdict(list)
    for r in R:
        by[r["mint"]].append(r)
    st = json.load(open(f"state/paper_state_{v}.json"))
    lines = open(os.path.join("logs", meta["logs"][v])).read().splitlines()
    sec = {m for m, rs in by.items() if any(r["holders"] is not None for r in rs)}
    passed = {m for m, rs in by.items() if any(r["verdict"] in ("CANDIDATE", "ENTERED") or r["verdict"].startswith("SELL_SIMULATION=") for r in rs)}
    simmed = {m for m, rs in by.items() if any(r["verdict"] == "ENTERED" or r["verdict"].startswith("SELL_SIMULATION=") for r in rs)}
    entered = {m for m, rs in by.items() if any(r["verdict"] == "ENTERED" for r in rs)}
    trades, opn = st["trades"], st["positions"]
    o += [f"## {v}", "",
          f"observed {len(by)} -> security data {len(sec)} -> filters passed {len(passed)} -> entry sell simulation attempted {len(simmed)} "
          f"-> entered {len(entered)} -> closed {len(trades)} / still open {len(opn)}", ""]

    # migrated pools from the extended discovery, deduped by pool address
    mig = {}
    for m, rs in by.items():
        src = set().union(*[set(r.get("disc") or []) for r in rs])
        if src & EXT and not (src & BASE) and any(r.get("liquidity_source") == "dexscreener" for r in rs):
            mig[rs[-1].get("pair") or m] = m
    mm = set(mig.values())
    closed_mm = [t for t in trades if t.get("addr") in mm]
    o += [f"Migrated pools added ONLY by the extended discovery (unique pool addresses): **{len(mig)}** -> filters passed "
          f"{len(mm & passed)} -> entry simulation {len(mm & simmed)} -> closed {len(closed_mm)}", ""]

    # filters passed but no simulation: say why
    gap = passed - simmed
    if gap:
        o.append(f"Passed filters but no entry simulation ({len(gap)}):")
        for m in sorted(gap):
            why = [l.split("reason: ", 1)[-1] for l in lines if f"mint={m}" in l and (" SKIPPED " in l or " FILTERED " in l)]
            o.append(f"- `{m[:6]}...` " + (clean("; ".join(dict.fromkeys(why)) or "no log line: passed on the last cycle(s) or another check (one entry per pair)")))
        o.append("")

    # PnL split
    if trades and any("written_off" not in t for t in trades):
        net = sum(t["proceeds"] - t["cost"] for t in trades)
        o += [f"Net PnL, closed positions: **{net:+,.2f}** (this run's state predates the sold/written-off split; split not recorded)", ""]
    elif trades:
        sold = sum(t["proceeds"] - (t["cost"] - t.get("written_off", 0)) for t in trades)
        lost = sum(t.get("written_off", 0) for t in trades)
        net = sum(t["proceeds"] - t["cost"] for t in trades)
        pn = [t["proceeds"] - t["cost"] for t in trades]
        cur = [x for _, x in st["curve"]]
        peak, dd = cur[0], 0.0
        for x in cur:
            peak = max(peak, x); dd = max(dd, 1 - x / peak)
        o += ["| | |", "|---|---|",
              f"| PnL of executed simulated sales (after modelled costs) | {sold:+,.2f} |",
              f"| Cost written off by failed exits (proceeds $0) | {-lost:+,.2f} |",
              f"| **Net PnL, closed positions** | **{net:+,.2f}** |",
              f"| Net without the best trade | {net - max(pn):+,.2f} |",
              f"| Closed / wins / losses | {len(trades)} / {st['wins']} / {st['losses']} |",
              f"| Max drawdown (equity marks) | {dd:.1%} |", ""]
    else:
        o += ["PnL: N/A (no closed positions)", ""]
    if opn:
        val = sum(p["tokens"] * p["last_price"] for p in opn.values())
        cost = sum(p["cost"] for p in opn.values())
        o += [f"Still open at the end of the job: {len(opn)} (cost ${cost:,.2f}, marked at last price ${val:,.2f}); "
              "kept open in state, NOT sold to produce a PnL.", ""]

    # every FAIL / UNKNOWN with stage and exact error
    fu = []
    for l in lines:
        mm_ = re.search(r"SELL_SIMULATION=(FAIL|UNKNOWN) \((.*)\)\s*$", l)
        if " FILTERED " in l and mm_:
            fu.append(("entry", mm_.group(1), l.split()[3], mm_.group(2)))
        mx = re.search(r" (EXIT_FAILED|EXIT_DEFERRED) +(\S+) .*?\((.*?)\)", l)
        if mx:
            fu.append(("exit", "FAIL" if mx.group(1) == "EXIT_FAILED" else "UNKNOWN", mx.group(2), mx.group(3)))
    if fu:
        o += ["| When | Result | Token | Stage | Exact error |", "|---|---|---|---|---|"]
        o += [f"| {w} | {r} | {clean(t, 14)} | {stage(dt)} | {clean(dt)} |" for w, r, t, dt in fu]
        o += ["", "A FAIL means the simulated sale did not go through at that moment; it is not by itself proof of a honeypot.", ""]
    fin = collections.Counter(norm(r["reason"]) for r in {m: rs[-1] for m, rs in by.items()}.values() if r["verdict"] != "ENTERED")
    o += ["Final state per pair: " + "; ".join(f"{n} x {k}" for k, n in fin.most_common(5)), ""]

se, sx = meta.get("sellsim_entry", {}), meta.get("sellsim_exit", {})
o.append(f"Sell simulations (shared cache): entry PASS {se.get('OK',0)} / FAIL {se.get('FAIL',0)} / UNKNOWN {se.get('UNKNOWN',0)}; "
         f"exit PASS {sx.get('OK',0)} / FAIL {sx.get('FAIL',0)} / UNKNOWN {sx.get('UNKNOWN',0)}. EXPLORATION run, not validation.")
print("\n".join(o))
