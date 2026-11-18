"""SIGNALS_FREE - read-only discovery events, kept apart from v1/v2/v3 (nothing here changes their rules or decides a BUY).

Two event types, never mixed:
* MIGRATION  - a token that had a launch-curve pool now has an AMM pool. Puts the pool on a WATCH list with its migration
               time. It is not a buy signal. Eligibility for a v3-style test still needs pool age >= 6 h (v3 rule); a pool
               is never marked eligible earlier.
* WALLET_BUY - a confirmed swap (by signature and mint, never by ticker) in which a PUBLIC wallet with a DOCUMENTED source
               received a token and paid SOL / a quote token, with no transaction error. Transfers are excluded.
               Logged: swap time (blockTime), detection time, delay, and the price available to us AFTER detection.
               The tracked wallet's own price is informational only and is never used as our fill.

Sources:
* GeckoTerminal new pools + DexScreener token-pairs (hosts already approved)            -> MIGRATION, enabled.
* Solana RPC getSignaturesForAddress / getTransaction (host already approved)            -> WALLET_BUY, needs a wallet list.
* PumpPortal websocket `subscribeMigration` (wss://pumpportal.fun/api/data)              -> PROPOSED ONLY, disabled: host not
  approved, topic not confirmed from official docs, and the Python stdlib has no websocket client. subscribeAccountTrade is
  never used."""
import json
import os
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .sellsim import RPC, SOL, http_json

GT_NEW = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"
DS_TOKEN_PAIRS = "https://api.dexscreener.com/token-pairs/v1/solana/{}"
CURVE_DEX = {"pumpfun", "meteoradbc", "launchlab", "raydiumlaunchlab", "bags", "moonshot", "moonit", "boop"}
QUOTES = {SOL}                                   # SOL; quote-token decreases (USDC etc.) are counted by mint below
WALLETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "wallets_public.json")
PUMPPORTAL_ENABLED = False                       # stays False until the host is approved and the topic is confirmed


@dataclass
class Migration:
    mint: str
    amm_pool: str
    amm_dex: str
    curve_pool: str
    curve_dex: str
    amm_created_at: float                        # DexScreener pairCreatedAt of the AMM pool (what the v3 age rule uses)
    detected_at: float
    source: str
    status: str = "MIGRATION_CANDIDATE"          # MIGRATION only when the curve is verified complete on-chain
    migrated_at: Optional[float] = None          # only if an exact on-chain time is verified (not implemented -> stays None)
    migration_time_estimate: Optional[float] = None   # AMM pool creation time, labelled as an estimate
    curve_last_tx_at: Optional[float] = None     # informational: the curve can still receive transactions after migration
    verify_note: str = ""

    def age_h(self, now=None):
        return ((now or time.time()) - self.amm_created_at) / 3600

    def eligible_for_v3(self, now=None):
        return self.age_h(now) >= 6.0            # v3 minimum pool age; never earlier, verified or not


@dataclass
class WalletBuy:
    wallet: str
    wallet_label: str
    wallet_source: str
    signature: str
    mint: str
    token_amount: float
    paid_sol: float
    swap_time: float
    detected_at: float
    delay_s: float
    price_after_detection_usd: Optional[float] = None
    wallet_implied_price_sol: Optional[float] = None   # informational only, never our fill


@dataclass
class MigrationWatch:
    """GeckoTerminal new pools -> DexScreener token-pairs confirms curve + AMM -> MIGRATION event (deduped by pool)."""
    seen: Dict[str, Migration] = field(default_factory=dict)
    errors: Dict[str, int] = field(default_factory=dict)

    def poll(self, gt_fetch=None, ds_fetch=None, verify=None) -> List[Migration]:
        now = time.time()
        try:
            data = (gt_fetch or _gt_new)()
        except Exception as e:
            self.errors[type(e).__name__] = self.errors.get(type(e).__name__, 0) + 1
            return []
        new = []
        for x in data:
            try:
                mint = x["relationships"]["base_token"]["data"]["id"].split("_", 1)[1]
            except Exception:
                continue
            s, pools = (ds_fetch or (lambda m: http_json(DS_TOKEN_PAIRS.format(m))))(mint)
            if s != 200 or not isinstance(pools, list):
                self.errors[f"token-pairs http {s}"] = self.errors.get(f"token-pairs http {s}", 0) + 1
                continue
            curve = [p for p in pools if p.get("dexId") in CURVE_DEX]
            amm = [p for p in pools if p.get("dexId") not in CURVE_DEX and p.get("pairCreatedAt")]
            if not curve or not amm:
                continue                          # no launch curve -> not a migration; no AMM -> still on the curve
            a = min(amm, key=lambda p: p["pairCreatedAt"])
            if a["pairAddress"] in self.seen:
                continue
            c = curve[0]
            m = Migration(mint, a["pairAddress"], a["dexId"], c.get("pairAddress", ""), c["dexId"], a["pairCreatedAt"] / 1000, now,
                          "gt_new_pools+ds_token_pairs")
            (verify or verify_pump_curve)(m)
            self.seen[a["pairAddress"]] = m
            new.append(m)
        return new


def verify_pump_curve(m: Migration, rpc=None):
    """pump.fun only: the curve account (DexScreener pairAddress of the pumpfun pool) must be owned by the pump program
    with complete == 1 -> MIGRATION (verified that it migrated). The exact time is not verified: it is reported as an
    estimate (AMM pool creation), because the curve can receive transactions after migrating (seen live on BUCKZ).
    Anything else stays a MIGRATION_CANDIDATE with the reason."""
    import base64
    rpc = rpc or _rpc
    if m.curve_dex != "pumpfun" or not m.curve_pool:
        m.verify_note = f"no on-chain check for curve type {m.curve_dex}"
        return m
    try:
        v = (rpc("getAccountInfo", [m.curve_pool, {"encoding": "base64"}]) or {}).get("value")
        if not v or v.get("owner") != "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
            m.verify_note = "curve account missing or not owned by the pump program"
            return m
        raw = base64.b64decode(v["data"][0])
        if len(raw) < 49 or raw[48] != 1:
            m.verify_note = "curve not complete (complete flag != 1)"
            return m
        sig = rpc("getSignaturesForAddress", [m.curve_pool, {"limit": 1}]) or []
        m.curve_last_tx_at = float(sig[0]["blockTime"]) if sig and sig[0].get("blockTime") else None
        m.status = "MIGRATION"
        m.migration_time_estimate = m.amm_created_at
        m.verify_note = ("pump curve complete=1 (migration verified); exact migration time NOT verified - estimate = AMM pool "
                         "creation; the curve's last transaction is informational only")
    except Exception as e:
        m.verify_note = f"verification error {type(e).__name__}"
    return m


def _gt_new():
    req = urllib.request.Request(GT_NEW, headers={"User-Agent": "dexbot/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("data") or []


def load_wallets(path=WALLETS_FILE):
    """Only entries with a public address AND a documented source are accepted. Returns (wallets, status)."""
    try:
        raw = json.load(open(path))
    except FileNotFoundError:
        return [], "LISTA LIPSA (no config/wallets_public.json)"
    ok = [w for w in raw if w.get("address") and w.get("source_url") and w.get("source_note")]
    if not ok:
        return [], "LISTA LIPSA (no wallet with a documented public source)"
    return ok, f"{len(ok)} documented wallet(s)"


def parse_buy(tx: dict, wallet: str):
    """Confirmed swap BUY for `wallet` from a jsonParsed transaction, else None.
    BUY = no error, wallet's balance of some non-quote mint went up AND the wallet paid SOL (beyond the fee) or a quote token."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    sol_paid = 0.0
    if wallet in keys:
        i = keys.index(wallet)
        sol_paid = (meta["preBalances"][i] - meta["postBalances"][i] - (meta.get("fee") or 0)) / 1e9
    def bal(lst):
        out = {}
        for b in lst or []:
            if b.get("owner") == wallet:
                out[b["mint"]] = out.get(b["mint"], 0) + float(b["uiTokenAmount"]["uiAmount"] or 0)
        return out
    pre, post = bal(meta.get("preTokenBalances")), bal(meta.get("postTokenBalances"))
    deltas = {m: post.get(m, 0) - pre.get(m, 0) for m in set(pre) | set(post)}
    wsol_paid = -deltas.pop(SOL, 0.0)
    gained = {m: d for m, d in deltas.items() if d > 0}
    paid_quote = any(d < 0 for d in deltas.values())
    total_sol = sol_paid + max(wsol_paid, 0)
    if len(gained) != 1 or not (total_sol > 0.0001 or paid_quote):
        return None                               # transfer, airdrop, multi-token or sell: not a WALLET_BUY
    mint, amt = next(iter(gained.items()))
    return {"mint": mint, "token_amount": amt, "paid_sol": total_sol, "swap_time": tx.get("blockTime")}


class WalletBuyWatch:
    """Polls documented public wallets over the approved RPC. Never trades; emits WALLET_BUY events only."""

    def __init__(self, wallets, rpc=None):
        self.wallets, self.rpc = wallets, rpc or _rpc
        self.last_sig: Dict[str, Optional[str]] = {w["address"]: None for w in wallets}
        self.errors: Dict[str, int] = {}

    def poll(self, price_now=None) -> List[WalletBuy]:
        events = []
        for w in self.wallets:
            a = w["address"]
            try:
                sigs = self.rpc("getSignaturesForAddress", [a, {"limit": 10}]) or []
            except Exception as e:
                self.errors[type(e).__name__] = self.errors.get(type(e).__name__, 0) + 1
                continue
            if self.last_sig[a] is None:          # first poll: remember the head, do not replay history
                self.last_sig[a] = sigs[0]["signature"] if sigs else ""
                continue
            fresh = []
            for s in sigs:
                if s["signature"] == self.last_sig[a]:
                    break
                fresh.append(s)
            if sigs:
                self.last_sig[a] = sigs[0]["signature"]
            for s in reversed(fresh):
                if s.get("err") is not None:
                    continue
                try:
                    tx = self.rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                except Exception as e:
                    self.errors[type(e).__name__] = self.errors.get(type(e).__name__, 0) + 1
                    continue
                b = parse_buy(tx or {}, a)
                if not b:
                    continue
                det = time.time()
                px = price_now(b["mint"]) if price_now else None
                events.append(WalletBuy(a, w.get("label", ""), w["source_url"], s["signature"], b["mint"], b["token_amount"],
                                        b["paid_sol"], b["swap_time"] or det, det, det - (b["swap_time"] or det), px,
                                        b["paid_sol"] / b["token_amount"] if b["token_amount"] else None))
        return events


def _rpc(method, params):
    s, r = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if s != 200 or not isinstance(r, dict) or "result" not in r:
        raise RuntimeError(f"rpc {method} http={s}")
    return r["result"]


def to_json(ev):
    return json.dumps({"type": ev.status if isinstance(ev, Migration) else "WALLET_BUY", **asdict(ev)})
