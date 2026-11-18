#!/usr/bin/env python3
"""v6 'capture' strategy evaluated as a SHADOW of the v5 collection (no new collection, no new data source).

v6 entry set = v5 Momentum entries that also meet the screenshot rules: >= 2 distinct signals, DEX in
{pump.fun, raydium, meteora}, entry inside the 23:00-07:00 Europe/Bucharest session. Everything else (universe,
safety, sell simulation, exits) is identical to v5, so each v6 trade is a v5 trade. Rules fixed in docs/PREREG_V6.md.

Re-sized with the screenshot sizer from a $70 paper balance: 20 % of current realised equity for 2 signals, 30 % for 3,
x0.5 after two consecutive losses (until a win), max 2 open, compounding. Per-trade outcome is the v5 trade's return with
fixed per-swap fees re-applied at the new size. Entries pause for the rest of the session day after a 30 % drawdown
from that day's realised peak. Baseline = v5 random-baseline trades paired with these entries.

Usage: python scripts/v6_shadow.py <dir with state/ and logs/ from paper-data-v5>"""
import glob
import json
import os
import re
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Bucharest")
V5, V5B = "momentum-v5-1signal", "random-baseline-v5"
START, NET_FEE, DEXES = 70.0, 0.02, {"pump.fun", "raydium", "meteora"}
MIN_CLOSED, MIN_SESSIONS, MIN_SESSION_MIN = 30, 7, 360


def utc(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def session_of(t):
    """Session label = local date on which the 23:00-07:00 session starts; None outside the session."""
    lt = t.astimezone(TZ)
    if lt.time() >= dtime(23, 0):
        return lt.date().isoformat()
    if lt.time() < dtime(7, 0):
        return (lt.date() - timedelta(days=1)).isoformat()
    return None


def entries_from_logs(logs):
    """mint -> (first ENTER time, signals, dex) from v5 Momentum logs."""
    out = {}
    for f in sorted(glob.glob(os.path.join(logs, f"night_{V5}_*.log"))):
        for line in open(f):
            m = re.match(r"(\S+ \S+) ENTER\s+\S+ mint=(\S+) part 1/2 .*signals=(\S+)", line)
            if m and m.group(2) not in out:
                out[m.group(2)] = (utc(m.group(1).replace(" ", "T")), m.group(3).split("+"))
    return out


def dex_of(logs):
    dex = {}
    for f in glob.glob(os.path.join(logs, "v3_collect_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("variant") == V5:
                dex[r["mint"]] = r.get("dex")
    return dex


def scaled(t, size):
    """v5 trade -> same trade at `size` USD: v5 return without its fixed fees, then fixed fees at the new size."""
    legs = max(1, sum(1 for r in t["reasons"] if r != "EXIT_FAILED"))
    gross_in = t["cost"] - 2 * NET_FEE
    gross_out = t["proceeds"] + (NET_FEE * legs if t["proceeds"] > 0 else 0)
    r = gross_out / gross_in if gross_in > 0 else 0.0
    proceeds = max(0.0, (size - 2 * NET_FEE) * r - (NET_FEE * legs if t["proceeds"] > 0 else 0))
    imputed = size * (t.get("written_off", 0) / t["cost"]) if t["cost"] else 0.0
    return proceeds, imputed


def replay(data_dir):
    st = os.path.join(data_dir, "state"); logs = os.path.join(data_dir, "logs")
    mom = json.load(open(os.path.join(st, f"paper_state_{V5}.json")))["trades"]
    base = json.load(open(os.path.join(st, f"paper_state_{V5B}.json")))["trades"] if os.path.exists(os.path.join(st, f"paper_state_{V5B}.json")) else []
    ent, dex = entries_from_logs(logs), dex_of(logs)
    picked, excluded = [], {"<2 signals": 0, "dex not allowed": 0, "outside session": 0, "no entry log": 0}
    for t in sorted(mom, key=lambda t: t["opened"]):
        e = ent.get(t.get("addr"))
        if not e:
            excluded["no entry log"] += 1; continue
        if len(set(e[1])) < 2:
            excluded["<2 signals"] += 1; continue
        if dex.get(t.get("addr")) not in DEXES:
            excluded["dex not allowed"] += 1; continue
        if session_of(e[0]) is None:
            excluded["outside session"] += 1; continue
        picked.append((t, e))
    eq, streak, open_until, trades, day_peak, paused = START, 0, [], [], {}, set()
    for t, e in picked:
        t_open, t_close = utc(t["opened"]), utc(t["closed"])
        sess = session_of(e[0])
        open_until = [c for c in open_until if c > t_open]
        if len(open_until) >= 2 or sess in paused:
            excluded.setdefault("capacity/paused", 0); excluded["capacity/paused"] += 1; continue
        frac = 0.20 if len(set(e[1])) == 2 else 0.30
        frac *= 0.5 if streak >= 2 else 1.0
        size = round(eq * frac, 2)
        proceeds, imputed = scaled(t, size)
        pnl = proceeds - size
        eq += pnl
        streak = 0 if pnl > 0 else streak + 1
        open_until.append(t_close)
        day_peak[sess] = max(day_peak.get(sess, eq - pnl), eq)    # realised peak of this session (starts at its opening equity)
        if eq <= 0.7 * day_peak[sess]:
            paused.add(sess)                                    # 30 % daily drawdown: no new entries for the rest of the session
        trades.append({"mint": t.get("addr"), "symbol": t["symbol"], "session": sess, "opened": t["opened"], "closed": t["closed"],
                       "signals": sorted(set(e[1])), "size": size, "proceeds": round(proceeds, 4), "imputed": round(imputed, 4),
                       "pnl": round(pnl, 4)})
    bpaired = []
    for tr in trades:                                        # baseline trades opened in the same cycle as a v6 entry
        o = utc(tr["opened"])
        b = next((b for b in base if abs((utc(b["opened"]) - o).total_seconds()) <= 45), None)
        if b:
            p, imp = scaled(b, tr["size"]); bpaired.append({"pnl": p - tr["size"], "imputed": imp})
    return {"trades": trades, "excluded": excluded, "equity_end": eq, "baseline_paired": bpaired}


def session_coverage(logs):
    """Minutes of v5 collection inside each 23:00-07:00 Bucharest session (from logs/runs/*.json)."""
    cov = {}
    for f in glob.glob(os.path.join(logs, "runs", f"{V5}_*.json")):
        m = json.load(open(f)); a, b = utc(m["started_utc"]), utc(m["ended_utc"])
        t = a
        while t < b:
            nxt = min(b, t + timedelta(minutes=1))
            s_ = session_of(t)
            if s_:
                cov[s_] = cov.get(s_, 0) + (nxt - t).total_seconds() / 60
            t = nxt
    return cov


def verdict(res, cov, n_multi_draws):
    tr, sessions = res["trades"], [s for s, v in cov.items() if v >= MIN_SESSION_MIN]
    if len(tr) < MIN_CLOSED or len(sessions) < MIN_SESSIONS:
        return f"PENDING/INSUFICIENT ({len(tr)} closed of {MIN_CLOSED}, {len(sessions)} covered sessions of {MIN_SESSIONS})"
    pn = [t["pnl"] for t in tr]; net = sum(pn)
    eq, peak, dd = START, START, 0.0
    for p in pn:
        eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
    b = res["baseline_paired"]; bnet = sum(x["pnl"] for x in b)
    testable = len(b) >= MIN_CLOSED and n_multi_draws >= 20
    ok = net > 0 and net - max(pn) > 0 and dd < 0.30 * START and max(pn) <= 0.30 * net and testable and net > bnet
    if not ok and not testable and net > 0 and net - max(pn) > 0 and dd < 0.30 * START and max(pn) <= 0.30 * net:
        return "NU TRECE: reper NETESTABIL"
    return "CRITERIU INDEPLINIT (validare)" if ok else "FARA AVANTAJ"


def report(res, runs_dir=None):
    tr = res["trades"]
    o = ["# v6 capture strategy - SHADOW of the v5 collection (derived, paper only)", ""]
    o.append(f"Closed v6 trades: {len(tr)} | excluded v5 trades: {res['excluded']}")
    if not tr:
        o.append("PnL: N/A (no closed v6 trade)")
        return "\n".join(o)
    pn = [t["pnl"] for t in tr]
    net, best = sum(pn), max(pn)
    eq, peak, dd = START, START, 0.0
    for p in pn:
        eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
    by = {}
    for t in tr:
        by.setdefault(t["session"], []).append(t["pnl"])
    worst = min(by.items(), key=lambda kv: sum(kv[1]))
    bnet = sum(b["pnl"] for b in res["baseline_paired"])
    o += [f"Net PnL after costs (incl. imputed): {net:+.2f} USD on a {START:.0f} USD paper balance | without best trade: {net - best:+.2f}",
          f"Imputed losses (failed exits): {-sum(t['imputed'] for t in tr):+.2f} | max drawdown: {dd:.2f} USD | end equity {res['equity_end']:.2f}",
          f"Worst session: {worst[0]} {sum(worst[1]):+.2f} | baseline (paired) net: {bnet:+.2f} over {len(res['baseline_paired'])} trades",
          "", "| Session (23:00-07:00 Bucharest) | Trades | Net |", "|---|---|---|"]
    o += [f"| {s} | {len(v)} | {sum(v):+.2f} |" for s, v in sorted(by.items())]
    for s, v in sorted(by.items()):
        ts = [t for t in tr if t["session"] == s]
        b, w = max(ts, key=lambda t: t["pnl"]), min(ts, key=lambda t: t["pnl"])
        agg = {}
        for t in ts:
            agg[t["symbol"]] = agg.get(t["symbol"], 0) + t["pnl"]
        o.append(f"07:00 recap {s}: best {b['symbol']} {b['pnl']:+.2f}, worst {w['symbol']} {w['pnl']:+.2f}, trades {len(ts)}, "
                 f"top 3: {', '.join(f'{k} {v:+.2f}' for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:3])}")
    return "\n".join(o)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    res = replay(d)
    cov = session_coverage(os.path.join(d, "logs"))
    print(report(res))
    print("\nSession coverage (min of collection inside 23:00-07:00 Bucharest):", {k: round(v) for k, v in sorted(cov.items())})
    v6 = {t["mint"] for t in res["trades"]}
    multi = sum(1 for f in glob.glob(os.path.join(d, "logs", "runs", "*.json")) for x in json.load(open(f)).get("baseline_draws", [])
                if x.get("momentum_entry") in v6 and x["eligible_options"] > 1)
    print("Sessions without data are those missing above. Verdict:", verdict(res, cov, multi))
