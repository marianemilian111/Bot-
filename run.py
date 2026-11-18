#!/usr/bin/env python3
"""python3 run.py --sim              fast simulated night (23:00 -> 07:00), paper
   python3 run.py --live             real-time, live market data, PAPER fills, until 07:00"""
import argparse
from datetime import datetime

from dexbot.bot import run
from dexbot.config import Config

ap = argparse.ArgumentParser()
g = ap.add_mutually_exclusive_group(required=True)
g.add_argument("--sim", action="store_true")
g.add_argument("--live", action="store_true")
ap.add_argument("--balance", type=float, default=1000.0)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--start", default="23:00", help="sim start time HH:MM")
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()

cfg = Config(start_balance=a.balance)
if a.sim:
    from dexbot.sim import SimFeed
    h, m = map(int, a.start.split(":"))
    start = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    run(cfg, lambda t: SimFeed(cfg, t, seed=a.seed), "SIM/PAPER", start, realtime=False, quiet=a.quiet)
else:
    from dexbot.live import LiveFeed
    run(cfg, lambda t: LiveFeed(cfg), "LIVE-DATA/PAPER", datetime.now(), realtime=True, dash_every_s=30)
