"""Live market data (read-only): DexScreener for market stats, RugCheck.xyz for security.
Anything the APIs do not give us is left as None, and RugCheck/Scanner treat None as FAIL,
so missing data can never produce an entry. Smart-money / KOL signals need a wallet-tracking
source (e.g. Helius webhooks) and stay off until you plug one into `wallet_hook`."""
import json
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .config import Config
from .models import Pair

DS = "https://api.dexscreener.com"
RC = "https://api.rugcheck.xyz/v1/tokens/{}/report"
SOURCES = {"pumpfun": "pump.fun", "pumpswap": "pump.fun", "raydium": "raydium", "meteora": "meteora"}


def _get(url: str, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "dexbot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class LiveFeed:
    def __init__(self, cfg: Config, wallet_hook: Optional[Callable[[str], tuple]] = None):
        self.cfg = cfg
        self.wallet_hook = wallet_hook          # addr -> (smart_buyers_10m, kol_bought)
        self.cache: Dict[str, Pair] = {}
        self.sec: Dict[str, dict] = {}
        self.holder_hist = defaultdict(lambda: deque(maxlen=600))
        self.last_discovery = 0.0
        self.watch: List[str] = []

    def _discover(self):
        if time.time() - self.last_discovery < 60:
            return
        self.last_discovery = time.time()
        addrs = []
        for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1"):
            try:
                addrs += [x["tokenAddress"] for x in _get(DS + ep) if x.get("chainId") == "solana"]
            except Exception:
                pass
        self.watch = list(dict.fromkeys(addrs + self.watch))[:200]

    def _security(self, mint: str) -> dict:
        if mint in self.sec:
            return self.sec[mint]
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
            out["honeypot"] = None   # needs a real sell simulation (e.g. Jupiter quote); unknown = FAIL
        except Exception:
            pass
        self.sec[mint] = out
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
        s = self._security(a) if (r.get("liquidity") or {}).get("usd", 0) >= self.cfg.min_liq else {}
        h = s.get("holders")
        if h:
            self.holder_hist[a].append((time.time(), h))
        old = [x for t, x in self.holder_hist[a] if time.time() - t >= 600]
        sm, kol = self.wallet_hook(a) if self.wallet_hook else (0, False)
        return Pair(
            addr=a, symbol=r["baseToken"].get("symbol", "?"),
            source=SOURCES.get(r.get("dexId", ""), r.get("dexId", "?")),
            age_s=time.time() - r["pairCreatedAt"] / 1000, price=price,
            liquidity=(r.get("liquidity") or {}).get("usd", 0), vol5m=v.get("m5", 0),
            vol_avg5m_1h=v.get("h1", 0) / 12, buys5m=m5.get("buys", 0), sells5m=m5.get("sells", 0),
            holders=h, holders_10m=old[-1] if old else None,
            price_1h_ago=price / (1 + ch1 / 100) if ch1 is not None else None,
            pullback_pct=None,           # unknown -> no-chase rule stays strict
            smart_buyers_10m=sm, kol_bought=kol,
            dev_pct=s.get("dev_pct"), top10_pct=s.get("top10_pct"), bundled=s.get("bundled"),
            lp_status=s.get("lp_status"), mint_frozen=s.get("mint_frozen"),
            mint_auth_open=s.get("mint_auth_open"), honeypot=s.get("honeypot"))

    def snapshot(self) -> List[Pair]:
        return list(self.cache.values())

    def get(self, addr: str) -> Optional[Pair]:
        return self.cache.get(addr)
