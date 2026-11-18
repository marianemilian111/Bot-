#!/usr/bin/env python3
"""Markdown report for the Actions Summary (or stdout). Reads only files the collector wrote."""
import collections
import glob
import json
import os
import re
import sys

def latest(pat):
    f = sorted(glob.glob(pat), key=os.path.getmtime)   # newest by modification time, never by name
    return f[-1] if f else None

out = ["## Live smoke test: read-only collector + paper (frozen `momentum-v1`)", ""]
pf = "logs/preflight.json"
if os.path.exists(pf):
    d = json.load(open(pf))
    out += ["| Source | Mandatory | HTTP | Result |", "|---|---|---|---|"]
    for r in d["rows"]:
        out.append(f"| {r['source']} | {'yes' if r['mandatory'] else 'no'} | {r['http']} | {'OK' if r['ok'] else 'FAIL ' + r['detail']} |")
    if d["blocked"]:
        out += ["", f"**BLOCAT** - mandatory source(s) unreachable from this runner: {', '.join(d['blocked'])}. "
                "No live data was collected. Nothing was replaced with synthetic data.", ""]
j, lg = latest("logs/collect_*.jsonl"), latest("logs/night_*.log")
if j:
    last = {}
    n = 0
    for line in open(j):
        r = json.loads(line); n += 1
        last[r["mint"]] = r
    fin = collections.Counter(re.sub(r"[\$\d][\d,\.]*", "N", r["reason"])[:70] for r in last.values())
    out += ["", f"**Distinct pairs observed (<= 6h old):** {len(last)}  ({n} snapshots)",
            f"**Reached the sell-simulation stage (CANDIDATE):** {sum(r['verdict'] == 'CANDIDATE' for r in last.values())}",
            "", "Final non-sell verdict per pair:", "", "| Count | Reason |", "|---|---|"]
    out += [f"| {c} | {r} |" for r, c in fin.most_common()]
if lg:
    lines = open(lg).read().splitlines()
    kinds = collections.Counter(l.split()[2] for l in lines if len(l.split()) > 2)
    ss = [l for l in lines if "SELL_SIMULATION=" in l]
    out += ["", f"**Bot log kinds:** {dict(kinds)}", f"**Sell-simulation outcomes logged:** {len(ss)} rejected as FAIL/UNKNOWN; "
            f"entries (ENTER) = {kinds.get('ENTER', 0)}, closed trades (CLOSED) = {kinds.get('CLOSED', 0)}, "
            f"failed exits (EXIT_FAILED) = {kinds.get('EXIT_FAILED', 0)}"]
    out += ["", "Last sell-simulation lines:", "```"] + [l[:230] for l in ss[-5:]] + ["```"]
out += ["", "> A smoke test is not evidence of profit. Paper fills are modelled from pool depth; "
        "SELL_SIMULATION is a holder-address simulation and was not validated against a known honeypot."]
print("\n".join(out))
