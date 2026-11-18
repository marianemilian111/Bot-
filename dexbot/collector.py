"""Read-only collector + paper trading of the frozen momentum strategy on live public data.
No wallet, no keys, no orders. Every evaluated pair is appended to a JSONL file; paper state survives between runs."""
import dataclasses
import json
import os
import time
from datetime import datetime, timezone

from .agents import RugCheck, Scanner
from .alert import evaluate
from .bot import Bot
from .config import Config
from .frozen import assert_frozen
from .variants import V1, make
from .live import LiveFeed
from .models import Position, Trade
from .sellsim import SellChecker


def _dt(v):
    return datetime.fromisoformat(v)


def save_state(bot, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    eq, ex = bot.eq, bot.ex
    pos = {a: {**dataclasses.asdict(p), "opened": p.opened.isoformat()} for a, p in ex.positions.items()}
    trades = [{**dataclasses.asdict(t), "opened": t.opened.isoformat(), "closed": t.closed.isoformat()} for t in ex.trades]
    json.dump({"strategy": bot.strategy_id, "saved": datetime.now().isoformat(), "start": eq.start, "cash": eq.cash,
               "wins": eq.wins, "losses": eq.losses, "loss_streak": eq.loss_streak, "day": eq.day.isoformat(),
               "peak": eq.peak, "paused": eq.paused, "curve": [(t.isoformat(), v) for t, v in eq.curve],
               "positions": pos, "pending": ex.pending, "trades": trades, "failed_exits": ex.failed_exits,
               "traded": sorted(bot.traded)}, open(path, "w"), indent=1)


def load_state(bot, path) -> bool:
    if not os.path.exists(path):
        return False
    d = json.load(open(path))
    if d.get("strategy") != bot.strategy_id:
        raise SystemExit(f"{path} belongs to strategy {d.get('strategy')}, not {bot.strategy_id}")
    eq, ex = bot.eq, bot.ex
    eq.start, eq.cash, eq.wins, eq.losses = d["start"], d["cash"], d["wins"], d["losses"]
    eq.loss_streak, eq.peak, eq.paused = d["loss_streak"], d["peak"], d["paused"]
    eq.day = datetime.fromisoformat(d["day"]).date() if "T" in d["day"] else datetime.strptime(d["day"], "%Y-%m-%d").date()
    eq.curve = [(_dt(t), v) for t, v in d["curve"]]
    for a, p in d["positions"].items():
        p["opened"] = _dt(p["opened"])
        ex.positions[a] = Position(**p)
    ex.pending = d["pending"]
    ex.trades = [Trade(**{**t, "opened": _dt(t["opened"]), "closed": _dt(t["closed"])}) for t in d["trades"]]
    ex.failed_exits = d.get("failed_exits", 0)
    bot.traded = set(d["traded"])
    return True


def _decision(bot, checker, p, verdict, why):
    """Final per-pair state for this variant, including what the sell simulation said."""
    if p.addr in bot.traded:
        return "ENTERED", "paper entry taken"
    if verdict == "CANDIDATE":
        hit = checker.cache.get(p.addr)
        if hit:
            return ("SELL_SIMULATION=" + hit[1]), "SELL_SIMULATION=" + hit[1] + (
                " (rejected: UNKNOWN or FAIL never passes)" if hit[1] != "OK" else " but no entry (limits/pause)")
        return "CANDIDATE", "non-sell checks passed; not simulated yet (limits/pause/one-entry-per-pair)"
    return verdict, why


def run_collector(base_cfg: Config, minutes: float, variants=(V1,), interval_s: int = 30, state_dir="state",
                  out_dir="logs", no_entry_last_min: float = 0):
    os.makedirs(out_dir, exist_ok=True)
    feed, checker = LiveFeed(base_cfg), SellChecker()      # one feed and one sell-simulation cache shared by all variants
    now = datetime.now()
    bots = {}
    for vid in variants:
        cfg = make(vid, base_cfg.start_balance)
        fp = assert_frozen(cfg, vid)
        b = Bot(cfg, feed, now, logdir=out_dir, sell_checker=checker, tag=vid, strategy_id=vid)
        b.state_path = os.path.join(state_dir, f"paper_state_{vid}.json")
        resumed = load_state(b, b.state_path)
        b.log.log(now, "INFO", f"strategy={vid} fingerprint={fp} resumed_state={resumed} minutes={minutes} "
                               f"thresholds liq>={cfg.min_liq:,.0f} vol5m>={cfg.min_vol5m:,.0f} signals>={cfg.min_signals} "
                               "(read-only, paper only)")
        bots[vid] = (b, cfg, Scanner(cfg), RugCheck(cfg))
    jpath = os.path.join(out_dir, f"collect_{now:%Y%m%d_%H%M%S}.jsonl")
    out = open(jpath, "a", buffering=1)
    end = time.time() + minutes * 60
    cycles = 0
    while time.time() < end:
        t0 = time.time()
        now = datetime.now()
        for b, *_ in bots.values():
            for a in b.ex.positions:
                feed.pin(a)
        first = True
        for vid, (b, cfg, scanner, rug) in bots.items():
            b.entries_enabled = (end - t0) > no_entry_last_min * 60
            try:
                b.tick(now, step=first)
            except Exception as e:                   # record feed hiccups instead of dying silently
                b.log.log(now, "ERROR", repr(e)[:200])
            first = False
        cycles += 1
        for vid, (b, cfg, scanner, rug) in bots.items():
            for p in feed.snapshot():
                if p.age_s > cfg.max_age_s:
                    continue
                verdict, why, sig = evaluate(cfg, scanner, rug, p, skip_sell=True)
                verdict, why = _decision(b, checker, p, verdict, why)
                out.write(json.dumps({
                    "variant": vid, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mint": p.addr,
                    "pair": p.pair_addr, "source": p.source, "age_s": round(p.age_s), "price": p.price,
                    "liquidity": p.liquidity, "vol5m": p.vol5m, "vol_avg5m_1h": p.vol_avg5m_1h, "buys5m": p.buys5m,
                    "sells5m": p.sells5m, "holders": p.holders, "holders_10m": p.holders_10m, "signals": sig,
                    "verdict": verdict, "reason": why}) + "\n")
            save_state(b, b.state_path)
        time.sleep(max(0, interval_s - (time.time() - t0)))
    for vid, (b, *_ ) in bots.items():
        b.log.log(datetime.now(), "INFO", f"end cycles={cycles} open={len(b.ex.positions)} closed={len(b.ex.trades)} "
                                           f"failed_exits={b.ex.failed_exits}")
        save_state(b, b.state_path)
        b.log.close()
    out.close()
    json.dump({"variants": list(variants), "cycles": cycles, "minutes": minutes, "sha": os.environ.get("GITHUB_SHA"),
               "run_id": os.environ.get("GITHUB_RUN_ID"), "jsonl": os.path.basename(jpath),
               "sellsim_entry": checker.entry, "sellsim_exit": checker.exit,
               "logs": {v: os.path.basename(b.log.path) for v, (b, *_ ) in bots.items()}},
              open(os.path.join(out_dir, "run_meta.json"), "w"), indent=1)
    return bots, jpath, checker
