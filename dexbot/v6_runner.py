"""Independent paper runner for v6 (own balance, positions, entries, exits; own quotes and sell simulations at v6 sizes).
Separate from v5: nothing here changes the v5 code path or data. Writes per cycle the full decision record (raw signal
inputs, safety values, verdict) so a later audit does not depend on aggregated reasons."""
import dataclasses
import json
import os
import random
import time
import urllib.request
from datetime import datetime, timezone

from .agents import Sizer
from .alert import evaluate
from .bot import STALE_S, Bot
from .collector import _decision, load_state, save_state
from .execsim import buy_quote, sell_simulation
from .live import GT, LiveFeed
from .signals_free import WalletBuyWatch, load_wallets
from .v3 import SizedExecutor
from .v6 import (DevLpWatch, SessionEquity, V6RugCheck, V6Scanner, WalletSignals, lp_burned_onchain, pullback_from_candles,
                 recap, session_of)

V6, V6B = "capture-v6", "random-baseline-v6"


class V6EntryChecker:
    """Entry SELL_SIMULATION for the token amount the position would actually buy at its v6 size."""

    def __init__(self, size_for):
        self.size_for, self.cache, self.last_detail = size_for, {}, {}
        self.entry = {"OK": 0, "FAIL": 0, "UNKNOWN": 0}

    def check(self, p):
        hit = self.cache.get(p.addr)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        usd = self.size_for(p)
        if usd <= 0:
            st, det = "UNKNOWN", "no size available (limits / cash)"
        else:
            q = buy_quote(p.addr, usd)
            if q["status"] != "OK":
                st, det = q["status"], f"stage {q['stage']}: {q['detail']}"
            else:
                r = sell_simulation(p.addr, p.sell_holders, q["tokens"])
                st, det = r["status"], f"stage {r['stage']}: {r['detail']} (for {q['tokens']:,.0f} tokens = ${usd:,.2f})"
        self.entry[st] += 1
        self.cache[p.addr] = (time.time(), st)
        self.last_detail[p.addr] = det
        return st

    def fresh(self, p):
        return "UNKNOWN"


class V6Feed(LiveFeed):
    """LiveFeed + pullback from GeckoTerminal candles for +300 % candidates (cached 60 s)."""

    def __init__(self, cfg, wallet_hook=None):
        super().__init__(cfg, wallet_hook=wallet_hook, gate_cfgs=[cfg])
        self._pb, self._lp = {}, {}

    def rpc(self, method, params):
        from .sellsim import RPC, http_json
        s, r = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if s != 200 or not isinstance(r, dict) or "result" not in r:
            raise RuntimeError(f"rpc {method} http={s}")
        return r["result"]

    def _to_pair(self, a, r):
        p = super()._to_pair(a, r)
        lp_mint = (p.lp_mint_by_market or {}).get(p.pair_addr) if p.pair_addr else None
        if lp_mint:                                           # LP burn read on chain for the traded pool (cached)
            hit = self._lp.get(lp_mint)
            if not hit or time.time() - hit[0] > (600 if hit[1] is not None else 60):
                hit = (time.time(), lp_burned_onchain(self.rpc, lp_mint))
                self._lp[lp_mint] = hit
            p.lp_burned = hit[1]
        if p.price_1h_ago and p.price / p.price_1h_ago >= self.cfg.no_chase_mult and p.pair_addr:
            hit = self._pb.get(p.pair_addr)
            if not hit or time.time() - hit[0] > 60:
                try:
                    req = urllib.request.Request(f"{GT}/pools/{p.pair_addr}/ohlcv/minute?aggregate=5&limit=12",
                                                 headers={"User-Agent": "dexbot/0.1", "Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as x:
                        c = json.load(x)["data"]["attributes"]["ohlcv_list"]
                    hit = (time.time(), pullback_from_candles(p.price, c))
                except Exception:
                    hit = (time.time(), None)                 # unknown -> treated as no pullback (strict)
                self._pb[p.pair_addr] = hit
            p.pullback_pct = hit[1]
        return p


def _bot(cfg, feed, now, out_dir, sid):
    b = Bot(cfg, feed, now, logdir=out_dir, tag=sid, strategy_id=sid)
    b.eq = SessionEquity(cfg.start_balance, now, b.log)
    b.scanner, b.rug, b.sizer = V6Scanner(cfg), V6RugCheck(cfg), Sizer(cfg)
    b.ex = SizedExecutor(cfg, b.log, b.eq)

    def size_for(p, b=b):
        equity = b.eq.cash + b.ex.value({})
        return b.sizer.size(equity, b.eq.cash - b.ex.reserved(), len(b.scanner.signals(p)), len(b.ex.positions), b.eq.loss_streak)[0]

    b.sell_checker = V6EntryChecker(size_for)
    b.ex.sell_gate = None
    return b


def run_v6(cfg, rules, fp, minutes, interval_s=30, out_dir="logs", state_dir="state", resume=False, seed=20261120,
           wallets_path=None, feed_factory=None, rpc=None, no_entry_last_min=5):
    os.makedirs(out_dir, exist_ok=True); os.makedirs(state_dir, exist_ok=True)
    wallets, wstatus = load_wallets(wallets_path) if wallets_path else load_wallets()
    ws = WalletSignals(wallets)
    wwatch = WalletBuyWatch(wallets) if wallets else None
    now = datetime.now()
    feed = (feed_factory or V6Feed)(cfg, wallet_hook=ws.hook)
    mom, base = _bot(cfg, feed, now, out_dir, V6), _bot(cfg, feed, now, out_dir, V6B)
    base.entries_enabled = False
    if resume:
        for b in (mom, base):
            load_state(b, os.path.join(state_dir, f"paper_state_{b.strategy_id}.json"))
    from .sellsim import http_json
    watch = DevLpWatch(rpc or (lambda m, p: (http_json("https://api.mainnet-beta.solana.com",
                                                        {"jsonrpc": "2.0", "id": 1, "method": m, "params": p})[1] or {}).get("result")))
    rng = random.Random(seed + (int(time.time()) if resume else 0))
    for b in (mom, base):
        b.log.log(now, "INFO", f"strategy={b.strategy_id} fingerprint={fp} wallets={wstatus} rules={json.dumps(rules, sort_keys=True)} "
                               f"resumed={resume} (PAPER ONLY)")
    scanner, rug = V6Scanner(cfg), V6RugCheck(cfg)
    jpath = os.path.join(out_dir, f"v6_collect_{now:%Y%m%d_%H%M%S}.jsonl")
    out = open(jpath, "a", buffering=1)
    t_start, end, cycles, draws, recaps, evaluable = time.time(), time.time() + minutes * 60, 0, [], [], set()
    last_session = session_of(datetime.now(timezone.utc))
    session_start_balance = mom.eq.cash
    while time.time() < end:
        t0 = time.time(); now = datetime.now(); now_utc = datetime.now(timezone.utc)
        sess = session_of(now_utc)
        if last_session and sess != last_session:             # 07:00 recap of the session that just ended
            ts = [t for t in mom.ex.trades if session_of(t.closed.replace(tzinfo=timezone.utc)) == last_session]
            rc = {"session": last_session, **recap(ts, session_start_balance, mom.eq.cash)}
            recaps.append(rc); mom.log.log(now, "RECAP", json.dumps(rc, default=str))
        if sess and sess != last_session:
            session_start_balance = mom.eq.cash
        last_session = sess
        if wwatch:
            for ev in wwatch.poll(price_now=lambda m: (feed.get(m).price if feed.get(m) else None)):
                ws.add(ev)
                mom.log.log(now, "WALLET_BUY", json.dumps(dataclasses.asdict(ev)))
        for b in (mom, base):
            b.ex.cycle_t = t0
            for a in b.ex.positions:
                feed.pin(a)
        before = set(mom.traded)
        mom.entries_enabled = sess is not None and (end - t0) > no_entry_last_min * 60   # entries only inside 23:00-07:00 Bucharest
        try:
            feed.step(now)                                      # a malformed row must cost one cycle, never the run
        except Exception as e:
            mom.log.log(now, "ERROR", f"feed step failed, cycle skipped: {repr(e)[:180]}")
        for a in list(feed.cache):                               # exit triggers for held positions (not fills)
            if a in mom.ex.positions or a in base.ex.positions:
                watch.apply(feed.cache[a])
        while watch.events:
            kind, mint, info = watch.events.pop(0)
            mom.log.log(now, kind, f"mint={mint} {info}")
        for b in (mom, base):
            try:
                b.tick(now, step=False)
            except Exception as e:
                b.log.log(now, "ERROR", repr(e)[:200])
        for a in set(mom.traded) - before:
            p = feed.get(a)
            if p:
                watch.on_entry(p)
        cycles += 1
        snap = [p for p in feed.snapshot() if p.age_s <= cfg.max_age_s and (p.fetched_at is None or time.time() - p.fetched_at <= STALE_S)]
        eligible = []
        for p in snap:
            verdict, why, sig = evaluate(cfg, scanner, rug, p, skip_sell=True)
            if scanner.universe_fail(p) is None and rug.check(p, skip_sell=True)[0]:
                eligible.append(p); evaluable.add(p.addr)
            verdict, why = _decision(mom, mom.sell_checker, p, verdict, why)
            out.write(json.dumps({"variant": V6, "ts": now_utc.isoformat(timespec="seconds"), "session": sess, "mint": p.addr,
                                  "pair": p.pair_addr, "dex_id": p.dex_id, "age_s": round(p.age_s), "price": p.price,
                                  "liquidity": p.liquidity, "vol5m": p.vol5m, "vol_avg5m_1h": p.vol_avg5m_1h,
                                  "buys5m": p.buys5m, "sells5m": p.sells5m, "holders": p.holders, "holders_10m": p.holders_10m,
                                  "price_1h_ago": p.price_1h_ago, "pullback": p.pullback_pct, "smart_buyers_10m": p.smart_buyers_10m,
                                  "kol_bought": p.kol_bought, "dev_pct": p.dev_pct, "top10_pct_raw": p.top10_pct,
                                  "bundled": p.bundled, "lp_traded_pool": (p.lp_by_market or {}).get(p.pair_addr), "lp_burned": p.lp_burned,
                                  "mint_frozen": p.mint_frozen, "mint_auth_open": p.mint_auth_open, "signals": sig,
                                  "verdict": verdict, "reason": why}) + "\n")
        for addr in sorted(set(mom.traded) - before):            # paired random baseline
            pool = [p for p in eligible if p.addr not in base.traded and p.addr not in base.ex.positions]
            draws.append({"ts": now.isoformat(timespec="seconds"), "momentum_entry": addr, "eligible_options": len(pool)})
            rng.shuffle(pool)
            for p in pool[:3]:
                if base.sell_checker.check(p) != "OK":
                    continue
                q = feed.refresh(p.addr)
                if q is None or not q.price:
                    continue
                usd = base.sell_checker.size_for(p)
                if usd > 0:
                    base.traded.add(p.addr)
                    base.ex.enter(dataclasses.replace(p, price=q.price, liquidity=q.liquidity or p.liquidity), usd, ["RANDOM"], now)
                break
        for b in (mom, base):
            save_state(b, os.path.join(state_dir, f"paper_state_{b.strategy_id}.json"))
        time.sleep(max(0, interval_s - (time.time() - t0)))
    for b in (mom, base):
        b.log.log(datetime.now(), "INFO", f"end cycles={cycles} open={len(b.ex.positions)} closed={len(b.ex.trades)} "
                                          f"failed_exits={b.ex.failed_exits} (open positions stay open; no sale is invented)")
        save_state(b, os.path.join(state_dir, f"paper_state_{b.strategy_id}.json"))
        b.log.close()
    out.close()
    meta = {"strategy": V6, "baseline": V6B, "fingerprint": fp, "rules": rules, "cycles": cycles, "resumed": resume,
            "minutes_planned": minutes, "minutes_run": round((time.time() - t_start) / 60, 1), "stop": None,
            "pools_discovered": len(getattr(feed, "watch", [])), "evaluable_pairs": len(evaluable),
            "discovery_errors": {k: dict(v) for k, v in getattr(feed, "disc_errors", {}).items()},
            "started_utc": datetime.fromtimestamp(t_start, timezone.utc).isoformat(timespec="seconds"),
            "ended_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "wallets": wstatus,
            "sha": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"), "jsonl": os.path.basename(jpath),
            "baseline_draws": draws, "recaps": recaps, "entry_simulations": mom.sell_checker.entry,
            "logs": {V6: os.path.basename(mom.log.path), V6B: os.path.basename(base.log.path)}}
    json.dump(meta, open(os.path.join(out_dir, "v6_meta.json"), "w"), indent=1)
    os.makedirs(os.path.join(out_dir, "runs"), exist_ok=True)
    json.dump(meta, open(os.path.join(out_dir, "runs", f"{V6}_{datetime.fromtimestamp(t_start, timezone.utc):%Y%m%dT%H%M%S%fZ}_"
                                                       f"{os.environ.get('GITHUB_RUN_ID', 'local')}.json"), "w"), indent=1)
    return mom, base
