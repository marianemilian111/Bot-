#!/usr/bin/env python3
"""Pre-registered stop logic for the INDEPENDENT v6 test (docs/PREREG_V6_INDEPENDENT.md). Same structure as the v5 gate:
VALIDATION -> REPLICATION -> DONE; >= 30 closed and >= 7 sessions (23:00-07:00 Bucharest) with >= 6 h coverage per
window; 14 days without a complete window -> INSUFICIENT. Drawdown limit: 30 % of the $70 start ($21)."""
import importlib.util
import os
import sys
from datetime import timedelta

_spec = importlib.util.spec_from_file_location("v5_gate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "v5_gate.py"))
G = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(G)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.v6 import session_of  # noqa: E402

G.V5, G.V5B, G.START, G.MIN_DAY_MIN = "capture-v6", "random-baseline-v6", 70.0, 360


def session_coverage(rs, since):
    """Coverage = 30-s cycles whose WHOLE feed step succeeded (`cycle_status` == ok), per session. A run without
    cycle_status cannot prove coverage and counts 0 (strict). Wall-clock run time is no longer counted."""
    cov = {}
    for m in rs:
        if "cycle_status" not in m:
            continue
        step = (m.get("interval_s") or 30) / 60
        for ts, stt in m["cycle_status"]:
            t = G._t(ts)
            s = session_of(t)
            if s and stt == "ok" and t >= since:
                cov[s] = cov.get(s, 0) + step
    return cov


def _wallclock_coverage_unused(rs, since):
    cov = {}
    for m in rs:
        a, b = max(G._t(m["started_utc"]), since), G._t(m["ended_utc"])
        t = a
        while t < b:
            nxt = min(b, t + timedelta(minutes=1))
            s = session_of(t)
            if s:
                cov[s] = cov.get(s, 0) + (nxt - t).total_seconds() / 60
            t = nxt
    return cov


G.coverage = session_coverage                 # coverage counted per session, not per UTC day
_orig_status = G.status


def status(state_dir="state", logs="logs", now=None):
    ph = os.path.join(state_dir, "v5_phase.json")
    alt = os.path.join(state_dir, "v6_phase.json")
    # keep the phase file separate from v5's
    if os.path.exists(alt) and not os.path.exists(ph):
        os.replace(alt, ph)
    try:
        return _orig_status(state_dir, logs, now)
    finally:
        if os.path.exists(ph):
            os.replace(ph, alt)


if __name__ == "__main__":
    import json
    s = status()
    print(json.dumps(s, indent=1))
    sys.exit(3 if s["action"] == "STOP" else 0)
