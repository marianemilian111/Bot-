#!/usr/bin/env python3
"""Read-only A/B candidate check (no trading, no paper entries, does not touch v3).
A = migrated pools aged 6-72 h found by SIGNALS_FREE (GeckoTerminal new pools + DexScreener token-pairs) but not by the
    v3 discovery (GeckoTerminal pools by 24 h volume p1-3 + trending 24 h).
B = pools older than 72 h in the v3 discovery list (v3 excludes them only by its age window).
For each group: liquidity >= $40k AND 5 m volume >= $25k in the same snapshot, safety filters (non-sell), and observable
signals (VOL_SPIKE, BUY_RATIO; HOLDER_GROWTH = UNKNOWN unless a second snapshot >= 10 min later exists).
Two snapshots ~10 min apart. Output: JSON to stdout."""
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.agents import RugCheck, Scanner  # noqa: E402
from dexbot.live import CURVE_DEX, LiveFeed  # noqa: E402
from dexbot.sellsim import http_json  # noqa: E402
from dexbot import signals_free as sf  # noqa: E402
from dexbot.v3 import MAJOR_SYMBOLS  # noqa: E402
from dexbot.variants import V1, make  # noqa: E402

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
H = 3600


def gt(path):
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{GT}/{path}", headers={"User-Agent": "dexbot/0.1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r).get("data") or []
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(35)
                continue
            raise
    return []


def v3_discovery():
    out = {}
    for path in ("pools?page=1&sort=h24_volume_usd_desc", "pools?page=2&sort=h24_volume_usd_desc",
                 "pools?page=3&sort=h24_volume_usd_desc", "trending_pools?page=1&duration=24h"):
        for x in gt(path):
            a, rel = x["attributes"], x["relationships"]
            dex, sym = rel["dex"]["data"]["id"], a["name"].split(" / ")[0].strip().upper()
            if any(c in dex for c in CURVE_DEX) or sym in MAJOR_SYMBOLS:
                continue
            mint = rel["base_token"]["data"]["id"].split("_", 1)[1]
            out[mint] = time.time() - calendar.timegm(time.strptime(a["pool_created_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        time.sleep(3)
    return out


def best_pairs(mints):
    res = {}
    mints = list(mints)
    for i in range(0, len(mints), 30):
        s, d = http_json("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(mints[i:i + 30]))
        for p in (d.get("pairs") or []) if isinstance(d, dict) else []:
            if p.get("chainId") != "solana" or not p.get("pairCreatedAt") or p.get("dexId") in sf.CURVE_DEX:
                continue
            a = p["baseToken"]["address"]
            if a in mints and (a not in res or ((p.get("liquidity") or {}).get("usd") or 0) > ((res[a].get("liquidity") or {}).get("usd") or 0)):
                res[a] = p
    return res


def snapshot(feed, mints, cfg):
    rows = {}
    for mint, r in best_pairs(mints).items():
        p = feed._to_pair(mint, r)
        rows[mint] = p
    return rows


def classify(p, cfg, second):
    sc, rug = Scanner(cfg), RugCheck(cfg)
    liqvol = (p.liquidity or 0) >= cfg.min_liq and p.vol5m >= cfg.min_vol5m
    nonage = sc.universe_fail(type(p)(**{**p.__dict__, "age_s": 0}))
    safe = nonage is None and rug.check(p, skip_sell=True)[0]
    sig = [s for s in sc.signals(p) if s != "HOLDER_GROWTH"]
    hg = "UNKNOWN" if (not second or p.holders is None or p.holders_10m is None) else ("YES" if "HOLDER_GROWTH" in sc.signals(p) else "NO")
    known = len(sig) + (1 if hg == "YES" else 0)
    return {"liq": p.liquidity, "vol5m": p.vol5m, "liq_and_vol": liqvol, "safety_pass": safe,
            "safety_reason": nonage or (rug.check(p, skip_sell=True)[1] if not safe else "ok"),
            "signals_known": sig + (["HOLDER_GROWTH"] if hg == "YES" else []), "holder_growth": hg, "n_known": known,
            "age_h": round(p.age_s / H, 1)}


def main():
    cfg = make(V1)
    feed = LiveFeed(cfg)
    t0 = time.time()
    v3 = v3_discovery()
    w = sf.MigrationWatch()
    mig = w.poll()
    A = {m.mint: m for m in mig if 6 <= m.age_h() <= 72 and m.mint not in v3}
    A_all_ages = {m.mint: round(m.age_h(), 2) for m in mig}
    B = [m for m, age in v3.items() if age > 72 * H]
    groups = {"A": list(A), "B": B}
    s1 = {g: snapshot(feed, ms, cfg) for g, ms in groups.items()}
    wait_until = time.time() + 10 * 60 + 15
    while time.time() < wait_until:
        time.sleep(20)
    s2 = {g: snapshot(feed, ms, cfg) for g, ms in groups.items()}
    out = {"t0": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(t0)), "v3_discovery_tokens": len(v3),
           "signals_free_migration_events": [{"mint": m.mint, "status": m.status, "age_h": round(m.age_h(), 2),
                                              "curve": m.curve_dex, "amm": m.amm_dex, "in_v3": m.mint in v3} for m in mig],
           "migration_errors": w.errors, "groups": {}}
    for g in groups:
        res = []
        for mint in groups[g]:
            a = classify(s1[g][mint], cfg, False) if mint in s1[g] else None
            b = classify(s2[g][mint], cfg, True) if mint in s2[g] else None
            res.append({"mint": mint, "snap1": a, "snap2": b})
        out["groups"][g] = res
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
