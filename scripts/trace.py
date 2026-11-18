#!/usr/bin/env python3
"""Per-variant funnel from a finished run's files (cwd = folder holding logs/ and state/):
observed -> security data received -> filters passed -> SELL_SIMULATION PASS/FAIL/UNKNOWN -> positions -> PnL.
Also splits pump.fun curve pairs from migrated pools, and keeps the '2 x quote reserves' liquidity estimate apart
from what an exit at position size would collect (model only; a real figure exists only for executed paper exits)."""
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.variants import V1, V2, make  # noqa: E402

meta = json.load(open("logs/run_meta.json"))
rows = [json.loads(l) for l in open(os.path.join("logs", meta["jsonl"]))]
norm = lambda r: re.sub(r"[\$\d][\d,\.]*", "N", r)[:70]
o = [f"# Trace - run {meta.get('run_id')} commit {(meta.get('sha') or '?')[:8]} ({meta['minutes']:g} min, {meta['cycles']} cycles)", ""]
for v in (V1, V2):
    cfg = make(v)
    R = [r for r in rows if r["variant"] == v]
    by = collections.defaultdict(list)
    for r in R:
        by[r["mint"]].append(r)
    sec = {m for m, rs in by.items() if any(r["holders"] is not None for r in rs)}
    passed = {m for m, rs in by.items() if any(r["verdict"] in ("CANDIDATE", "ENTERED") or r["verdict"].startswith("SELL_SIMULATION=") for r in rs)}
    ss = collections.Counter()
    for m, rs in by.items():
        vs = [r["verdict"] for r in rs if r["verdict"].startswith("SELL_SIMULATION=")]
        if vs:
            ss[vs[-1].split("=")[1]] += 1
    entered = {m for m, rs in by.items() if any(r["verdict"] == "ENTERED" for r in rs)}
    st = json.load(open(f"state/paper_state_{v}.json"))
    closed = st["trades"]
    o += [f"## {v}", "",
          f"observed {len(by)} -> security data received {len(sec)} -> filters passed {len(passed)} -> "
          f"SELL_SIMULATION PASS {ss['OK']} / FAIL {ss['FAIL']} / UNKNOWN {ss['UNKNOWN']} -> entered {len(entered)} -> "
          f"open {len(st['positions'])} / closed {len(closed)}"]
    if closed:
        pnls = [t["proceeds"] - t["cost"] for t in closed]
        fails = sum("EXIT_FAILED" in t["reasons"] for t in closed)
        o.append(f"PnL net estimated after costs: {sum(pnls):+,.2f} | without best trade: {sum(pnls) - max(pnls):+,.2f} | "
                 f"failed exits {st.get('failed_exits', 0)} (trades touched: {fails})")
    else:
        o.append("PnL: N/A")
    last = {m: rs[-1] for m, rs in by.items()}
    fin = collections.Counter(norm(r["reason"]) for r in last.values() if r["verdict"] != "ENTERED")
    o.append("final state per pair: " + "; ".join(f"{n} x {k}" for k, n in fin.most_common(5)))
    # curve vs migrated (same feed for both variants, but thresholds differ)
    curve = {m for m, rs in by.items() if any(r.get("liquidity_source") == "pump_curve_rpc" for r in rs)}
    migrated = {m for m, rs in by.items() if m not in curve and any(r.get("liquidity_source") == "dexscreener" for r in rs)}
    unknown = set(by) - curve - migrated
    def mx(ms, key):
        vals = [r[key] for m in ms for r in by[m] if r.get(key) is not None]
        return max(vals) if vals else None
    o += ["", f"| group | pairs | max liquidity seen | pairs ever >= liq floor ${cfg.min_liq:,.0f} | ever >= liq floor AND vol5m >= ${cfg.min_vol5m:,.0f} |",
          "|---|---|---|---|---|"]
    for name, ms in (("pump.fun curve (liquidity = 2 x real quote reserves, RPC)", curve),
                     ("migrated / AMM pools (liquidity from DexScreener)", migrated), ("liquidity UNKNOWN", unknown)):
        ge = {m for m in ms if any((r["liquidity"] or 0) >= cfg.min_liq for r in by[m])}
        both = {m for m in ms if any((r["liquidity"] or 0) >= cfg.min_liq and r["vol5m"] >= cfg.min_vol5m for r in by[m])}
        m_ = mx(ms, "liquidity")
        o.append(f"| {name} | {len(ms)} | {'-' if m_ is None else '$%s' % format(round(m_), ',')} | {len(ge)} | {len(both)} |")
    # estimate vs collectable at position size (model)
    size = cfg.start_balance * cfg.size_fracs[2]
    o += ["", f"Liquidity estimate vs. what an exit of one position (${size:,.0f} = 20% of start) would collect - MODEL ONLY "
          "(constant-product depth from the estimate, fee 0.3%); a real figure exists only for executed paper exits:", ""]
    for name, ms in (("curve", curve), ("migrated", migrated)):
        vals = sorted({round(r["liquidity"]) for m in ms for r in by[m][-1:] if r["liquidity"]})
        if vals:
            med = vals[len(vals) // 2]
            slip = min(0.003 + 2 * size / med, 0.5)
            o.append(f"- {name}: median latest estimate ${med:,} -> modelled proceeds ${size * (1 - slip) * (1 - cfg.fee):,.2f} "
                     f"for ${size:,.0f} of tokens (impact {slip:.1%}{', above the 2.5% entry cap' if slip > cfg.max_slippage else ''})")
    ex = [l for l in open(os.path.join("logs", meta["logs"][v])).read().splitlines() if re.search(r" (EXIT|EXIT_FAILED) ", l)]
    o.append(f"- executed paper exits in this run: {len(ex)}" + ("" if ex else " (none, so no actual collectable figure)"))
    o.append("")
se, sx = meta.get("sellsim_entry", {}), meta.get("sellsim_exit", {})
o.append(f"Sell simulations performed (shared cache): before entry PASS {se.get('OK',0)} / FAIL {se.get('FAIL',0)} / UNKNOWN {se.get('UNKNOWN',0)}; "
         f"at exit PASS {sx.get('OK',0)} / FAIL {sx.get('FAIL',0)} / UNKNOWN {sx.get('UNKNOWN',0)}.")
o.append("'security data received' = RugCheck report returned holders for the pair at least once.")
print("\n".join(o))
