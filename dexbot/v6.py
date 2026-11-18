"""v6 - the screenshot strategy, implemented as an independent paper strategy (separate from v5; nothing here is used by v5).

Each screenshot rule maps to code below; rules that need a list or a feed we do not have are kept in the code path,
tested offline and reported as NEVERIFICAT LIVE / UNKNOWN (never PASS):
* universe: age <= 6 h; DEX pump.fun / pumpswap / Raydium / Meteora only; liquidity, volume, holders, mint, freeze as v1;
  LP judged on the TRADED pool (not the max over all pools - defect D8), >= 95 % locked/burned; lock duration is not
  verifiable (RugCheck lockers empty) -> recorded as a limitation.
* signals: volume spike vs the 1 h average (for pools younger than 1 h: average over the 5-min windows that existed),
  buy share > 70 %, holder growth, SMART_MONEY (>= 3 of the tracked wallets buying within 10 min), KOL (a tracked KOL
  wallet actually bought within 10 min). No verified wallet list -> both UNKNOWN, never counted.
* exits: v1 ladder and stops plus DEV_SELL (creator token balance fell since entry, read over RPC) and LP_PULLED
  (traded pool liquidity < 50 % of the value at entry: proxy). Triggers only start an exit attempt; the fill needs a
  passing sell simulation of the exact amount.
* no chase: +300 % in 1 h without a >= 10 % pullback from the 1 h high (high from GeckoTerminal 5 m candles of the
  traded pool; unavailable -> treated as no pullback -> skip).
* session: entries 23:00-07:00 Europe/Bucharest; 07:00 recap; daily drawdown 30 % from the session's peak -> pause.
* sizer: equity-based 20 / 30 / 40 % (2 / 3 / >= 4 signals), x0.5 after two losses in a row, compounding, $70 paper."""
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .agents import Equity, RugCheck, Scanner
from .models import Pair

TZ = ZoneInfo("Europe/Bucharest")
ALLOWED_DEX = ("pumpfun", "pumpswap", "raydium", "meteora")
SMART_MIN, WALLET_WINDOW_S = 3, 600


def session_of(t: datetime) -> Optional[str]:
    lt = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(TZ)
    if lt.time() >= dtime(23, 0):
        return lt.date().isoformat()
    if lt.time() < dtime(7, 0):
        return (lt.date() - timedelta(days=1)).isoformat()
    return None


def lp_traded_pool(p: Pair):
    """(ok, reason). Screenshot rule: LP burned, or locked >= 30 days. Only the TRADED pool counts (D8).
    Burned = the traded pool's LP mint has on-chain supply 0 (read over RPC, `p.lp_burned`). A locked percentage does
    not prove a 30-day lock and RugCheck lockers carry no usable dates, so anything not verified burned fails."""
    if p.lp_burned is True:
        return True, "LP_BURNED: traded pool LP mint supply = 0 on chain"
    pct = (p.lp_by_market or {}).get(p.pair_addr) if p.pair_addr else None
    if p.lp_burned is False:
        return False, f"LP not burned (traded pool LP supply > 0, {0 if pct is None else pct:.1f}% reported locked; 30-day lock not verifiable)"
    return False, "LP UNKNOWN for the traded pool (burn not verified)"


def lp_burned_onchain(rpc, lp_mint) -> Optional[bool]:
    """True if the LP mint's supply is 0, False if > 0, None if unknown (RPC error / no mint)."""
    if not lp_mint or lp_mint == "11111111111111111111111111111111":
        return None
    try:
        v = (rpc("getTokenSupply", [lp_mint]) or {}).get("value") or {}
        return int(v["amount"]) == 0
    except Exception:
        return None


def vol_avg_5m(p: Pair) -> float:
    """1 h average 5-min volume; for pools younger than 1 h, average over the windows that existed."""
    h1 = p.vol_avg5m_1h * 12
    windows = 12 if p.age_s >= 3600 else max(1.0, p.age_s / 300)
    return h1 / windows


class V6Scanner(Scanner):
    def signals(self, p: Pair) -> List[str]:
        c, s = self.cfg, []
        avg = vol_avg_5m(p)
        if avg > 0 and p.vol5m > c.spike_mult * avg:
            s.append("VOL_SPIKE")
        tot = p.buys5m + p.sells5m
        if tot >= 20 and p.buys5m / tot > c.buy_ratio_min:
            s.append("BUY_RATIO")
        if p.holders and p.holders_10m and p.holders >= p.holders_10m * (1 + c.holder_growth_min):
            s.append("HOLDER_GROWTH")
        if p.smart_buyers_10m >= SMART_MIN:
            s.append("SMART_MONEY")
        if p.kol_bought:
            s.append("KOL")
        return s

    def universe_fail(self, p: Pair):
        if not p.dex_id or not p.dex_id.startswith(ALLOWED_DEX):
            return f"DEX {p.dex_id} not in pump.fun/Raydium/Meteora"
        ok, why = lp_traded_pool(p)
        lp_status = "burned" if ok else ("unlocked" if p.lp_burned is False else None)
        r = super().universe_fail(Pair(**{**p.__dict__, "lp_status": lp_status}))
        if r and r.startswith("LP"):
            return why
        return r


class V6RugCheck(RugCheck):
    def check(self, p: Pair, skip_sell: bool = False):
        ok, why = lp_traded_pool(p)
        q = Pair(**{**p.__dict__, "lp_status": "burned" if ok else None})
        res = super().check(q, skip_sell)
        if not res[0] and res[1].startswith("LP"):
            return False, why
        return res


class WalletSignals:
    """Counts BUY events from documented public wallets. kind = 'smart' | 'kol'. Empty list -> UNKNOWN."""

    def __init__(self, wallets: List[dict]):
        self.kind = {w["address"]: w.get("kind", "smart") for w in wallets}
        self.events: Dict[str, List[tuple]] = {}
        self.status = "LISTA LIPSA -> SMART_MONEY / KOL UNKNOWN" if not wallets else f"{len(wallets)} documented wallets"

    def add(self, ev):                                       # ev: signals_free.WalletBuy (confirmed by signature + mint)
        self.events.setdefault(ev.mint, []).append((ev.swap_time, ev.wallet))

    def hook(self, mint: str, now: Optional[float] = None):
        now = now or time.time()
        recent = [(t, w) for t, w in self.events.get(mint, []) if now - t <= WALLET_WINDOW_S]
        smart = {w for t, w in recent if self.kind.get(w) == "smart"}
        kol = any(self.kind.get(w) == "kol" for t, w in recent)
        return len(smart), kol


class DevLpWatch:
    """Exit triggers for held positions (rpc = the approved Solana RPC; stubbed in tests).
    DEV_SELL fires only when a creator transaction is confirmed as a SELL swap of this mint (signature + mint). A balance
    drop alone is logged as DEV_BALANCE_DROP (could be a transfer) and does not trigger an exit. LP pull: entries require
    the traded pool's LP to be burned on chain, so it cannot be withdrawn; a liquidity drop >= 50 % is only logged."""

    def __init__(self, rpc):
        self.rpc, self.entry, self.events = rpc, {}, []

    def creator_balance(self, creator: str, mint: str) -> Optional[float]:
        try:
            r = self.rpc("getTokenAccountsByOwner", [creator, {"mint": mint}, {"encoding": "jsonParsed"}]) or {}
            return sum(float(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0) for a in r.get("value", []))
        except Exception:
            return None

    def confirmed_dev_sell(self, creator: str, mint: str, since: float):
        from .signals_free import parse_sell
        try:
            sigs = self.rpc("getSignaturesForAddress", [creator, {"limit": 10}]) or []
        except Exception:
            return None
        for s_ in sigs:
            if (s_.get("blockTime") or 0) < since or s_.get("err") is not None:
                continue
            try:
                tx = self.rpc("getTransaction", [s_["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            except Exception:
                continue
            if tx and parse_sell(tx, creator, mint):
                return s_["signature"]
        return None

    def on_entry(self, p: Pair):
        self.entry[p.addr] = {"liq": p.liquidity, "t": time.time(),
                              "dev": self.creator_balance(p.creator, p.addr) if p.creator else None, "warned": set()}

    def apply(self, p: Pair) -> Pair:
        e = self.entry.get(p.addr)
        if not e:
            return p
        if e["dev"] is not None and p.creator:
            now_bal = self.creator_balance(p.creator, p.addr)
            if now_bal is not None and now_bal < e["dev"] - 1e-9:
                sig = self.confirmed_dev_sell(p.creator, p.addr, e["t"])
                if sig:
                    p.dev_sold = True
                    self.events.append(("DEV_SELL", p.addr, sig))
                elif "drop" not in e["warned"]:
                    e["warned"].add("drop")
                    self.events.append(("DEV_BALANCE_DROP", p.addr, "no confirmed sell swap; no exit"))
        if e["liq"] and p.liquidity is not None and p.liquidity < 0.5 * e["liq"] and "liq" not in e["warned"]:
            e["warned"].add("liq")
            self.events.append(("LIQ_DROP_50", p.addr, "warning only; LP burned at entry, not an LP pull"))
        return p


def pullback_from_candles(price: float, candles: List[list]) -> Optional[float]:
    """candles: GeckoTerminal ohlcv_list [ts, o, h, l, c, v] covering the last hour. None if unavailable."""
    highs = [c[2] for c in candles or [] if c and c[2]]
    if not highs or not price:
        return None
    return max(0.0, 1 - price / max(highs))


class SessionEquity(Equity):
    """Daily drawdown measured per 23:00-07:00 Bucharest session (outside a session: per local date)."""

    def check_drawdown(self, now: datetime, equity: float, limit: float):
        key = session_of(now) or (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(TZ).date().isoformat()
        if key != getattr(self, "_key", None):
            self._key, self.peak = key, equity
            if self.paused:
                self.paused = False
                self.log.log(now, "RESUME", f"new session {key}, peak reset")
        self.peak = max(self.peak, equity)
        dd = self.drawdown(equity)
        if dd >= limit and not self.paused:
            self.paused = True
            self.log.log(now, "PAUSE", f"session drawdown {dd:.1%} >= {limit:.0%} from peak ${self.peak:,.2f}; entries stop, "
                                       "open positions keep their exit rules (not liquidated)")
        self.status = "PAUSED (drawdown)" if self.paused else f"dd {dd:.1%}"


def recap(trades, start_balance, end_balance) -> dict:
    """07:00 recap of one session."""
    if not trades:
        return {"start": start_balance, "end": end_balance, "trades": 0, "best": None, "worst": None, "top3": []}
    pn = [(t.symbol, t.pnl) for t in trades]
    agg = {}
    for s, v in pn:
        agg[s] = agg.get(s, 0) + v
    return {"start": start_balance, "end": end_balance, "trades": len(pn), "best": max(pn, key=lambda x: x[1]),
            "worst": min(pn, key=lambda x: x[1]), "top3": sorted(agg.items(), key=lambda kv: -kv[1])[:3]}
