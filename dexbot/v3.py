"""momentum-v3-migrated (PAPER ONLY, separate from v1/v2) and its random baseline.

Hypothesis: migrated pools that are already liquid give more evaluable opportunities than launches under 6 h,
without lowering the liquidity floor.

Fixed before any run (also pinned by the fingerprint in variants.py):
* Discovery: GeckoTerminal only - pools sorted by 24 h volume (pages 1-3) and trending pools (24 h). No boosts, no
  profiles. Discovery chooses what to look at; it is never a buy signal.
* Universe: pool age 6 h < age <= 72 h, not a launch curve, base token not SOL / stablecoins; liquidity >= $40,000;
  5 m volume >= $25,000; >= 300 holders; mint + freeze authority closed; LP burned or locked >= 30 d; dev < 5 %;
  top 10 < 35 %; no bundled launch; SELL_SIMULATION at the real position size must PASS (UNKNOWN = reject).
* Entry: same Momentum signals as v1 (volume spike > 5x the 1 h average, buy ratio > 70 %, holder growth >= 10 % in 10 min;
  smart money / KOL unavailable), at least 2; no-chase rule as v1. Max 2 open positions, one entry per pair.
* Size: $50 per position (20 % of a $250 virtual balance), 2 parts of $25. Fills from Jupiter quotes at that size.
* Exits: as v1 - 40 % at 2x, 30 % at 4x, 25 % trailing on the rest, hard stop -12 %, time stop 25 min. An exit fills only
  when the sell simulation at that size passes; FAIL -> written off at $0 (shown separately); UNKNOWN -> retried next cycle.
* Baseline: each time Momentum enters, the baseline enters ONE pair drawn at random (seeded) from the pairs that passed the
  same universe + safety checks in that same cycle (signals ignored), with the same size, budget and exits."""
import dataclasses
import json
import os
import random
import time
from datetime import datetime, timezone

from .agents import Executor, RugCheck, Scanner
from .alert import evaluate
from .bot import STALE_S, Bot
from .collector import _decision, save_state
from .execsim import NET_FEE_USD, buy_quote, classify, sell_simulation
from .live import CURVE_DEX, GT, LiveFeed
from .variants import V3, V3B, V3_RULES, assert_v3, make

MAJOR_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT", "PYUSD", "USDS", "TTWO", "CBBTC", "WBTC", "WETH", "ETH", "JITOSOL", "MSOL"}


class V3Scanner(Scanner):
    def universe_fail(self, p):
        if p.age_s <= V3_RULES["min_age_s"]:
            return "younger than 6h (v3 window is 6-72h)"
        if p.age_s > self.cfg.max_age_s:
            return "older than 72h"
        return super().universe_fail(dataclasses.replace(p, age_s=0))   # remaining v1 checks, age already handled


class FixedSizer:
    def __init__(self, usd, max_open):
        self.usd, self.max_open, self.last = usd, max_open, "-"

    def size(self, equity, available, n_signals, open_count, loss_streak):
        if open_count >= self.max_open:
            return 0.0, f"max {self.max_open} open positions"
        if available < self.usd:
            return 0.0, f"free cash ${available:,.2f} < ${self.usd:,.0f}"
        self.last = f"${self.usd:,.0f} fixed"
        return self.usd, "fixed size"


class SizedExecutor(Executor):
    """Fills from quotes at the real size; exits only through a passing sell simulation at that size."""
    cycle_t = 0.0

    def _delay(self):
        return time.time() - self.cycle_t if self.cycle_t else float("nan")

    def _buy(self, pos, p, usd, tag, now):
        q = buy_quote(p.addr, usd)
        if q["status"] != "OK":
            self.log.log(now, "FILTERED", f"{p.symbol} mint={p.addr} {tag}: {q['stage']} {q['status']} "
                                          f"cat={classify(q['status'], q['stage'], q['detail'])} ({q['detail']})")
            return False
        move = q["fill_px"] / p.price - 1 if p.price else float("nan")
        if move > self.cfg.max_slippage:
            self.log.log(now, "FILTERED", f"{p.symbol} mint={p.addr} {tag}: cat=EXECUTION_LIMIT quoted fill {move:+.2%} vs "
                                          f"price after detection > {self.cfg.max_slippage:.1%} cap")
            return False
        self.eq.cash -= usd
        pos.cost += usd
        pos.tokens += q["tokens"]
        pos.tokens_bought += q["tokens"]
        pos.parts_done += 1
        pos.peak = max(pos.peak, p.price)
        self.log.log(now, "ENTER", f"{p.symbol} mint={p.addr} {tag} size=${usd:,.2f} price_after_detection={p.price:.8g} "
                                   f"quote_fill={q['fill_px']:.8g} ({move:+.2%}, quoted impact {q['quoted_impact']:.2%}) "
                                   f"tokens={q['tokens']:,.0f} (own quote for this ${usd:,.2f} tranche) delay={self._delay():.1f}s net_fee=${NET_FEE_USD} "
                                   f"signals={'+'.join(pos.signals)}")
        return True

    attempts_path = None                      # logs/exit_attempts.jsonl next to the bot log (set lazily)

    def _attempt(self, p, why, tokens, r, imputed):
        """One JSON line per exit attempt. Missing values are null (never NaN, never a zero stand-in)."""
        import math
        path = self.attempts_path or os.path.join(os.path.dirname(self.log.path) or ".", "exit_attempts.jsonl")
        def nz(x):
            return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x
        ev = {"ts_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "log": os.path.basename(self.log.path), "mint": p.addr, "symbol": p.symbol, "reason": why,
              "status": r["status"], "class": classify(r["status"], r["stage"], r["detail"]), "stage": r["stage"],
              "error": r.get("error") if r["status"] != "OK" else None,
              "tokens": tokens, "amount_raw": r.get("amount_raw"), "decimals": r.get("decimals"), "amount_unit": "token base units",
              "quote_out_lamports": r.get("quote_out_lamports"), "quote_unit": "lamports (SOL x 1e9)",
              "quote_impact": nz(r.get("quote_impact")), "sol_usd": nz(r.get("sol_usd")), "price_at_decision": nz(p.price),
              "proceeds_usd": nz(r.get("proceeds_usd")) if r["status"] == "OK" else None,
              "imputed_loss_usd": imputed, "delay_s": nz(self._delay())}
        with open(path, "a") as f:
            f.write(json.dumps(ev, allow_nan=False) + "\n")

    def _sell(self, pos, p, tokens, why, now):
        tokens = min(tokens, pos.tokens)
        if tokens <= 0:
            return False
        r = sell_simulation(p.addr, p.sell_holders, tokens)
        self._attempt(p, why, tokens, r, pos.avg_entry * tokens if r["status"] == "FAIL" else None)
        if r["status"] == "UNKNOWN":
            if (pos.addr, why, r["stage"]) not in self._deferred:
                self._deferred.add((pos.addr, why, r["stage"]))
                self.log.log(now, "EXIT_DEFERRED", f"{p.symbol} mint={p.addr} {why}: SELL_SIMULATION=UNKNOWN "
                                                   f"cat={classify('UNKNOWN', r['stage'], r['detail'])} stage={r['stage']} "
                                                   f"({r['detail']}) tokens={tokens:,.0f} price={p.price:.8g}; retried next cycle, no fill")
            return False
        if r["status"] == "FAIL":
            lost = pos.avg_entry * tokens
            pos.written_off += lost
            pos.tokens -= tokens
            pos.reasons.append("EXIT_FAILED")
            self.failed_exits += 1
            self.log.log(now, "EXIT_FAILED", f"{p.symbol} mint={p.addr} {why}: SELL_SIMULATION=FAIL "
                                             f"cat={classify('FAIL', r['stage'], r['detail'])} stage={r['stage']} ({r['detail']}) "
                                             f"tokens={tokens:,.0f} price={p.price:.8g} value_at_price=${tokens * p.price:,.2f}; "
                                             f"IMPUTED loss ${lost:,.2f} by the $0 rule (not a realised loss, not proof of a honeypot)")
            return True
        got = r["proceeds_usd"]
        part_cost = pos.avg_entry * tokens
        pos.tokens -= tokens
        pos.proceeds += got
        self.eq.cash += got
        pos.reasons.append(why)
        mtm = tokens * p.price
        self.log.log(now, "EXIT", f"{p.symbol} mint={p.addr} {why} sold={tokens / pos.tokens_bought:.0%} tokens={tokens:,.0f} (quoted and simulated exactly) "
                                  f"price_at_decision={p.price:.8g} value_at_price=${mtm:,.2f} simulated_proceeds=${got:,.2f} "
                                  f"({(got / mtm - 1) * 100 if mtm else float('nan'):+.2f}% vs price, quoted impact "
                                  f"{r['quoted_impact']:.2%}) delay={self._delay():.1f}s pnl={got - part_cost:+,.2f}")
        return True


class SizedEntryChecker:
    """Entry-time SELL_SIMULATION for the token amount the position would actually hold."""

    def __init__(self, usd):
        self.usd, self.cache, self.last_detail = usd, {}, {}
        self.entry = {"OK": 0, "FAIL": 0, "UNKNOWN": 0}
        self.exit = {"OK": 0, "FAIL": 0, "UNKNOWN": 0}

    def check(self, p):
        hit = self.cache.get(p.addr)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        q = buy_quote(p.addr, self.usd)
        if q["status"] != "OK":
            st, det = q["status"], f"cat={classify(q['status'], q['stage'], q['detail'])} stage {q['stage']}: {q['detail']}"
        else:
            r = sell_simulation(p.addr, p.sell_holders, q["tokens"])
            st, det = r["status"], (f"cat={classify(r['status'], r['stage'], r['detail'])} stage {r['stage']}: {r['detail']} "
                                    f"(for {q['tokens']:,.0f} tokens = ${self.usd:,.0f})")
        self.entry[st] += 1
        self.cache[p.addr] = (time.time(), st)
        self.last_detail[p.addr] = det
        return st

    def fresh(self, p):           # not used: SizedExecutor runs its own exit simulation
        return "UNKNOWN"


def _wire(bot, cfg):
    bot.scanner = V3Scanner(cfg)
    bot.sizer = FixedSizer(V3_RULES["position_usd"], cfg.max_open)
    ex = SizedExecutor(cfg, bot.log, bot.eq)
    bot.ex = ex
    return bot


class V3Feed(LiveFeed):
    """GeckoTerminal-only discovery of migrated pools in the 6-72 h window (no boosts, no profiles)."""
    PAGES = ("pools?page=1&sort=h24_volume_usd_desc", "pools?page=2&sort=h24_volume_usd_desc",
             "pools?page=3&sort=h24_volume_usd_desc", "trending_pools?page=1&duration=24h")

    def __init__(self, cfg):
        super().__init__(cfg, gate_cfgs=[cfg])
        self.v3_pools, self.turn = {}, 0

    def _discover(self):
        if time.time() - self.last_discovery < 40:        # ~1.5 GeckoTerminal calls / min (public limit ~30 / min, shared)
            return
        self.last_discovery = time.time()
        path = self.PAGES[self.turn % len(self.PAGES)]
        self.turn += 1
        src = "gt_" + path.split("?")[0] + ("_p" + path.split("page=")[1][0] if "page=" in path else "")
        try:
            data = _gt(path)
        except Exception as e:
            self.disc_errors[src][str(e)[:40]] += 1
            return
        for x in data:
            try:
                a, rel = x["attributes"], x["relationships"]
                dex = rel["dex"]["data"]["id"]
                mint = rel["base_token"]["data"]["id"].split("_", 1)[1]
                base_sym = a["name"].split(" / ")[0].strip().upper()
                age = time.time() - datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if any(c in dex for c in CURVE_DEX) or base_sym in MAJOR_SYMBOLS:
                continue
            if not (V3_RULES["min_age_s"] < age <= self.cfg.max_age_s):
                continue
            if a["address"] not in self.v3_pools:              # dedupe by pool address
                self.v3_pools[a["address"]] = (mint, dex, src)
            self.disc_src[mint].add(src)
        pinned = [a for a in self.watch if a in self.pinned]
        mints = [m for (m, _, _) in self.v3_pools.values()]
        self.watch = list(dict.fromkeys(pinned + mints))[:300]


def _gt(path):
    import urllib.request
    req = urllib.request.Request(f"{GT}/{path}", headers={"User-Agent": "dexbot/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("data") or []


def run_v3(minutes, early_stop_min=20, interval_s=30, out_dir="logs", state_dir="state"):
    cfg = make(V3, V3_RULES["start_balance"])
    fp = assert_v3(cfg)
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now()
    feed = V3Feed(cfg)
    mom = _wire(Bot(cfg, feed, now, logdir=out_dir, sell_checker=SizedEntryChecker(V3_RULES["position_usd"]), tag=V3, strategy_id=V3), cfg)
    base = _wire(Bot(cfg, feed, now, logdir=out_dir, sell_checker=SizedEntryChecker(V3_RULES["position_usd"]), tag=V3B, strategy_id=V3B), cfg)
    base.entries_enabled = False                           # the baseline only enters when Momentum does (paired draw)
    rng = random.Random(V3_RULES["baseline_seed"])
    for b, name in ((mom, V3), (base, V3B)):
        b.log.log(now, "INFO", f"strategy={name} fingerprint={fp} rules={json.dumps(V3_RULES, sort_keys=True)} (PAPER ONLY)")
    scanner, rug = V3Scanner(cfg), RugCheck(cfg)
    jpath = os.path.join(out_dir, f"v3_collect_{now:%Y%m%d_%H%M%S}.jsonl")
    out = open(jpath, "a", buffering=1)
    evaluable, cycles, stop_reason, draws = set(), 0, None, []
    t_start = time.time()
    end = t_start + minutes * 60
    while time.time() < end:
        t0 = time.time()
        now = datetime.now()
        for b in (mom, base):
            b.ex.cycle_t = t0
            for a in b.ex.positions:
                feed.pin(a)
        before = set(mom.traded)
        mom.entries_enabled = (end - t0) > 5 * 60
        for b, step in ((mom, True), (base, False)):
            try:
                b.tick(now, step=step)
            except Exception as e:
                b.log.log(now, "ERROR", repr(e)[:200])
        cycles += 1
        snap = [p for p in feed.snapshot() if p.age_s <= cfg.max_age_s
                and (p.fetched_at is None or time.time() - p.fetched_at <= STALE_S)]   # no stale data in candidates
        eligible = []
        for p in snap:
            verdict, why, sig = evaluate(cfg, scanner, rug, p, skip_sell=True)
            if scanner.universe_fail(p) is None and rug.check(p, skip_sell=True)[0]:
                evaluable.add(p.addr)
                eligible.append(p)
            verdict, why = _decision(mom, mom.sell_checker, p, verdict, why)
            out.write(json.dumps({"variant": V3, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mint": p.addr,
                                  "pair": p.pair_addr, "dex": p.source, "age_h": round(p.age_s / 3600, 2), "price": p.price,
                                  "liquidity": p.liquidity, "vol5m": p.vol5m, "holders": p.holders, "signals": sig,
                                  "disc": feed.sources(p.addr), "verdict": verdict, "reason": why}) + "\n")
        for addr in sorted(set(mom.traded) - before):       # paired random draw for the baseline
            pool = [p for p in eligible if p.addr not in base.traded and p.addr not in base.ex.positions]
            draws.append({"ts": now.isoformat(timespec="seconds"), "momentum_entry": addr, "eligible_options": len(pool),
                          "includes_momentum_pick": any(p.addr == addr for p in pool)})
            base.log.log(now, "DRAW", f"baseline draw for Momentum entry {addr[:8]}: {len(pool)} eligible option(s) in this cycle")
            rng.shuffle(pool)
            done = False
            for p in pool[:3]:
                if len(base.ex.positions) >= cfg.max_open:
                    base.log.log(now, "SKIPPED", "baseline at max open positions")
                    break
                st = base.sell_checker.check(p)
                if st != "OK":
                    base.log.log(now, "FILTERED", f"{p.symbol} mint={p.addr} baseline draw: SELL_SIMULATION={st} "
                                                  f"({base.sell_checker.last_detail.get(p.addr, '')})")
                    continue
                q = feed.refresh(p.addr)
                if q is None or not q.price:
                    continue
                base.traded.add(p.addr)
                done = base.ex.enter(dataclasses.replace(p, price=q.price, liquidity=q.liquidity or p.liquidity),
                                     V3_RULES["position_usd"], ["RANDOM"], now)
                break
            if not done:
                base.log.log(now, "INFO", f"baseline: no entry for Momentum entry {addr[:8]} ({len(pool)} eligible in this cycle)")
        for b in (mom, base):
            save_state(b, os.path.join(state_dir, f"paper_state_{b.strategy_id}.json"))
        if time.time() - t_start >= early_stop_min * 60 and not evaluable and stop_reason is None:
            stop_reason = f"STOP: no evaluable candidate (universe + safety) in the first {early_stop_min} min"
            mom.log.log(now, "STOP", stop_reason)
            break
        time.sleep(max(0, interval_s - (time.time() - t0)))
    for b in (mom, base):
        b.log.log(datetime.now(), "INFO", f"end cycles={cycles} open={len(b.ex.positions)} closed={len(b.ex.trades)} "
                                          f"failed_exits={b.ex.failed_exits} (open positions stay open; no sale is invented)")
        save_state(b, os.path.join(state_dir, f"paper_state_{b.strategy_id}.json"))
        b.log.close()
    out.close()
    json.dump({"strategy": V3, "baseline": V3B, "fingerprint": fp, "rules": V3_RULES, "cycles": cycles,
               "minutes_planned": minutes, "minutes_run": round((time.time() - t_start) / 60, 1), "stop": stop_reason,
               "sha": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"), "jsonl": os.path.basename(jpath),
               "pools_discovered": len(feed.v3_pools), "evaluable_pairs": len(evaluable),
               "entry_simulations": mom.sell_checker.entry, "baseline_draws": draws, "baseline_entry_simulations": base.sell_checker.entry,
               "discovery_errors": {k: dict(v) for k, v in feed.disc_errors.items()},
               "logs": {V3: os.path.basename(mom.log.path), V3B: os.path.basename(base.log.path)}},
              open(os.path.join(out_dir, "v3_meta.json"), "w"), indent=1)
    return mom, base
