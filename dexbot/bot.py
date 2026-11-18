import dataclasses
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .agents import Equity, Executor, Logger, RugCheck, Scanner, Sizer
from .config import Config
from .dashboard import render, write_state


class Bot:
    def __init__(self, cfg: Config, feed, now: datetime, logdir="logs", sell_checker=None, tag="", strategy_id="momentum-v1"):
        os.makedirs(logdir, exist_ok=True)
        self.cfg, self.feed, self.strategy_id = cfg, feed, strategy_id
        self.log = Logger(os.path.join(logdir, f"night_{tag + '_' if tag else ''}{now:%Y%m%d_%H%M%S}.log"))
        self.eq = Equity(cfg.start_balance, now, self.log)
        self.scanner, self.rug, self.sizer = Scanner(cfg), RugCheck(cfg), Sizer(cfg)
        self.ex = Executor(cfg, self.log, self.eq)
        self.sell_checker = sell_checker
        self.entries_enabled = True
        if sell_checker is not None:
            self.ex.sell_gate = sell_checker.fresh
            self.ex.sell_detail = lambda p: sell_checker.last_detail.get(p.addr, "")
        self.seen = set()          # (addr, reason) already logged as FILTERED
        self.traded = set()        # one entry per pair
        self.log.log(now, "INFO", f"start balance=${cfg.start_balance:,.2f}")

    def _filtered(self, now, p, signals, reason):
        key = (p.addr, reason.split(" ")[0])
        if key not in self.seen:
            self.seen.add(key)
            self.log.log(now, "FILTERED", f"{p.symbol} ({p.source}) mint={p.addr} signals={'+'.join(signals)} reason: {reason}")

    def _skipped(self, now, p, signals, reason):
        """Passed the universe filter but not evaluated for entry (no rule involved, just capacity/timing)."""
        key = (p.addr, "SKIP " + reason)
        if key not in self.seen:
            self.seen.add(key)
            self.log.log(now, "SKIPPED", f"{p.symbol} ({p.source}) mint={p.addr} signals={'+'.join(signals)} reason: {reason}")

    def tick(self, now: datetime, step: bool = True):
        c = self.cfg
        if hasattr(self.feed, "pin"):
            for a in self.ex.positions:
                self.feed.pin(a)
        if step:
            self.feed.step(now)
        pairs = {p.addr: p for p in self.feed.snapshot()}
        for a in self.ex.positions:                    # keep marking held pairs
            if a not in pairs and self.feed.get(a):
                pairs[a] = self.feed.get(a)
        self.ex.manage(pairs, now)
        equity = self.eq.cash + self.ex.value({a: p.price for a, p in pairs.items()})
        self.eq.check_drawdown(now, equity, c.dd_limit)
        self.eq.mark(now, equity)

        for f in self.scanner.scan(list(pairs.values())):
            p = f.pair
            if p.addr in self.traded or p.addr in self.ex.positions:
                continue
            if f.universe_fail:
                self._filtered(now, p, f.signals, f.universe_fail)
                continue
            if self.eq.paused or not self.entries_enabled:
                self._skipped(now, p, f.signals, "daily drawdown pause" if self.eq.paused else "entries closed in the last minutes of the run")
                continue
            if len(self.ex.positions) >= c.max_open:
                self._skipped(now, p, f.signals, f"max {c.max_open} open positions")
                continue
            if p.price_1h_ago and p.price / p.price_1h_ago >= c.no_chase_mult and \
                    not (p.pullback_pct is not None and p.pullback_pct >= c.pullback_min):
                self._filtered(now, p, f.signals, "no-chase: +300% in 1h without pullback")
                continue
            if self.sell_checker is not None:
                ok, why = self.rug.check(p, skip_sell=True)
                if ok:
                    st = self.sell_checker.check(p)
                    p.honeypot = {"OK": False, "FAIL": True}.get(st)     # UNKNOWN stays None -> rejected
                    if st != "OK":
                        why = f"SELL_SIMULATION={st} ({self.sell_checker.last_detail.get(p.addr, '')})"
                        self.rug.failed += 1
                        self._filtered(now, p, f.signals, "RUGCHECK " + why)
                        continue
            ok, why = self.rug.check(p)
            if not ok:
                self._filtered(now, p, f.signals, "RUGCHECK FAIL " + why)
                continue
            avail = self.eq.cash - self.ex.reserved()
            usd, note = self.sizer.size(equity, avail, len(f.signals), len(self.ex.positions), self.eq.loss_streak)
            if usd <= 0:
                self._filtered(now, p, f.signals, "SIZER " + note)
                continue
            if hasattr(self.feed, "refresh"):            # model the entry at a fresh price, not the signal-time price
                t_sig = time.time()
                q = self.feed.refresh(p.addr)
                if q is None or not q.price or q.liquidity is None or q.liquidity < c.min_liq:
                    self._filtered(now, p, f.signals, "entry price refresh UNKNOWN or liquidity below floor at entry")
                    continue
                self.log.log(now, "INFO", f"{p.symbol} entry repriced: signal {p.price:.8g} -> entry basis {q.price:.8g} "
                                          f"({(q.price / p.price - 1) * 100:+.2f}%), price refresh took {time.time() - t_sig:.1f}s (after the sell simulation)")
                p = dataclasses.replace(p, price=q.price, liquidity=q.liquidity)
            self.traded.add(p.addr)
            self.ex.enter(p, usd, f.signals, now)

    def recap(self, now: datetime, pairs) -> str:
        self.ex.close_all({p.addr: p for p in pairs}, now, "RECAP_CLOSE")
        tr, end = self.ex.trades, self.eq.cash
        by = defaultdict(float)
        for t in tr:
            by[t.symbol] += t.pnl
        top3 = sorted(by.items(), key=lambda kv: -kv[1])[:3]
        lines = ["", "═══════════ 07:00 RECAP ═══════════",
                 f"Start balance : ${self.eq.start:,.2f}",
                 f"End balance   : ${end:,.2f}  ({(end / self.eq.start - 1) * 100:+.1f}%)",
                 f"Total trades  : {len(tr)}  (W {self.eq.wins} / L {self.eq.losses})"]
        if tr:
            b, w = max(tr, key=lambda t: t.pnl), min(tr, key=lambda t: t.pnl)
            lines += [f"Best trade    : {b.symbol} {b.pnl:+,.2f} ({b.pnl_pct:+.1f}%)",
                      f"Worst trade   : {w.symbol} {w.pnl:+,.2f} ({w.pnl_pct:+.1f}%)",
                      "Top 3 coins   : " + ", ".join(f"{s} {v:+,.2f}" for s, v in top3)]
        else:
            lines.append("No trades taken.")
        out = "\n".join(lines)
        self.log.log(now, "RECAP", out.replace("\n", " | "))
        return out


def next_recap(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def run(cfg: Config, feed_factory, mode: str, start: datetime, realtime: bool,
        dash_every_s: int = 600, quiet=False):
    feed = feed_factory(start)
    bot = Bot(cfg, feed, start)
    now, end = start, next_recap(start, cfg.recap_at)
    last_dash = None
    tty = sys.stdout.isatty()
    while now < end:
        bot.tick(now)
        if last_dash is None or (now - last_dash).total_seconds() >= dash_every_s:
            last_dash = now
            txt = render(bot, now, mode)
            write_state(bot, now, "state.json")
            open("dashboard.txt", "w").write(txt)
            if not quiet:
                print(("\033[2J\033[H" if tty else "\n") + txt, flush=True)
        if realtime:
            time.sleep(cfg.tick_s)
            now = datetime.now()
        else:
            now += timedelta(seconds=cfg.tick_s)
    pairs = feed.snapshot()
    for a in bot.ex.positions:
        if feed.get(a):
            pairs.append(feed.get(a))
    print(bot.recap(now, pairs))
    write_state(bot, now, "state.json")
    open("dashboard.txt", "w").write(render(bot, now, mode))
    print(f"\nFull night log: {bot.log.path}")
    bot.log.close()
    return bot
