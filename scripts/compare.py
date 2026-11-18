#!/usr/bin/env python3
"""PAPER ONLY comparison of momentum-v1 and momentum-v2-explore on the same live feed (Markdown for the Actions Summary).
Reads only files the collector wrote: logs/run_meta.json, logs/collect_*.jsonl, state/paper_state_<variant>.json, per-variant logs."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.dashboard import spark  # noqa: E402
from dexbot.frozen import fingerprint  # noqa: E402
from dexbot.variants import LABELS, V1, V2, make  # noqa: E402


def clean(s, n=14, extra=""):
    return re.sub(r"[^A-Za-z0-9_.$%" + extra + "-]", "", str(s))[:n] or "?"


def norm(r):
    return re.sub(r"[\$\d][\d,\.]*", "N", r)[:80]


pf = "logs/preflight.json"
pre = []
if os.path.exists(pf):
    d = json.load(open(pf))
    pre = ["", "| Source | HTTP | Result |", "|---|---|---|"] + [
        f"| {r['source']} | {r['http']} | {'OK' if r['ok'] else 'FAIL ' + clean(r['detail'], 100, ' :,')} |" for r in d["rows"]]
    if d["blocked"]:
        pre += ["", f"**BLOCAT** - unreachable from this runner: {', '.join(d['blocked'])}. No live data collected, nothing synthetic used."]
meta_p = "logs/run_meta.json"
o = ["# Paper comparison: momentum-v1 vs momentum-v2-explore", "",
     "> **PAPER ONLY** - virtual balances, no wallet, no real orders. Both variants read the same live feed in the same run, "
     "each with its own balance, log and state. Entry thresholds are fixed in code and were not changed during the run.", ""]
if not os.path.exists(meta_p):
    o += pre + ["**BLOCAT / no data**: `logs/run_meta.json` is missing, so the collector did not complete in this job.",
          "0 tranzactii inchise - PnL estimat: N/A (both variants)."]
    print("\n".join(o)); sys.exit(0)
meta = json.load(open(meta_p))
o += pre + ["", f"Run `{meta.get('run_id')}` | commit `{(meta.get('sha') or '?')[:7]}` | {meta['minutes']:g} min | {meta['cycles']} cycles", "",
      "| Threshold | v1 (frozen) | v2 (explore) |", "|---|---|---|"]
c1, c2 = make(V1), make(V2)
for label, f in [("min liquidity", lambda c: f"${c.min_liq:,.0f}"), ("min 5m volume", lambda c: f"${c.min_vol5m:,.0f}"),
                 ("signals needed", lambda c: str(c.min_signals)), ("exits / costs", lambda c: "same"),
                 ("mint, freeze, LP, SELL_SIMULATION", lambda c: "same, UNKNOWN = reject")]:
    o.append(f"| {label} | {f(c1)} | {f(c2)} |")
o.append(f"| fingerprint | `{fingerprint(c1, V1)}` | `{fingerprint(c2, V2)}` |")

rows = collections.defaultdict(dict)
jp = os.path.join("logs", meta["jsonl"])
nrows = 0
if os.path.exists(jp):
    for line in open(jp):
        r = json.loads(line); nrows += 1
        rows[r["variant"]][r["mint"]] = r            # last row per pair = final state

o += ["", "## Results per variant", "", "| | v1 | v2 |", "|---|---|---|"]
data = {}
for v in (V1, V2):
    sp = f"state/paper_state_{v}.json"
    st = json.load(open(sp)) if os.path.exists(sp) else None
    lg = meta["logs"].get(v)
    lines = open(os.path.join("logs", lg)).read().splitlines() if lg and os.path.exists(os.path.join("logs", lg)) else []
    data[v] = (st, lines)

def cell(fn):
    return " | ".join(fn(*data[v], v) for v in (V1, V2))

def pnl(st, lines, v):
    if st is None or not st["trades"]:
        return "**N/A** (0 closed)"
    p = sum(t["proceeds"] - t["cost"] for t in st["trades"])
    return f"**{p:+,.2f}** ({p / st['start'] * 100:+.1f}% of start)"

def equity(st, lines, v):
    return "N/A" if st is None else f"${st['cash'] + sum(p['tokens'] * p['last_price'] for p in st['positions'].values()):,.2f}"

def toprej(st, lines, v):
    c = collections.Counter(norm(r["reason"]) for r in rows[v].values()
                            if r["verdict"] not in ("ENTERED", "CANDIDATE") and not r["verdict"].startswith("SELL_SIMULATION=OK"))
    return f"{c.most_common(1)[0][0]} ({c.most_common(1)[0][1]} pairs)" if c else "none"

o += [f"| Start balance (virtual) | {cell(lambda st, l, v: '$%s' % format(st['start'], ',.2f') if st else 'N/A')} |",
      f"| Equity (estimate) | {cell(lambda st, l, v: equity(st, l, v))} |",
      f"| Open positions | {cell(lambda st, l, v: str(len(st['positions'])) if st else 'N/A')} |",
      f"| Closed positions | {cell(lambda st, l, v: str(len(st['trades'])) if st else 'N/A')} |",
      f"| Net PnL, estimated after modelled costs | {cell(pnl)} |",
      f"| Wins / losses | {cell(lambda st, l, v: '%d / %d' % (st['wins'], st['losses']) if st else 'N/A')} |",
      f"| Failed exits (EXIT_FAILED) | {cell(lambda st, l, v: str(st.get('failed_exits', 0)) if st else 'N/A')} |",
      f"| Exits deferred (sale unverifiable) | {cell(lambda st, l, v: str(sum(' EXIT_DEFERRED ' in x for x in l)))} |",
      f"| Distinct pairs observed (<= 6h) | {cell(lambda st, l, v: str(len(rows[v])))} |",
      f"| Passed non-sell checks (candidates) | {cell(lambda st, l, v: str(sum(r['verdict'] in ('CANDIDATE', 'ENTERED') or r['verdict'].startswith('SELL_SIMULATION=') for r in rows[v].values())))} |",
      f"| Entered | {cell(lambda st, l, v: str(sum(r['verdict'] == 'ENTERED' for r in rows[v].values())))} |",
      f"| Filter that rejected most pairs | {cell(toprej)} |"]

ss_e, ss_x = meta.get("sellsim_entry", {}), meta.get("sellsim_exit", {})
o += ["", "Sell simulations (shared by both variants, same cache): "
      f"on candidate pairs before entry OK/FAIL/UNKNOWN = {ss_e.get('OK', 0)}/{ss_e.get('FAIL', 0)}/{ss_e.get('UNKNOWN', 0)}; "
      f"at exit time = {ss_x.get('OK', 0)}/{ss_x.get('FAIL', 0)}/{ss_x.get('UNKNOWN', 0)}."]

for v in (V1, V2):
    st, lines = data[v]
    o += ["", f"## {LABELS[v]}", ""]
    c = collections.Counter(norm(r["reason"]) for r in rows[v].values() if r["verdict"] != "ENTERED")
    o += ["Top reasons for the final state of each pair:", "", "| Pairs | Reason |", "|---|---|"]
    o += [f"| {n} | {clean(r, 80, ' =<>(),:/')} |" for r, n in c.most_common(4)] or ["| 0 | none |"]
    if st:
        cur = [float(x) for _, x in st["curve"]]
        if len(cur) >= 2:
            o += ["", "```", spark(cur, 50), f"equity marks: start ${cur[0]:,.2f} last ${cur[-1]:,.2f} min ${min(cur):,.2f} max ${max(cur):,.2f}", "```"]
        if st["positions"]:
            o += ["", "Open positions:", "", "| Symbol | Cost | x | Stage |", "|---|---|---|---|"]
            for p in st["positions"].values():
                avg = p["cost"] / p["tokens_bought"] if p["tokens_bought"] else 0
                o.append(f"| {clean(p['symbol'])} | ${p['cost']:,.2f} | {p['last_price'] / avg if avg else 0:.2f}x | {p['stage']} |")
        if st["trades"]:
            o += ["", "Closed trades:", "", "| Symbol | PnL | % | Exit |", "|---|---|---|---|"]
            for t in st["trades"][-15:]:
                d = t["proceeds"] - t["cost"]
                o.append(f"| {clean(t['symbol'])} | {d:+,.2f} | {d / t['cost'] * 100:+.1f}% | {clean(', '.join(dict.fromkeys(t['reasons'])), 40, ', ')} |")
        else:
            o += ["", "0 tranzactii inchise - PnL estimat: N/A"]
    fx = [l[:200] for l in lines if " EXIT_FAILED " in l][-3:]
    if fx:
        o += ["", "Failed exits:", "```"] + fx + ["```"]

o += ["", "> Estimates from paper fills (modelled fee and depth slippage); failed exits are counted. A negative PnL is shown as is. "
      "v2 uses relaxed, experimental thresholds chosen before this run; its result is not validation and not evidence of profit. "
      "SELL_SIMULATION is a holder-address simulation, not validated against a known honeypot."]
print("\n".join(o))
