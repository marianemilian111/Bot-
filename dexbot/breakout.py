"""Breakout signal in two variants (immediate / after retest) + a quote-driven paper backtest. Rules: docs/BREAKOUT_RULES.md.

Decisions use only observations at or before the decision time. Fills come only from RECORDED quotes at the simulated
time (+ latency), for the exact USD size (buy) or token amount (sell). A missing quote never becomes a fill: entries are
skipped, exits stay pending and the position remains open. Nothing here signs, sends or calls a network."""
import json
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


class Detector:
    """Incremental signal detector for one variant. feed(obs) -> decision dict or None. Uses only past observations."""

    def __init__(self, variant: str, r=RULES):
        assert variant in ("immediate", "retest")
        self.variant, self.r = variant, r
        self.hist: Dict[str, List[Obs]] = defaultdict(list)
        self.pending: Dict[str, dict] = {}

    def feed(self, o: Obs) -> Optional[dict]:
        r, out = self.r, None
        h = self.hist[o.mint]
        ok_universe = (o.liquidity or 0) >= r["min_liq"] and o.vol5m >= r["min_vol5m"]
        if self.variant == "retest" and o.mint in self.pending:
            st = self.pending[o.mint]
            st["seen"] += 1
            L = st["level"]
            if o.price < L * r["retest_floor"]:
                del self.pending[o.mint]                              # broke back below the zone: invalid
            elif not st["touched"]:
                st["touched"] = L * r["retest_floor"] <= o.price <= L * (1 + r["break_pct"])
            elif o.price > L * (1 + r["break_pct"]) and ok_universe:
                out = {"mint": o.mint, "ts": o.ts, "level": L, "breakout_ts": st["ts"], "variant": "retest"}
                del self.pending[o.mint]
            if o.mint in self.pending and self.pending[o.mint]["seen"] >= r["retest_window"]:
                del self.pending[o.mint]                              # window over, no trade
        if len(h) >= r["lookback"] and ok_universe:
            prev = h[-r["lookback"]:]
            level = max(x.price for x in prev)
            vmed = median(x.vol5m for x in prev)
            if o.price > level * (1 + r["break_pct"]) and vmed > 0 and o.vol5m > r["vol_mult"] * vmed:
                if self.variant == "immediate":
                    out = {"mint": o.mint, "ts": o.ts, "level": level, "breakout_ts": o.ts, "variant": "immediate"}
                elif o.mint not in self.pending:
                    self.pending[o.mint] = {"level": level, "ts": o.ts, "seen": 0, "touched": False}
        h.append(o)
        if len(h) > r["lookback"] + 1:
            del h[0]
        return out


def signals(obs: List[Obs], variant: str, r=RULES) -> List[dict]:
    """Return decisions [{mint, ts, level, breakout_ts, variant}] for one variant. `obs` sorted by time."""
    det = Detector(variant, r)
    return [d for d in (det.feed(o) for o in obs) if d]


class RecordedQuotes:
    """Quotes recorded at real times. buys[mint] = [(ts, usd_in, tokens_out)]; sells[mint] = [(ts, amount_in, usd_out|None, status)].
    Lookup = the first recorded quote with due <= ts <= due + tolerance, same size (USD for buys, exact amount for sells)."""

    def __init__(self, buys=None, sells=None):
        self.buys = buys if buys is not None else defaultdict(list)
        self.sells = sells if sells is not None else defaultdict(list)

    def buy(self, mint, usd, due, tol_s, now=None):
        for ts, u, tokens in sorted(self.buys.get(mint, []), key=lambda x: x[0]):
            if due <= ts and (ts - due).total_seconds() <= tol_s and abs(u - usd) < 1e-9:
                return ts, tokens
        return None

    def sell(self, mint, tokens, due, tol_s, now=None):
        for ts, amount, usd_out, status in sorted(self.sells.get(mint, []), key=lambda x: x[0]):
            if due <= ts and (ts - due).total_seconds() <= tol_s and abs(amount - tokens) <= 1e-9 * max(1.0, tokens):
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


class Engine:
    """One (variant, latency) scenario. Decisions and exit triggers create requests due at time + latency; tick(now)
    resolves due requests through the quote provider. No quote -> no entry / no exit (position stays open)."""

    def __init__(self, provider, latency_s: int, r=RULES, entry_cutoff: Optional[datetime] = None):
        self.q, self.lat, self.r, self.cutoff = provider, timedelta(seconds=latency_s), r, entry_cutoff
        self.open: Dict[str, Pos] = {}
        self.done: List[Pos] = []
        self.pbuy: Dict[str, datetime] = {}                  # mint -> due
        self.psell: Dict[str, tuple] = {}                    # mint -> (due, qty, why)
        self.n = {"decisions": 0, "busy": 0, "window_end": 0, "no_buy_quote": 0, "failed_exits": 0, "exit_no_quote": 0,
                  "exit_fills": 0}

    def on_decision(self, d):
        self.n["decisions"] += 1
        if d["mint"] in self.open or d["mint"] in self.pbuy:
            self.n["busy"] += 1
        elif self.cutoff is not None and d["ts"] > self.cutoff:
            self.n["window_end"] += 1
        else:
            self.pbuy[d["mint"]] = d["ts"] + self.lat

    def on_obs(self, o: Obs):
        p = self.open.get(o.mint)
        if not p or o.ts < p.opened or o.mint in self.psell:
            return
        r = self.r
        p.peak = max(p.peak, o.price)
        x, left = o.price / p.entry_px, p.tokens - p.sold_tokens
        want = None
        if x <= 1 - r["hard_stop"]:
            want = ("HARD_STOP", left)
        elif (o.ts - p.opened).total_seconds() > r["time_stop_s"]:
            want = ("TIME_STOP", left)
        elif p.stage < 1 and x >= r["tp1"][0]:
            want = ("TP1", p.tokens * r["tp1"][1])
        elif p.stage < 2 and x >= r["tp2"][0]:
            want = ("TP2", p.tokens * r["tp2"][1])
        elif p.stage >= 2 and o.price <= p.peak * (1 - r["trail"]):
            want = ("TRAIL", left)
        if want:
            self.psell[o.mint] = (o.ts + self.lat, min(want[1], left), want[0])

    def tick(self, now: datetime):
        r = self.r
        for mint, due in list(self.pbuy.items()):
            if due > now:
                continue
            del self.pbuy[mint]
            q = self.q.buy(mint, r["size_usd"], due, r["quote_tolerance_s"], now)
            if q is None:
                self.n["no_buy_quote"] += 1
                continue
            self.open[mint] = Pos(mint, q[0], r["size_usd"], q[1], (r["size_usd"] - r["net_fee_usd"]) / q[1])
        for mint, (due, qty, why) in list(self.psell.items()):
            if due > now:
                continue
            del self.psell[mint]
            p = self.open[mint]
            s = self.q.sell(mint, qty, due, r["quote_tolerance_s"], now)
            if s is None:
                self.n["exit_no_quote"] += 1                  # not sold; re-triggered by the next observation
                continue
            usd_out, status = s[1], s[2]
            if status == "FAIL":
                p.imputed += p.cost * qty / p.tokens
                self.n["failed_exits"] += 1
                p.reasons.append(why + ":FAIL")
            else:
                p.proceeds += usd_out - r["net_fee_usd"]
                self.n["exit_fills"] += 1
                p.reasons.append(why)
            p.sold_tokens += qty
            p.stage = max(p.stage, {"TP1": 1, "TP2": 2}.get(why, p.stage))
            if p.tokens - p.sold_tokens <= 1e-9 * p.tokens:
                p.closed = s[0]
                self.done.append(p)
                del self.open[mint]

    def summary(self):
        done = self.done
        net = sum(p.proceeds - p.cost for p in done) if done else None
        return {**self.n, "entries": len(done) + len(self.open), "closed": len(done), "net": net,
                "net_without_best": (net - max(p.proceeds - p.cost for p in done)) if done else None,
                "open": len(self.open), "imputed": sum(p.imputed for p in done + list(self.open.values())),
                "pending_exit_attempts": self.n["exit_no_quote"], "failed_exits": self.n["failed_exits"],
                "skipped": [None] * self.n["no_buy_quote"],
                "trades": [{"mint": p.mint, "opened": p.opened.isoformat(), "closed": p.closed.isoformat(),
                            "pnl": p.proceeds - p.cost, "imputed": p.imputed, "exits": p.reasons} for p in done]}


def backtest(obs: List[Obs], decisions: List[dict], quotes, latency_s: int, r=RULES, entry_cutoff=None):
    """Offline replay over recorded observations and recorded quotes (same Engine as the live probe)."""
    eng = Engine(quotes, latency_s, r, entry_cutoff)
    dec = sorted(decisions, key=lambda d: d["ts"])
    di = 0
    for o in sorted(obs, key=lambda o: o.ts):
        eng.tick(o.ts)
        while di < len(dec) and dec[di]["ts"] <= o.ts:
            eng.on_decision(dec[di]); di += 1
        eng.tick(o.ts)
        eng.on_obs(o)
        eng.tick(o.ts)
    while di < len(dec):
        eng.on_decision(dec[di]); di += 1
    if obs:
        eng.tick(max(o.ts for o in obs) + timedelta(seconds=latency_s + r["quote_tolerance_s"] + 1))
    return eng.summary()


class LiveQuotes(RecordedQuotes):
    """Live provider for the probe: reuses a recorded quote if one already satisfies the request (so the offline replay
    sees exactly the same fills), otherwise asks once - only while now <= due + tolerance and within the request caps.
    Every answer is appended to `path` with its real request time. Read-only: Jupiter quote + unsigned simulateTransaction
    of a public holder (sigVerify false); nothing is signed or sent."""

    def __init__(self, path, buy_fn, sell_fn, holders_fn, max_buy=60, max_sell=120, clock=None):
        super().__init__()
        self.path, self.buy_fn, self.sell_fn, self.holders_fn = path, buy_fn, sell_fn, holders_fn
        self.max_buy, self.max_sell, self.used_buy, self.used_sell = max_buy, max_sell, 0, 0
        self.clock = clock
        self.events = defaultdict(int)

    def _log(self, rec):
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _missing(self, side, mint, due, now, reason, **kw):
        self.events[f"{side}_{reason}"] += 1
        self._log({"kind": f"{side}_missing", "ts": now.isoformat(), "due": due.isoformat(), "mint": mint,
                   "reason": reason, **kw})
        return None

    def _now(self, now):
        return self.clock() if self.clock else now

    def buy(self, mint, usd, due, tol_s, now=None):
        hit = super().buy(mint, usd, due, tol_s)
        if hit or now is None:
            return hit
        now = self._now(now)
        if (now - due).total_seconds() > tol_s:
            return self._missing("buy", mint, due, now, "too_late", usd_in=usd)
        if self.used_buy >= self.max_buy:
            return self._missing("buy", mint, due, now, "request_cap", usd_in=usd)
        self.used_buy += 1
        q = self.buy_fn(mint, usd)
        if q.get("status") != "OK":
            self.events["buy_" + q.get("status", "UNKNOWN")] += 1
            self._log({"kind": "buy_error", "ts": now.isoformat(), "due": due.isoformat(), "mint": mint, "usd_in": usd,
                       "status": q.get("status"), "stage": q.get("stage"), "detail": str(q.get("detail"))[:200]})
            return None
        self.buys[mint].append((now, usd, q["tokens"]))
        self._log({"kind": "buy", "ts": now.isoformat(), "due": due.isoformat(), "mint": mint, "usd_in": usd, "tokens_out": q["tokens"],
                   "quoted_impact": q.get("quoted_impact")})
        return now, q["tokens"]

    def sell(self, mint, tokens, due, tol_s, now=None):
        hit = super().sell(mint, tokens, due, tol_s)
        if hit or now is None:
            return hit
        now = self._now(now)
        if (now - due).total_seconds() > tol_s:
            return self._missing("sell", mint, due, now, "too_late", amount_in=tokens)
        if self.used_sell >= self.max_sell:
            return self._missing("sell", mint, due, now, "request_cap", amount_in=tokens)
        self.used_sell += 1
        r = self.sell_fn(mint, self.holders_fn(mint), tokens)
        st = r.get("status")
        if st == "OK":
            gross = (r["quote_out_lamports"] / 1e9 * r["sol_usd"]) if r.get("quote_out_lamports") and r.get("sol_usd") \
                else r["proceeds_usd"] + RULES["net_fee_usd"]
            rec = (now, tokens, gross, "PASS")
        elif st == "FAIL":
            rec = (now, tokens, None, "FAIL")
        else:
            self.events["sell_UNKNOWN"] += 1
            self._log({"kind": "sell_error", "ts": now.isoformat(), "due": due.isoformat(), "mint": mint, "amount_in": tokens, "status": st,
                       "stage": r.get("stage"), "detail": str(r.get("detail"))[:200]})
            return None
        self.sells[mint].append(rec)
        self._log({"kind": "sell", "ts": now.isoformat(), "due": due.isoformat(), "mint": mint, "amount_in": tokens, "usd_out": rec[2],
                   "status": rec[3], "stage": r.get("stage"), "detail": str(r.get("detail"))[:200]})
        return now, rec[2], rec[3]
