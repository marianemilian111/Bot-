#!/usr/bin/env python3
"""Short PAPER probe: breakout immediate vs retest (rules docs/BREAKOUT_RULES.md @ 375b78f, unchanged). NOT launched
without the owner's approval. Read-only: DexScreener / GeckoTerminal (discovery + observations), RugCheck (public holders
for the sell simulation), Jupiter lite-api quotes, Solana public RPC simulateTransaction. Nothing is signed or sent.

* Selection: a pool is tracked from the first observation where liquidity >= $40k and 5 m volume >= $25k (max --max-tracked);
  the signal itself re-checks both thresholds on the decision observation (Detector).
* Observations: one per pool per fresh DexScreener fetch (fetched_at); cached values are not written again.
* BUY quote ($14) only when an eligible signal exists, at decision + 0 / 30 / 60 s; SELL quote + simulation of the exact
  amount only at an exit trigger + latency. Missing or late quote -> no entry / no exit.
* Caps: --minutes total, --max-buy quote requests, --max-sell sell requests. No new entries in the last 27 min
  (time stop 25 min + 60 s latency + margin) so positions can close inside the window; such signals are counted apart.
* Output (--out): obs.jsonl, decisions.jsonl, quotes.jsonl, meta.json, result.json (offline replay of the recorded files
  with scripts/breakout_compare.py = the reported result)."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.breakout import RULES, Detector, Engine, LiveQuotes, Obs  # noqa: E402

LATENCIES = (0, 30, 60)
CUTOFF_MIN = 27


def _dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=str)


def eligible_pool(p):
    return (p.liquidity or 0) >= RULES["min_liq"] and (p.vol5m or 0) >= RULES["min_vol5m"] and (p.price or 0) > 0


def run(out, minutes, max_buy, max_sell, max_tracked, feed, buy_fn, sell_fn, clock=None, sleep=time.sleep, cycle_s=30,
        discover=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    os.makedirs(out, exist_ok=True)
    start = clock()
    end, cutoff = start + timedelta(minutes=minutes), start + timedelta(minutes=minutes - CUTOFF_MIN)
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
    except Exception:
        sha = None
    meta = {"code_sha": sha, "rules_commit": "375b78f", "started_utc": start.isoformat(), "end_utc": end.isoformat(),
            "entry_cutoff_utc": cutoff.isoformat(), "max_buy_requests": max_buy, "max_sell_requests": max_sell,
            "max_tracked": max_tracked, "latencies_s": list(LATENCIES), "paper_only": True, "signing": False}
    _dump(os.path.join(out, "meta.json"), meta)
    lq = LiveQuotes(os.path.join(out, "quotes.jsonl"), buy_fn, sell_fn,
                    lambda m: (feed.get(m).sell_holders if feed.get(m) else None), max_buy, max_sell)
    dets = {v: Detector(v) for v in ("immediate", "retest")}
    engines = {(v, lat): Engine(lq, lat, entry_cutoff=cutoff) for v in dets for lat in LATENCIES}
    tracked, last = [], {}
    cycles = {"ok": 0, "partial": 0, "failed": 0}

    def tick_all():
        for e in engines.values():
            e.tick(clock())

    while clock() < end:
        t0 = clock()
        if discover:
            try:
                discover(feed)
            except Exception:
                pass
        try:
            feed.step(t0)
            st = getattr(feed, "step_status", {}) or {}
            cyc = "ok" if not st.get("failed") else ("failed" if st.get("failed") == st.get("chunks") else "partial")
        except Exception as e:
            st, cyc = {"error": type(e).__name__}, "failed"
        cycles[cyc] += 1
        with open(os.path.join(out, "cycles.jsonl"), "a") as f:
            f.write(json.dumps({"ts": t0.isoformat(), "feed": cyc, "chunks": st.get("chunks"), "failed_chunks": st.get("failed"),
                                "error": st.get("error"), "tracked": len(tracked)}) + "\n")
        for p in feed.snapshot():
            if p.addr not in tracked and len(tracked) < max_tracked and eligible_pool(p):
                tracked.append(p.addr)
                feed.pin(p.addr)
        for m in tracked:
            p = feed.get(m)
            if not p or not p.fetched_at or last.get(m) == p.fetched_at or not p.price:
                continue
            last[m] = p.fetched_at
            o = Obs(datetime.fromtimestamp(p.fetched_at, timezone.utc), m, float(p.price), float(p.vol5m or 0), p.liquidity)
            with open(os.path.join(out, "obs.jsonl"), "a") as f:
                f.write(json.dumps({"ts": o.ts.isoformat(), "mint": m, "price": o.price, "vol5m": o.vol5m,
                                    "liquidity": o.liquidity, "cycle_ts": t0.isoformat(), "cycle_feed": cyc}) + "\n")
            for v, det in dets.items():
                d = det.feed(o)
                if d:
                    with open(os.path.join(out, "decisions.jsonl"), "a") as f:
                        f.write(json.dumps({**d, "ts": d["ts"].isoformat(), "breakout_ts": d["breakout_ts"].isoformat()}) + "\n")
                    for lat in LATENCIES:
                        engines[(v, lat)].on_decision(d)
            tick_all()
            for e in engines.values():
                e.on_obs(o)
            tick_all()
        while clock() < min(end, t0 + timedelta(seconds=cycle_s)):          # resolve due requests between cycles
            sleep(5)
            tick_all()
    for _ in range(int((60 + RULES["quote_tolerance_s"]) / 5) + 1):      # let last due requests resolve
        tick_all()
        sleep(5)
    meta.update(ended_utc=clock().isoformat(), cycles=cycles, tracked=len(tracked), buy_requests=lq.used_buy,
                sell_requests=lq.used_sell, quote_events=dict(lq.events),
                live_engine={f"{v}/{lat}": {k: x for k, x in e.summary().items() if k != "skipped"} for (v, lat), e in engines.items()})
    _dump(os.path.join(out, "meta.json"), meta)
    return meta


NOT_TARGETS = {"So11111111111111111111111111111111111111112",          # SOL is what we pay with
               "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",          # USDC
               "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}          # USDT


def gt_top_discovery(every_s=120):
    """Discovery only (not a signal): GeckoTerminal Solana pools with the highest 24 h volume, so selection starts
    from pools likely to pass the thresholds. DexScreener observations decide eligibility; SOL/USDC/USDT excluded
    (cannot be bought with SOL / no breakout meaning)."""
    last = {"t": 0.0}

    def run_(feed):
        if time.time() - last["t"] < every_s:
            return
        last["t"] = time.time()
        for pool, mint, dex, ts in feed._gt_pools("pools?page=1&sort=h24_volume_usd_desc") or []:
            if mint not in NOT_TARGETS and mint not in feed.watch:
                feed.watch.insert(0, mint)
                feed.disc_src[mint].add("gt_top_volume")
    return run_


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=int, default=110)
    ap.add_argument("--max-buy", type=int, default=60)
    ap.add_argument("--max-sell", type=int, default=120)
    ap.add_argument("--max-tracked", type=int, default=60)
    a = ap.parse_args()
    from dexbot.config import Config
    from dexbot.execsim import buy_quote, sell_simulation
    from dexbot.live import LiveFeed
    cfg = Config()
    cfg.max_age_s = 10 ** 9                     # breakout rules have no age limit (discovery only)
    run(a.out, a.minutes, a.max_buy, a.max_sell, a.max_tracked, LiveFeed(cfg), buy_quote, sell_simulation,
        discover=gt_top_discovery())
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(a.out, "meta.json")) as f:
        m = json.load(f)
    args = [sys.executable, os.path.join(here, "breakout_compare.py"), "--entry-cutoff", m["entry_cutoff_utc"],
            "--cycles", os.path.join(a.out, "cycles.jsonl"),
            "--quotes", os.path.join(a.out, "quotes.jsonl"), os.path.join(a.out, "obs.jsonl")]
    if not os.path.exists(os.path.join(a.out, "obs.jsonl")):
        open(os.path.join(a.out, "obs.jsonl"), "w").close()
    if not os.path.exists(os.path.join(a.out, "quotes.jsonl")):
        open(os.path.join(a.out, "quotes.jsonl"), "w").close()
    res = subprocess.run(args, capture_output=True, text=True)
    with open(os.path.join(a.out, "result.json"), "w") as f:
        f.write(res.stdout or res.stderr)
    print(res.stdout or res.stderr)


if __name__ == "__main__":
    main()
