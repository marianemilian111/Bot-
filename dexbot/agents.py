"""The six agents. Scanner flags only; nothing executes without a RugCheck PASS."""
from collections import Counter, deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import Config
from .models import Flag, Pair, Position, Trade


# ---------------------------------------------------------------- 1. SCANNER
class Scanner:
    """Flags pairs. Never trades."""
    status = "idle"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.scanned = 0
        self.flagged = 0

    def signals(self, p: Pair) -> List[str]:
        c, s = self.cfg, []
        if p.vol_avg5m_1h > 0 and p.vol5m > c.spike_mult * p.vol_avg5m_1h:
            s.append("VOL_SPIKE")
        tot = p.buys5m + p.sells5m
        if tot >= 20 and p.buys5m / tot > c.buy_ratio_min:
            s.append("BUY_RATIO")
        if p.holders and p.holders_10m and p.holders >= p.holders_10m * (1 + c.holder_growth_min):
            s.append("HOLDER_GROWTH")
        if p.smart_buyers_10m >= c.smart_min:
            s.append("SMART_MONEY")
        if p.kol_bought:
            s.append("KOL")
        return s

    def universe_fail(self, p: Pair) -> Optional[str]:
        c = self.cfg
        if p.age_s > c.max_age_s:
            return "older than 6h"
        if p.liquidity is None:
            return "liquidity UNKNOWN (source gives none)"
        if p.liquidity < c.min_liq:
            return f"liquidity ${p.liquidity:,.0f} < ${c.min_liq:,.0f}"
        if p.vol5m < c.min_vol5m:
            return f"5m volume ${p.vol5m:,.0f} < ${c.min_vol5m:,.0f}"
        if p.holders is None or p.holders < c.min_holders:
            return f"holders {p.holders} < {c.min_holders}"
        if p.mint_frozen is not False:
            return "frozen/unknown mint"
        if p.mint_auth_open is not False:
            return "open/unknown mint authority"
        if p.lp_status in (None, "unlocked"):
            return "LP unlocked/unknown"
        return None

    def scan(self, pairs: List[Pair]) -> List[Flag]:
        out = []
        for p in pairs:
            if p.age_s > self.cfg.max_age_s:
                continue
            self.scanned += 1
            sig = self.signals(p)
            if len(sig) >= self.cfg.min_signals:
                out.append(Flag(p, sig, self.universe_fail(p)))
        out.sort(key=lambda f: -len(f.signals))
        self.flagged += len(out)
        self.status = f"scanned {self.scanned}, flagged {self.flagged}"
        return out


# ---------------------------------------------------------------- 2. RUG CHECK
class RugCheck:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.passed = 0
        self.failed = 0

    @property
    def status(self):
        return f"PASS {self.passed} / FAIL {self.failed}"

    def check(self, p: Pair) -> Tuple[bool, str]:
        c = self.cfg
        # unknown (None) data fails closed
        checks = [
            (p.dev_pct is not None and p.dev_pct < c.dev_max_pct, f"dev wallet {p.dev_pct}% (need <{c.dev_max_pct}%)"),
            (p.top10_pct is not None and p.top10_pct < c.top10_max_pct, f"top10 holders {p.top10_pct}% (need <{c.top10_max_pct}%)"),
            (p.bundled is False, f"bundled launch ({p.bundled})"),
            (p.lp_status in ("burned", "locked_30d"), f"LP {p.lp_status} (need burned/locked 30d+)"),
            (p.honeypot is False, "SELL_SIMULATION=" + ("FAIL (honeypot)" if p.honeypot else "UNKNOWN (a SELL_QUOTE is not a simulation)")),
            (p.mint_frozen is False, "frozen mint"),
            (p.mint_auth_open is False, "open mint authority"),
        ]
        for ok, why in checks:
            if not ok:
                self.failed += 1
                return False, why
        self.passed += 1
        return True, "all checks passed"


# ---------------------------------------------------------------- 3. SIZER
class Sizer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last = "-"

    def size(self, equity: float, available: float, n_signals: int,
             open_count: int, loss_streak: int) -> Tuple[float, str]:
        c = self.cfg
        if open_count >= c.max_open:
            return 0.0, f"max {c.max_open} open positions"
        frac = c.size_fracs[min(max(n_signals, 2), 4)]
        note = f"{frac:.0%} of equity"
        if loss_streak >= c.loss_streak_trigger:
            frac *= c.loss_streak_scale
            note += f" x{c.loss_streak_scale} (loss streak {loss_streak})"
        usd = min(equity * frac, available)
        self.last = f"${usd:,.2f} ({note})"
        if usd < 10:
            return 0.0, "size below $10 / no free cash"
        return usd, note


# ---------------------------------------------------------------- 4. EXECUTOR (paper fills)
class Executor:
    """Paper executor: fills are modelled from pool depth. See README for going live."""

    def __init__(self, cfg: Config, log, equity):
        self.cfg, self.log, self.eq = cfg, log, equity
        self.positions: Dict[str, Position] = {}
        self.pending: Dict[str, float] = {}   # addr -> usd still to buy (part 2)
        self.trades: List[Trade] = []
        self.status = "idle"

    # slippage from constant-product depth: buying $x into a pool with liq/2 in quote
    def slip(self, usd: float, liq: float) -> float:
        return 0.003 + 2 * usd / max(liq, 1)

    def reserved(self) -> float:
        return sum(self.pending.values())

    def _buy(self, pos: Position, p: Pair, usd: float, tag: str, now: datetime) -> bool:
        sl = self.slip(usd, p.liquidity)
        if sl > self.cfg.max_slippage:
            self.log.log(now, "FILTERED", f"{p.symbol} {tag}: slippage {sl:.2%} > {self.cfg.max_slippage:.1%}")
            return False
        fill = p.price * (1 + sl)
        tok = usd * (1 - self.cfg.fee) / fill
        self.eq.cash -= usd
        pos.cost += usd
        pos.tokens += tok
        pos.tokens_bought += tok
        pos.parts_done += 1
        pos.peak = max(pos.peak, p.price)
        self.log.log(now, "ENTER", f"{p.symbol} {tag} size=${usd:,.2f} fill={fill:.8g} slip={sl:.2%} "
                                   f"signals={'+'.join(pos.signals)}")
        return True

    def enter(self, p: Pair, usd: float, signals: List[str], now: datetime) -> bool:
        parts = self.cfg.entry_parts
        pos = Position(p.addr, p.symbol, now, usd, signals, last_price=p.price)
        if not self._buy(pos, p, usd / parts, f"part 1/{parts}", now):
            return False
        self.positions[p.addr] = pos
        if parts > 1:
            self.pending[p.addr] = usd - usd / parts
        return True

    def _sell(self, pos: Position, p: Pair, tokens: float, why: str, now: datetime):
        tokens = min(tokens, pos.tokens)
        if tokens <= 0:
            return
        usd_val = tokens * p.price
        sl = min(self.slip(usd_val, p.liquidity), 0.5)
        fill = p.price * (1 - sl)
        got = tokens * fill * (1 - self.cfg.fee)
        pos.tokens -= tokens
        pos.proceeds += got
        self.eq.cash += got
        pos.reasons.append(why)
        part_cost = pos.avg_entry * tokens
        pnl = got - part_cost
        self.log.log(now, "EXIT", f"{p.symbol} {why} sold={tokens / pos.tokens_bought:.0%} size=${got:,.2f} "
                                  f"fill={fill:.8g} pnl={pnl:+,.2f} ({pnl / part_cost * 100:+.1f}%)")

    def _close_if_done(self, pos: Position, now: datetime, prices: Dict[str, float]):
        if pos.tokens > 1e-12:
            return
        self.positions.pop(pos.addr, None)
        self.pending.pop(pos.addr, None)
        t = Trade(pos.symbol, pos.opened, now, pos.cost, pos.proceeds, pos.reasons)
        self.trades.append(t)
        self.eq.on_close(now, t, self.value(prices))
        self.log.log(now, "CLOSED", f"{t.symbol} total pnl={t.pnl:+,.2f} ({t.pnl_pct:+.1f}%) "
                                    f"balance=${self.eq.cash:,.2f} reasons={','.join(dict.fromkeys(t.reasons))}")

    def value(self, prices: Dict[str, float]) -> float:
        return sum(pos.tokens * prices.get(a, pos.last_price) for a, pos in self.positions.items())

    def manage(self, pairs: Dict[str, Pair], now: datetime):
        c = self.cfg
        prices = {a: p.price for a, p in pairs.items()}
        for addr, pos in list(self.positions.items()):
            p = pairs.get(addr)
            if p is None:
                continue
            pos.last_price = p.price
            pos.peak = max(pos.peak, p.price)
            avg = pos.avg_entry
            age = (now - pos.opened).total_seconds()

            if p.dev_sold or p.lp_pulled:
                self._sell(pos, p, pos.tokens, "DEV_SELL" if p.dev_sold else "LP_PULLED", now)
                self.pending.pop(addr, None)
            elif p.price <= avg * (1 - c.hard_stop):
                self._sell(pos, p, pos.tokens, "HARD_STOP", now)
                self.pending.pop(addr, None)
            else:
                # second half of the entry
                if addr in self.pending:
                    usd = self.pending.pop(addr)
                    self._buy(pos, p, usd, f"part 2/{c.entry_parts}", now)
                    avg = pos.avg_entry
                if pos.stage < 1 and p.price >= avg * c.tp1_mult:
                    self._sell(pos, p, pos.tokens_bought * c.tp1_frac, "TP1_2x", now)
                    pos.stage = 1
                if pos.stage < 2 and p.price >= avg * c.tp2_mult:
                    self._sell(pos, p, pos.tokens_bought * c.tp2_frac, "TP2_4x", now)
                    pos.stage = 2
                if pos.stage == 2 and pos.tokens > 0 and p.price <= pos.peak * (1 - c.trail_pct):
                    self._sell(pos, p, pos.tokens, "TRAIL_25%", now)
                elif pos.stage < 2 and age > c.time_stop_s and pos.tokens > 0:
                    self._sell(pos, p, pos.tokens, "TIME_STOP", now)
                    self.pending.pop(addr, None)
            self._close_if_done(pos, now, prices)
        self.status = f"{len(self.positions)} open, {len(self.trades)} closed"

    def close_all(self, pairs: Dict[str, Pair], now: datetime, why: str):
        prices = {a: p.price for a, p in pairs.items()}
        for addr, pos in list(self.positions.items()):
            p = pairs.get(addr)
            if p is None:
                continue
            self.pending.pop(addr, None)
            self._sell(pos, p, pos.tokens, why, now)
            self._close_if_done(pos, now, prices)


# ---------------------------------------------------------------- S. LOGGER
class Logger:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "a", buffering=1)
        self.tail = deque(maxlen=500)
        self.counts = Counter()
        self.status = "ok"

    def log(self, now: datetime, kind: str, msg: str):
        line = f"{now:%Y-%m-%d %H:%M:%S} {kind:<8} {msg}"
        self.f.write(line + "\n")
        self.tail.append(line)
        self.counts[kind] += 1

    def close(self):
        self.f.close()


# ---------------------------------------------------------------- 6. EQUITY
class Equity:
    def __init__(self, start: float, now: datetime, log):
        self.start = start
        self.cash = start
        self.log = log
        self.curve: List[Tuple[datetime, float]] = [(now, start)]
        self.wins = self.losses = 0
        self.loss_streak = 0
        self.day = now.date()
        self.peak = start
        self.paused = False
        self.status = "ok"

    def on_close(self, now: datetime, t: Trade, open_value: float):
        if t.pnl > 0:
            self.wins += 1
            self.loss_streak = 0
        else:
            self.losses += 1
            self.loss_streak += 1
        self.curve.append((now, self.cash + open_value))
        self.log.log(now, "EQUITY", f"balance=${self.cash:,.2f} equity=${self.cash + open_value:,.2f} "
                                    f"W/L={self.wins}/{self.losses} streak_losses={self.loss_streak}")

    def mark(self, now: datetime, equity: float, every_s=3600):
        if (now - self.curve[-1][0]).total_seconds() >= every_s:
            self.curve.append((now, equity))

    def drawdown(self, equity: float) -> float:
        return 1 - equity / self.peak if self.peak else 0.0

    def check_drawdown(self, now: datetime, equity: float, limit: float):
        if now.date() != self.day:               # new day: reset peak, lift pause
            self.day, self.peak = now.date(), equity
            if self.paused:
                self.paused = False
                self.log.log(now, "RESUME", "new day, peak reset")
        self.peak = max(self.peak, equity)
        dd = self.drawdown(equity)
        if dd >= limit and not self.paused:
            self.paused = True
            self.log.log(now, "PAUSE", f"daily drawdown {dd:.1%} >= {limit:.0%} from peak ${self.peak:,.2f} "
                                       f"(equity ${equity:,.2f}); no new entries until next day")
        self.status = "PAUSED (drawdown)" if self.paused else f"dd {dd:.1%}"
