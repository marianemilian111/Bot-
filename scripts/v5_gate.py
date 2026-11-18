#!/usr/bin/env python3
"""Pre-registered phase / stop logic for the v5 collection (docs/PREREG_V5.md). Reads only state/ and logs/runs/.

Phases: VALIDATION (from the first run) -> REPLICATION (from the moment validation passed) -> DONE.
A window is complete with >= 30 closed Momentum positions AND >= 7 UTC days with >= 12 h coverage.
Stops: window complete -> evaluate criterion (pass -> next phase / AVANTAJ PAPER REPLICAT; fail -> FARA AVANTAJ);
14 calendar days without a complete window -> INSUFICIENT. Output: JSON status; exit 0 = COLLECT, 3 = STOP."""
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

V5, V5B = "momentum-v5-1signal", "random-baseline-v5"
MIN_CLOSED, MIN_DAYS, MIN_DAY_MIN, MAX_DAYS, START = 30, 7, 720, 14, 250.0


def _t(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)      # runner wall clock is UTC on GitHub-hosted runners


def runs(logs="logs"):
    return sorted((json.load(open(p)) for p in glob.glob(os.path.join(logs, "runs", f"{V5}_*.json"))), key=lambda m: m["started_utc"])


def coverage(rs, since):
    cov = {}
    for m in rs:
        a, b = max(_t(m["started_utc"]), since), _t(m["ended_utc"])
        while a < b:
            day_end = datetime(a.year, a.month, a.day, tzinfo=timezone.utc) + timedelta(days=1)
            seg = min(b, day_end)
            cov[a.date().isoformat()] = cov.get(a.date().isoformat(), 0) + (seg - a).total_seconds() / 60
            a = seg
    return cov


def trades(state_dir, sid, since, until=None):
    p = os.path.join(state_dir, f"paper_state_{sid}.json")
    if not os.path.exists(p):
        return []
    out = []
    for t in json.load(open(p))["trades"]:
        c = _t(t["closed"])
        if c >= since and (until is None or c < until):
            out.append(t)
    return out


def criterion(mom, base, draws_multi):
    pn = [t["proceeds"] - t["cost"] for t in mom]
    net = sum(pn)
    bnet = sum(t["proceeds"] - t["cost"] for t in base)
    eq, peak, dd = START, START, 0.0
    for t in sorted(mom, key=lambda t: t["closed"]):
        eq += t["proceeds"] - t["cost"]; peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
    baseline_ok = len(base) >= MIN_CLOSED and draws_multi >= 20
    checks = {"net_after_costs_gt_0": net > 0, "net_without_best_gt_0": (net - max(pn)) > 0 if pn else False,
              "beats_baseline": baseline_ok and net > bnet, "baseline_testable": baseline_ok,
              "max_drawdown_lt_30pct": dd < 0.30, "no_trade_gt_30pct_of_net": net > 0 and max(pn) <= 0.30 * net}
    return all(v for k, v in checks.items() if k != "baseline_testable"), checks, {"net": net, "baseline_net": bnet,
                                                                                    "max_drawdown": dd, "closed": len(mom)}


def status(state_dir="state", logs="logs", now=None):
    now = now or datetime.now(timezone.utc)
    rs = runs(logs)
    if not rs:
        return {"phase": "VALIDATION", "action": "COLLECT", "note": "no run yet"}
    ph_path = os.path.join(state_dir, "v5_phase.json")
    ph = json.load(open(ph_path)) if os.path.exists(ph_path) else {"phase": "VALIDATION", "since": rs[0]["started_utc"]}
    if ph["phase"] == "DONE":
        return {**ph, "action": "STOP"}
    since = _t(ph["since"])
    cov = coverage(rs, since)
    days_ok = sorted(d for d, mins in cov.items() if mins >= MIN_DAY_MIN)
    mom, base = trades(state_dir, V5, since), trades(state_dir, V5B, since)
    draws = [d for m in rs for d in m.get("baseline_draws", []) if _t(d["ts"]) >= since]
    multi = sum(1 for d in draws if d["eligible_options"] > 1)
    st = {"phase": ph["phase"], "since": ph["since"], "closed": len(mom), "baseline_closed": len(base),
          "days_with_12h_coverage": len(days_ok), "coverage_min_per_day": {d: round(v) for d, v in sorted(cov.items())},
          "draws_with_more_than_one_option": multi, "elapsed_days": round((now - since).total_seconds() / 86400, 2)}
    if len(mom) >= MIN_CLOSED and len(days_ok) >= MIN_DAYS:
        ok, checks, nums = criterion(mom, base, multi)
        st.update(checks=checks, numbers=nums)
        if ph["phase"] == "VALIDATION" and ok:
            new = {"phase": "REPLICATION", "since": now.isoformat(timespec="seconds"), "validation": st}
            json.dump(new, open(ph_path, "w"), indent=1)
            return {**st, "action": "COLLECT", "verdict": "VALIDATION PASSED -> replication starts (same rules)"}
        verdict = ("AVANTAJ PAPER REPLICAT" if ok else "FARA AVANTAJ IN REPLICARE") if ph["phase"] == "REPLICATION" \
            else "FARA AVANTAJ IN TEST"
        json.dump({"phase": "DONE", "verdict": verdict, "final": st, "previous": ph}, open(ph_path, "w"), indent=1)
        return {**st, "action": "STOP", "verdict": verdict}
    if (now - since) >= timedelta(days=MAX_DAYS):
        verdict = f"INSUFICIENT ({ph['phase'].lower()}: < {MIN_CLOSED} closed or < {MIN_DAYS} covered days in {MAX_DAYS} days)"
        json.dump({"phase": "DONE", "verdict": verdict, "final": st, "previous": ph}, open(ph_path, "w"), indent=1)
        return {**st, "action": "STOP", "verdict": verdict}
    return {**st, "action": "COLLECT"}


if __name__ == "__main__":
    s = status()
    print(json.dumps(s, indent=1))
    sys.exit(3 if s["action"] == "STOP" else 0)
