#!/usr/bin/env python3
"""Report for the v3 paper test (Momentum v3 vs random baseline). Reads only logs/v3_meta.json, the two logs and states.
Printed to the job log and appended to the Actions Summary. LIVE data only; no DEMO numbers."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.variants import LABELS, V3, V3B  # noqa: E402


def clean(s, n=200):
    return re.sub(r"[`|>]", "", s.replace("<", " below "))[:n]


o = ["# Paper test v3 - migrated pools 6-72h (LIVE data, PAPER ONLY, no wallet, no orders)", ""]
pf = "logs/preflight.json"
if os.path.exists(pf):
    d = json.load(open(pf))
    if d["blocked"]:
        o += [f"**BLOCAT** - unreachable from this runner: {', '.join(d['blocked'])}. No test was run, nothing synthetic used."]
        print("\n".join(o)); sys.exit(0)
META = sys.argv[1] if len(sys.argv) > 1 else "logs/v3_meta.json"
if not os.path.exists(META):
    o += [f"**No result**: {META} missing (the run did not complete). PnL: N/A"]
    print("\n".join(o)); sys.exit(0)
m = json.load(open(META))
V3, V3B = m["strategy"], m["baseline"]                    # works for v3 and v5 (ids come from the run itself)
o[0] = f"# Paper test {V3} vs {V3B} (LIVE data, PAPER ONLY, no wallet, no orders)"
o += [f"Run `{m.get('run_id')}` | commit `{(m.get('sha') or '?')[:8]}` | fingerprint `{m['fingerprint']}` | "
      f"{m['minutes_run']} of {m['minutes_planned']} min | {m['cycles']} cycles", "",
      f"Rules fixed before the run: {clean(json.dumps(m['rules'], sort_keys=True), 900)}", "",
      f"Pools (v3) or tokens (v5) discovered: **{m['pools_discovered']}** | evaluable pairs (universe + safety, "
      f"before sell simulation): **{m['evaluable_pairs']}** | discovery errors: {m['discovery_errors'] or 'none'}", ""]
if m.get("stop"):
    o += [f"**{m['stop']}**", ""]

res = {}
for v in (V3, V3B):
    st = json.load(open(f"state/paper_state_{v}.json"))
    lines = open(os.path.join("logs", m["logs"][v])).read().splitlines()
    tr, op = st["trades"], st["positions"]
    pn = [t["proceeds"] - t["cost"] for t in tr]
    res[v] = dict(
        closed=len(tr), net=sum(pn) if tr else None, best=max(pn) if pn else None,
        sold=sum(t["proceeds"] - (t["cost"] - t.get("written_off", 0)) for t in tr) if tr else None,
        lost=sum(t.get("written_off", 0) for t in tr) if tr else 0.0,
        failed=st.get("failed_exits", 0), deferred=sum(" EXIT_DEFERRED " in l for l in lines),
        open=len(op), open_cost=sum(p["cost"] for p in op.values()), open_val=sum(p["tokens"] * p["last_price"] for p in op.values()),
        wins=st["wins"], losses=st["losses"], lines=lines, curve=[x for _, x in st["curve"]],
        proceeds=sum(t["proceeds"] for t in tr) if tr else None,
        cats=collections.Counter((("exit " if (" EXIT_FAILED " in l or " EXIT_DEFERRED " in l) else "entry ") +
                                  re.search(r"cat=(\w+)", l).group(1)) for l in lines if "cat=" in l))

def f(x):
    return "N/A" if x is None else f"{x:+,.2f}"

o += ["## Result", "", f"| | {LABELS[V3]} | {LABELS[V3B]} |", "|---|---|---|"]
row = lambda name, fn: o.append(f"| {name} | {fn(res[V3])} | {fn(res[V3B])} |")
row("Closed positions (wins / losses)", lambda r: f"{r['closed']} ({r['wins']} / {r['losses']})")
row("Simulated proceeds received (all sales)", lambda r: "N/A" if r["proceeds"] is None else f"${r['proceeds']:,.2f}")
row("PnL of executed simulated sales", lambda r: f(r["sold"]))
row("IMPUTED loss from the $0 rule after FAIL (separate)", lambda r: f(-r["lost"]) if r["closed"] else "N/A")
row("**Net PnL after costs (closed)**", lambda r: f"**{f(r['net'])}**")
row("Net PnL without the best trade", lambda r: f(r["net"] - r["best"]) if r["closed"] else "N/A")
row("Failed exits / deferred exits", lambda r: f"{r['failed']} / {r['deferred']}")
row("Open at the end (cost -> marked value)", lambda r: f"{r['open']} (${r['open_cost']:,.2f} -> ${r['open_val']:,.2f}, not sold)")
def dd(c):
    peak, d = c[0], 0.0
    for x in c:
        peak = max(peak, x); d = max(d, 1 - x / peak)
    return f"{d:.1%}"
row("Max drawdown (equity marks)", lambda r: dd(r["curve"]))
a, b = res[V3], res[V3B]
dr = m.get("baseline_draws", [])
multi = sum(1 for d in dr if d["eligible_options"] > 1)
o += ["", f"Baseline draws (one per Momentum entry, same cycle, same size and costs): {len(dr)}; moments with more than one "
      f"eligible candidate: **{multi}**; moments with exactly one: {sum(1 for d in dr if d['eligible_options'] == 1)}; with none: "
      f"{sum(1 for d in dr if d['eligible_options'] == 0)}."]
if a["closed"] and b["closed"] and multi > 0:
    o += ["", f"Momentum minus baseline (closed trades): {a['net'] - b['net']:+,.2f}. With fewer than 30 closed trades each this "
          "says nothing about skill (INSUFICIENT)."]
else:
    why = ("no closed trades on at least one side" if not (a["closed"] and b["closed"])
           else "the baseline never had more than one eligible option")
    o += ["", f"Comparison with the random baseline: **NETESTABIL** ({why}). Momentum is not claimed to beat it."]
def per_day(st):
    days = collections.OrderedDict()
    for t in sorted(st["trades"], key=lambda t: t["closed"]):
        d = t["closed"][:10]
        x = days.setdefault(d, {"n": 0, "net": 0.0, "imputed": 0.0})
        x["n"] += 1; x["net"] += t["proceeds"] - t["cost"]; x["imputed"] += t.get("written_off", 0)
    return days

import datetime as _dt
span = [x for x, _ in json.load(open(f"state/paper_state_{V3}.json"))["curve"]]
d0 = _dt.date.fromisoformat(span[0][:10]); d1 = _dt.date.fromisoformat(json.load(open(f"state/paper_state_{V3}.json"))["saved"][:10])
all_days = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
o += ["", "## Per day (close date, UTC)", "", "| Day | Momentum closed | Momentum net | Momentum imputed | Baseline closed | Baseline net |", "|---|---|---|---|---|---|"]
pdm, pdb = per_day(json.load(open(f"state/paper_state_{V3}.json"))), per_day(json.load(open(f"state/paper_state_{V3B}.json")))
for d in all_days:
    m_, b_ = pdm.get(d), pdb.get(d)
    o.append(f"| {d} | {m_['n'] if m_ else 0} | {f(m_['net']) if m_ else 'N/A'} | {f(-m_['imputed']) if m_ else 'N/A'} | "
             f"{b_['n'] if b_ else 0} | {f(b_['net']) if b_ else 'N/A'} |")
no_trade = [d for d in all_days if d not in pdm]
worst = min(pdm.items(), key=lambda kv: kv[1]["net"]) if pdm else None
o += ["", f"Days in the test span: {len(all_days)}; Momentum days without a closed trade: {len(no_trade)}; worst day: "
      + (f"{worst[0]} {f(worst[1]['net'])}" if worst else "N/A") + ". A positive total does not mean every day was positive."]
n = a["closed"]
o += ["", f"Verdict: **{'INSUFICIENT' if n < 30 else 'see criteria'}** ({n} closed Momentum trades; validation needs >= 30 over >= 7 days "
      "of new data, positive net PnL, above the baseline, drawdown < 30 %, no trade > 30 % of net profit). "
      "Paper PnL is not realised money.", ""]
o += ["", "## FAIL / UNKNOWN by class (reporting only; accounting unchanged)", "",
      "| Class | Momentum | Baseline |", "|---|---|---|"]
keys = sorted(set(a["cats"]) | set(b["cats"]))
o += [f"| {k} | {a['cats'].get(k, 0)} | {b['cats'].get(k, 0)} |" for k in keys] or ["| none | 0 | 0 |"]
o += ["", "Classes: RATE_LIMIT_OR_TIMEOUT (429/timeout/5xx), NO_ROUTE, SIMULATION_ERROR (transaction failed in simulation), "
      "EXECUTION_LIMIT (slippage tolerance / 2.5 % cap), NO_HOLDER, PRICE_DATA. A FAIL is not a honeypot verdict and an imputed "
      "loss is not a realised loss.", ""]
if not a["closed"]:
    es = m.get("entry_simulations", {})
    o += [f"PnL: N/A for Momentum. Flow: pools discovered {m['pools_discovered']} -> evaluable pairs {m['evaluable_pairs']} -> "
          f"entry sell simulations OK/FAIL/UNKNOWN {es.get('OK', 0)}/{es.get('FAIL', 0)}/{es.get('UNKNOWN', 0)} -> "
          f"entries {sum(' ENTER ' in l for l in a['lines'])} -> closed 0.", ""]
for v in (V3, V3B):
    ex = [l for l in res[v]["lines"] if re.search(r" (ENTER|EXIT|EXIT_FAILED|EXIT_DEFERRED) ", l)]
    o += [f"### Execution detail - {LABELS[v]}", "```"] + [clean(l, 330) for l in ex[-30:]] + (["(none)"] if not ex else []) + ["```", ""]
    fx = [l for l in res[v]["lines"] if "SELL_SIMULATION=" in l or " STOP " in l]
    if fx:
        o += ["FAIL / UNKNOWN / STOP lines (stage and exact error as returned; not an automatic honeypot label):", "```"] + \
             [clean(l, 330) for l in fx[-15:]] + ["```", ""]
jp = os.path.join("logs", m["jsonl"])
if os.path.exists(jp):
    last = {}
    for line in open(jp):
        r = json.loads(line); last[r["mint"]] = r
    c = collections.Counter((re.sub(r"[\$\d][\d,\.]*", "N", r["reason"]) if not r["reason"].startswith("signals") else r["reason"])[:70]
                            for r in last.values())
    o += ["Final state of each observed pair (where candidates disappear):", "", "| Pairs | Reason |", "|---|---|"] + \
         [f"| {k} | {clean(r, 80)} |" for r, k in c.most_common(8)]
print("\n".join(o))
