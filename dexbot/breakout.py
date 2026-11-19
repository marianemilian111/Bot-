"""Breakout signal in two variants (immediate / after retest) + a quote-driven paper backtest. Rules: docs/BREAKOUT_RULES.md.

Decisions use only observations at or before the decision time. Fills come only from RECORDED quotes at the simulated
time (+ latency), for the exact USD size (buy) or token amount (sell). A missing quote never becomes a fill: entries are
skipped, exits stay pending and the position remains open. Nothing here signs, sends or calls a network."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Dict, List, Optional

RULES = {"lookback": 20, "break_pct": 0.005, "vol_mult": 2.0, "retest_window": 10, "retest_floor": 0.98,
         "min_liq": 40_000, "min_vol5m": 25_000, "size_usd": 14.0, "net_fee_usd": 0.02, "quote_tolerance_s": 60,
         "tp1": (2.0, 0.40), "tp2": (4.0, 0.30), "trail": 0.25, "hard_stop": 0.12, "time_stop_s": 25 * 60}


@dataclass
class Obs:
    ts: datetime
    mint: str
    price: float
    vol5m: float
    liquidity: Optional[float]


def signals(obs: List[Obs], variant: str, r=RULES) -> List[dict]:
    """Return decisions [{mint, ts, level, breakout_ts}] for one variant. `obs` sorted by time."""
    hist: Dict[str, List[Obs]] = defaultdict(list)
    pending: Dict[str, dict] = {}                 # retest state per mint
    out = []
    for o in obs:
        h = hist[o.mint]
        ok_universe = (o.liquidity or 0) >= r["min_liq"] and o.vol5m >= r["min_vol5m"]
        if variant == "retest" and o.mint in pending:
            st = pending[o.mint]
            st["seen"] += 1
            L = st["level"]
            if o.price < L * r["retest_floor"]:
                del pending[o.mint]                                   # broke back below the zone: invalid
            elif not st["touched"]:
                st["touched"] = L * r["retest_floor"] <= o.price <= L * (1 + r["break_pct"])
            elif o.price > L * (1 + r["break_pct"]) and ok_universe:
                out.append({"mint": o.mint, "ts": o.ts, "level": L, "breakout_ts": st["ts"]})
                del pending[o.mint]
            if o.mint in pending and pending[o.mint]["seen"] >= r["retest_window"]:
                del pending[o.mint]                                   # window over, no trade
        if len(h) >= r["lookback"] and ok_universe:
            prev = h[-r["lookback"]:]
            level = max(x.price for x in prev)
            vmed = median(x.vol5m for x in prev)
            if o.price > level * (1 + r["break_pct"]) and vmed > 0 and o.vol5m > r["vol_mult"] * vmed:
                if variant == "immediate":
                    out.append({"mint": o.mint, "ts": o.ts, "level": level, "breakout_ts": o.ts})
                elif o.mint not in pending:
                    pending[o.mint] = {"level": level, "ts": o.ts, "seen": 0, "touched": False}
        h.append(o)
    return out


class RecordedQuotes:
    """Quotes recorded at real times. buy: (mint, usd) -> [(ts, tokens_out)]; sell: (mint) -> [(ts, amount, usd_out|None, status)]."""

    def __init__(self, buys=None, sells=None):
        self.buys, self.sells = buys or defaultdict(list), sells or defaultdict(list)

    def buy(self, mint, usd, t, tol_s):
        for ts, u, tokens in sorted(self.buys.get(mint, [])):
            if ts >= t and (ts - t).total_seconds() <= tol_s and abs(u - usd) < 1e-9:
                return ts, tokens
        return None

    def sell(self, mint, tokens, t, tol_s):
        for ts, amount, usd_out, status in sorted(self.sells.get(mint, []), key=lambda x: x[0]):
            if ts >= t and (ts - t).total_seconds() <= tol_s and abs(amount - tokens) <= 1e-9 * max(1.0, tokens):
                return ts, usd_out, status
        return None


@dataclass
class Pos:
    mint: str
    opened: datetime
    cost: float
    tokens: float
    entry_px: float
    sold_tokens: float = 0.0
    proceeds: float = 0.0
    imputed: float = 0.0
    stage: int = 0
    peak: float = 0.0
    closed: Optional[datetime] = None
    reasons: List[str] = field(default_factory=list)


def backtest(obs: List[Obs], decisions: List[dict], quotes: RecordedQuotes, latency_s: int, r=RULES):
    """Quote-driven paper run. Returns dict with entries, closed, open, failed exits, imputed and net PnL."""
    by_t = sorted(obs, key=lambda o: o.ts)
    dec = sorted(decisions, key=lambda d: d["ts"])
    open_: Dict[str, Pos] = {}
    done: List[Pos] = []
    skipped, failed, pending_exits = [], 0, 0
    di = 0
    for o in by_t:
        while di < len(dec) and dec[di]["ts"] <= o.ts:
            d = dec[di]; di += 1
            if d["mint"] in open_:
                continue
            q = quotes.buy(d["mint"], r["size_usd"], d["ts"] + timedelta(seconds=latency_s), r["quote_tolerance_s"])
            if q is None:
                skipped.append((d["mint"], d["ts"], "no recorded buy quote"))
                continue
            tokens = q[1]
            open_[d["mint"]] = Pos(d["mint"], q[0], r["size_usd"], tokens, (r["size_usd"] - r["net_fee_usd"]) / tokens)
        p = open_.get(o.mint)
        if not p or o.ts < p.opened:
            continue
        p.peak = max(p.peak, o.price)
        x = o.price / p.entry_px
        want = []
        if x <= 1 - r["hard_stop"]:
            want = [("HARD_STOP", p.tokens - p.sold_tokens)]
        elif (o.ts - p.opened).total_seconds() > r["time_stop_s"]:
            want = [("TIME_STOP", p.tokens - p.sold_tokens)]
        else:
            if p.stage < 1 and x >= r["tp1"][0]:
                want.append(("TP1", p.tokens * r["tp1"][1]))
            if p.stage < 2 and x >= r["tp2"][0]:
                want.append(("TP2", p.tokens * r["tp2"][1]))
            if p.stage >= 2 and o.price <= p.peak * (1 - r["trail"]):
                want.append(("TRAIL", p.tokens - p.sold_tokens))
        for why, qty in want:
            qty = min(qty, p.tokens - p.sold_tokens)
            if qty <= 0:
                continue
            s = quotes.sell(p.mint, qty, o.ts + timedelta(seconds=latency_s), r["quote_tolerance_s"])
            if s is None:
                pending_exits += 1                                   # no quote: not sold, stays open
                break
            ts, usd_out, status = s
            if status == "FAIL":
                p.imputed += p.cost * qty / p.tokens; failed += 1; p.reasons.append(f"{why}:FAIL")
            else:
                p.proceeds += usd_out - r["net_fee_usd"]; p.reasons.append(why)
            p.sold_tokens += qty
            if why == "TP1": p.stage = 1
            if why == "TP2": p.stage = 2
        if p.tokens - p.sold_tokens <= 1e-12:
            p.closed = o.ts; done.append(p); del open_[p.mint]
    net = sum(p.proceeds - p.cost for p in done) if done else None
    return {"decisions": len(dec), "entries": len(done) + len(open_), "closed": len(done), "net": net,
            "net_without_best": (net - max(p.proceeds - p.cost for p in done)) if done else None,
            "open": len(open_), "failed_exits": failed, "imputed": sum(p.imputed for p in done + list(open_.values())),
            "pending_exit_attempts": pending_exits, "skipped": skipped}
