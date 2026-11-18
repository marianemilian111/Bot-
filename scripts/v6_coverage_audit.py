#!/usr/bin/env python3
"""Read-only coverage audit for the v6 collection (does not change the run, the gate or the data).

Compares, per 23:00-07:00 Bucharest session:
  gate coverage  = minutes between run start and end (what scripts/v6_gate.py counts, from logs/runs/*.json)
  cycle evidence = cycles that wrote decision rows (distinct row timestamps in v6_collect_*.jsonl)
  failed cycles  = cycles whose whole feed step raised (ERROR "feed step failed" in the Momentum log)
  max supported  = (cycles with rows - failed cycles) x interval
Limit that cannot be removed from these files: a DexScreener chunk that fails inside LiveFeed.step is skipped silently and
rows carry no fetch time, so a cycle can reuse cached data (<= 120 s old) under a new timestamp. Such cycles cannot be
told apart from fresh ones -> the supported figure is an UPPER bound, and coverage is NEVERIFICAT whenever this matters.
Usage: python scripts/v6_coverage_audit.py <dir with logs/ and state/ from paper-data-v6>"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.v6 import session_of  # noqa: E402

INTERVAL_MIN, MIN_SESSION_MIN = 0.5, 360


def utc(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def audit(d):
    logs = os.path.join(d, "logs")
    runs = [json.load(open(f)) for f in glob.glob(os.path.join(logs, "runs", "capture-v6_*.json"))]
    gate = {}
    for m in runs:
        t, b = utc(m["started_utc"]), utc(m["ended_utc"])
        while t < b:
            s = session_of(t)
            if s:
                gate[s] = gate.get(s, 0) + min(1.0, (b - t).total_seconds() / 60)
            t += timedelta(minutes=1)
    ts_rows = set()
    for f in glob.glob(os.path.join(logs, "v6_collect_*.jsonl")):
        for line in open(f):
            ts_rows.add(json.loads(line)["ts"])
    failed = []
    for f in glob.glob(os.path.join(logs, "night_capture-v6_*.log")):
        for line in open(f):
            if "feed step failed" in line:
                failed.append(utc(line[:19].replace(" ", "T")))
    per = {}
    for ts in ts_rows:
        s = session_of(utc(ts))
        if s:
            per.setdefault(s, {"cycles_with_rows": 0, "failed": 0})["cycles_with_rows"] += 1
    for t in failed:
        s = session_of(t)
        if s:
            per.setdefault(s, {"cycles_with_rows": 0, "failed": 0})["failed"] += 1
    out = {"runs_in_gate": [m.get("run_id") for m in runs], "sessions": {}}
    for s in sorted(set(gate) | set(per)):
        g = round(gate.get(s, 0))
        c = per.get(s, {"cycles_with_rows": 0, "failed": 0})
        sup = max(0, c["cycles_with_rows"] - c["failed"]) * INTERVAL_MIN
        out["sessions"][s] = {"gate_minutes": g, "cycles_with_rows": c["cycles_with_rows"], "failed_feed_cycles": c["failed"],
                              "max_supported_minutes": sup, "gate_counts_session": g >= MIN_SESSION_MIN,
                              "supported_upper_bound_counts": sup >= MIN_SESSION_MIN,
                              "verdict_sensitive": (g >= MIN_SESSION_MIN) != (sup >= MIN_SESSION_MIN)}
    out["silent_chunk_failures_detectable"] = False
    out["status"] = ("BLOCAT - ACOPERIRE NEVERIFICATA" if any(v["gate_counts_session"] for v in out["sessions"].values())
                     else "no session reaches the gate threshold yet")
    return out


if __name__ == "__main__":
    print(json.dumps(audit(sys.argv[1] if len(sys.argv) > 1 else "."), indent=1))
