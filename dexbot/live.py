"""Live market data (read-only): DexScreener for market stats, RugCheck.xyz for security.
Anything the APIs do not give us is left as None, and RugCheck/Scanner treat None as FAIL,
so missing data can never produce an entry. Smart-money / KOL signals need a wallet-tracking
source (e.g. Helius webhooks) and stay off until you plug one into `wallet_hook`."""
import base64
import json
import struct
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .config import Config
from .models import Pair

DS = "https://api.dexscreener.com"
RC = "https://api.rugcheck.xyz/v1/tokens/{}/report"
RPC = "https://api.mainnet-beta.solana.com"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
CURVE_DEX = ("pump-fun", "pumpfun", "dbc", "launchlab", "bags", "moonshot", "moonit", "boop")   # launch curves, not migrated pools
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SEC_TTL, SEC_RETRY, CURVE_TTL = 300, 60, 60
SOURCES = {"pumpfun": "pump.fun", "pumpswap": "pump.fun", "raydium": "raydium", "meteora": "meteora"}


def _get(url: str, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "dexbot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class LiveFeed:
    def __init__(self, cfg: Config, wallet_hook: Optional[Callable[[str], tuple]] = None, gate_cfgs=None):
        self.cfg = cfg
        # fetch security data for any pair that could pass the LOOSEST variant's floors (data coverage, not a threshold)
        gates = list(gate_cfgs or [cfg])
        self.gate_vol = min(c.min_vol5m for c in gates)
        self.gate_liq = min(c.min_liq for c in gates)
        self.curve: Dict[str, tuple] = {}       # pair addr -> (fetched_at, liquidity_usd or None)
        self.decimals: Dict[str, Optional[int]] = {}
        self.wallet_hook = wallet_hook          # addr -> (smart_buyers_10m, kol_bought)
        self.cache: Dict[str, Pair] = {}
        self.sec: Dict[str, dict] = {}
        self.holder_hist = defaultdict(lambda: deque(maxlen=600))
        self.last_discovery = 0.0
        self.last_gt, self.gt_turn = 0.0, 0
        self.disc_src: Dict[str, set] = defaultdict(set)            # mint -> discovery sources
        self.gt_pools: Dict[str, tuple] = {}                        # pool address -> (mint, dex, source), deduped
        self.disc_errors = defaultdict(lambda: defaultdict(int))
        self.pinned: set = set()
        self.watch: List[str] = []

    def pin(self, addr: str):
        """Keep a held token in the watch list so its price keeps updating."""
        self.pinned.add(addr)
        if addr not in self.watch:
            self.watch.insert(0, addr)

    def _gt_pools(self, path):
        """GeckoTerminal pools (public, keyless, ~30 calls/min shared per IP). Returns [(pool, mint, dex, created_ts)] or None."""
        try:
            req = urllib.request.Request(f"{GT}/{path}", headers={"User-Agent": "dexbot/0.1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r).get("data") or []
        except urllib.error.HTTPError as e:
            self.disc_errors[f"gt_{path.split('?')[0]}"][f"http {e.code}"] += 1
            return None
        except Exception as e:
            self.disc_errors[f"gt_{path.split('?')[0]}"][type(e).__name__] += 1
            return None
        out = []
        for x in data:
            try:
                a, rel = x["attributes"], x["relationships"]
                mint = rel["base_token"]["data"]["id"].split("_", 1)[1]
                dex = rel["dex"]["data"]["id"]
                created = a.get("pool_created_at")
                ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() if created else None
                out.append((a["address"], mint, dex, ts))
            except Exception:
                continue
        return out

    def sources(self, mint: str) -> List[str]:
        return sorted(self.disc_src.get(mint, ()))

    def _discover(self):
        """Discovery only: which tokens to look at. Boosts/profiles/trending are NOT buy signals."""
        if time.time() - self.last_discovery < 60:
            return
        self.last_discovery = time.time()
        found: Dict[str, List[str]] = defaultdict(list)
        for src, ep in (("ds_profiles", "/token-profiles/latest/v1"), ("ds_boosts_latest", "/token-boosts/latest/v1"),
                        ("ds_boosts_top", "/token-boosts/top/v1")):
            try:
                for x in _get(DS + ep):
                    if x.get("chainId") == "solana":
                        found[src].append(x["tokenAddress"])
            except Exception as e:
                self.disc_errors[src][type(e).__name__] += 1
        if time.time() - self.last_gt >= 120:          # one GeckoTerminal call every 2 min, alternating endpoints
            self.last_gt = time.time()
            path = ("new_pools?page=1", "trending_pools?page=1")[self.gt_turn % 2]
            self.gt_turn += 1
            src = "gt_" + path.split("?")[0]
            for pool, mint, dex, ts in self._gt_pools(path) or []:
                if any(c in dex for c in CURVE_DEX):
                    continue                            # still on a launch curve: not a migrated pool
                if ts is not None and time.time() - ts > self.cfg.max_age_s:
                    continue                            # older than 6 h: outside the universe
                if pool not in self.gt_pools:           # dedupe by pool address
                    self.gt_pools[pool] = (mint, dex, src)
                found[src].append(mint)
        for src, mints in found.items():
            for m in mints:
                self.disc_src[m].add(src)
        extended = [m for src in ("gt_new_pools", "gt_trending_pools", "ds_boosts_top") for m in found.get(src, [])]
        base = found.get("ds_profiles", []) + found.get("ds_boosts_latest", [])
        pinned = [a for a in self.watch if a in self.pinned]
        self.watch = list(dict.fromkeys(pinned + extended + base + self.watch))[:300]

    def _rpc(self, method, params):
        req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                     headers={"User-Agent": "dexbot/0.1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("result")

    def _curve_liquidity(self, r: dict) -> Optional[float]:
        """pump.fun bonding curve (DexScreener gives liquidity=null): read the curve account over RPC.
        Liquidity = 2 x real quote reserves in USD (same two-sided convention as an AMM pool). Any doubt -> None (UNKNOWN)."""
        pa, q = r.get("pairAddress"), (r.get("quoteToken") or {}).get("address")
        hit = self.curve.get(pa)
        if hit and time.time() - hit[0] < CURVE_TTL:
            return hit[1]
        val = None
        try:
            v = (self._rpc("getAccountInfo", [pa, {"encoding": "base64"}]) or {}).get("value")
            if v and v.get("owner") == PUMP_PROGRAM:
                raw = base64.b64decode(v["data"][0])
                if len(raw) >= 49 and raw[48] == 0:                  # complete == 0: still on the curve
                    real_quote = struct.unpack_from("<Q", raw, 32)[0]
                    if q not in self.decimals:
                        self.decimals[q] = ((self._rpc("getTokenSupply", [q]) or {}).get("value") or {}).get("decimals")
                    dec = self.decimals[q]
                    pu, pn = float(r.get("priceUsd") or 0), float(r.get("priceNative") or 0)
                    if dec is not None and pu > 0 and pn > 0:
                        val = 2 * real_quote / 10 ** dec * (pu / pn)    # priceUsd / priceNative = USD price of the quote token
        except Exception:
            val = None
        self.curve[pa] = (time.time(), val)
        return val

    def _security(self, mint: str) -> dict:
        hit = self.sec.get(mint)
        if hit and time.time() - hit[0] < (SEC_TTL if hit[1] else SEC_RETRY):
            return hit[1]
        out = {}
        try:
            r = _get(RC.format(mint))
            tops = r.get("topHolders") or []
            out["top10_pct"] = sum(h.get("pct", 0) for h in tops[:10]) if tops else None
            out["mint_auth_open"] = r.get("mintAuthority") is not None
            out["mint_frozen"] = r.get("freezeAuthority") is not None
            out["holders"] = r.get("totalHolders")
            locked = max([(m.get("lp") or {}).get("lpLockedPct", 0) for m in r.get("markets") or []] or [0])
            out["lp_status"] = "burned" if locked >= 95 else "locked_short" if locked > 0 else "unlocked"
            creator = r.get("creatorBalance")
            supply = (r.get("token") or {}).get("supply")
            out["dev_pct"] = creator / supply * 100 if creator is not None and supply else None
            risks = [x.get("name", "").lower() for x in r.get("risks") or []]
            out["bundled"] = any("bundl" in x or "snip" in x for x in risks)
            pools = {m.get("pubkey") for m in r.get("markets") or []}
            for h in tops:
                if h.get("owner") not in pools and not h.get("insider") and h.get("amount", 0) >= 10 ** 8:
                    out["sell_holder"] = (h["owner"], h["amount"])     # public holder for the sell simulation
                    break
            out["honeypot"] = None   # SELL_SIMULATION unavailable. A quote (SELL_QUOTE) is not a simulation. UNKNOWN = reject
        except Exception:
            pass
        self.sec[mint] = (time.time(), out)
        return out

    def step(self, now: datetime):
        self._discover()
        for i in range(0, len(self.watch), 30):
            chunk = self.watch[i:i + 30]
            try:
                rows = _get(f"{DS}/latest/dex/tokens/{','.join(chunk)}").get("pairs") or []
            except Exception:
                continue
            best: Dict[str, dict] = {}
            for r in rows:
                if r.get("chainId") != "solana" or not r.get("pairCreatedAt"):
                    continue
                a = r["baseToken"]["address"]
                if a not in best or (r.get("liquidity") or {}).get("usd", 0) > (best[a].get("liquidity") or {}).get("usd", 0):
                    best[a] = r
            for a, r in best.items():
                self.cache[a] = self._to_pair(a, r)

    def _to_pair(self, a: str, r: dict) -> Pair:
        price = float(r.get("priceUsd") or 0)
        m5 = (r.get("txns") or {}).get("m5") or {}
        v = r.get("volume") or {}
        ch1 = (r.get("priceChange") or {}).get("h1")
        liq = (r.get("liquidity") or {}).get("usd")      # None stays None (unknown)
        liq_src = "dexscreener" if liq is not None else None
        if liq is None and r.get("dexId") == "pumpfun":
            liq = self._curve_liquidity(r)
            liq_src = "pump_curve_rpc" if liq is not None else None
        # only spend a RugCheck call on pairs that could pass the loosest variant's floors
        s = self._security(a) if (v.get("m5") or 0) >= self.gate_vol and (liq is None or liq >= self.gate_liq) else {}
        h = s.get("holders")
        if h:
            self.holder_hist[a].append((time.time(), h))
        old = [x for t, x in self.holder_hist[a] if time.time() - t >= 600]
        sm, kol = self.wallet_hook(a) if self.wallet_hook else (0, False)
        return Pair(
            addr=a, pair_addr=r.get("pairAddress"), symbol=r["baseToken"].get("symbol", "?"),
            source=SOURCES.get(r.get("dexId", ""), r.get("dexId", "?")),
            age_s=time.time() - r["pairCreatedAt"] / 1000, price=price,
            liquidity=liq, liquidity_source=liq_src, vol5m=v.get("m5") or 0,
            vol_avg5m_1h=v.get("h1", 0) / 12, buys5m=m5.get("buys", 0), sells5m=m5.get("sells", 0),
            holders=h, holders_10m=old[-1] if old else None,
            price_1h_ago=price / (1 + ch1 / 100) if ch1 is not None else None,
            pullback_pct=None,           # unknown -> no-chase rule stays strict
            smart_buyers_10m=sm, kol_bought=kol,
            dev_pct=s.get("dev_pct"), top10_pct=s.get("top10_pct"), bundled=s.get("bundled"),
            sell_holder=s.get("sell_holder"), lp_status=s.get("lp_status"), mint_frozen=s.get("mint_frozen"),
            mint_auth_open=s.get("mint_auth_open"), honeypot=s.get("honeypot"))

    def refresh(self, addr: str) -> Optional[Pair]:
        """Fresh quote for one token, used for the modelled entry (not the price seen at signal time)."""
        try:
            rows = _get(f"{DS}/latest/dex/tokens/{addr}").get("pairs") or []
        except Exception:
            return None
        rows = [r for r in rows if r.get("chainId") == "solana" and r.get("pairCreatedAt") and r["baseToken"]["address"] == addr]
        if not rows:
            return None
        r = max(rows, key=lambda r: (r.get("liquidity") or {}).get("usd") or 0)
        p = self._to_pair(addr, r)
        self.cache[addr] = p
        return p

    def snapshot(self) -> List[Pair]:
        return list(self.cache.values())

    def get(self, addr: str) -> Optional[Pair]:
        return self.cache.get(addr)
