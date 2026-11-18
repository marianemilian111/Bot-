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
from .frozen import STRATEGY_ID, assert_frozen
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
    json.dump({"strategy": STRATEGY_ID, "saved": datetime.now().isoformat(), "start": eq.start, "cash": eq.cash,
               "wins": eq.wins, "losses": eq.losses, "loss_streak": eq.loss_streak, "day": eq.day.isoformat(),
               "peak": eq.peak, "paused": eq.paused, "curve": [(t.isoformat(), v) for t, v in eq.curve],
               "positions": pos, "pending": ex.pending, "trades": trades, "failed_exits": ex.failed_exits,
               "traded": sorted(bot.traded)}, open(path, "w"), indent=1)


def load_state(bot, path) -> bool:
    if not os.path.exists(path):
        return False
    d = json.load(open(path))
    if d.get("strategy") != STRATEGY_ID:
        raise SystemExit(f"{path} belongs to strategy {d.get('strategy')}, not {STRATEGY_ID}")
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


def run_collector(cfg: Config, minutes: float, interval_s: int = 30, state_path="state/paper_state.json",
                  out_dir="logs", no_entry_last_min: float = 0):
    fp = assert_frozen(cfg)
    os.makedirs(out_dir, exist_ok=True)
    feed, checker = LiveFeed(cfg), SellChecker()
    now = datetime.now()
    bot = Bot(cfg, feed, now, logdir=out_dir, sell_checker=checker)
    resumed = load_state(bot, state_path)
    bot.log.log(now, "INFO", f"strategy={STRATEGY_ID} fingerprint={fp} resumed_state={resumed} "
                             f"minutes={minutes} (read-only, paper only)")
    scanner, rug = Scanner(cfg), RugCheck(cfg)
    jpath = os.path.join(out_dir, f"collect_{now:%Y%m%d_%H%M%S}.jsonl")
    out = open(jpath, "a", buffering=1)
    end = time.time() + minutes * 60
    cycles = 0
    while time.time() < end:
        t0 = time.time()
        now = datetime.now()
        bot.entries_enabled = (end - t0) > no_entry_last_min * 60
        try:
            bot.tick(now)
        except Exception as e:                       # never die silently on a feed hiccup; record it
            bot.log.log(now, "ERROR", repr(e)[:200])
        cycles += 1
        for p in feed.snapshot():
            if p.age_s > cfg.max_age_s:
                continue
            verdict, why, sig = evaluate(cfg, scanner, rug, p, skip_sell=True)
            out.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mint": p.addr, "pair": p.pair_addr,
                "source": p.source, "age_s": round(p.age_s), "price": p.price, "liquidity": p.liquidity,
                "vol5m": p.vol5m, "vol_avg5m_1h": p.vol_avg5m_1h, "buys5m": p.buys5m, "sells5m": p.sells5m,
                "holders": p.holders, "holders_10m": p.holders_10m, "signals": sig, "verdict": verdict,
                "reason": why}) + "\n")
        save_state(bot, state_path)
        time.sleep(max(0, interval_s - (time.time() - t0)))
    bot.log.log(datetime.now(), "INFO", f"end cycles={cycles} open={len(bot.ex.positions)} "
                                         f"closed={len(bot.ex.trades)} failed_exits={bot.ex.failed_exits} "
                                         f"sellsim={checker.stats}")
    save_state(bot, state_path)
    out.close()
    bot.log.close()
    return bot, jpath, checker
