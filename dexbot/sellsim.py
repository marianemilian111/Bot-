"""SELL_SIMULATION (read-only, no signing, no orders).

SELL_QUOTE      = Jupiter price quote only. Evidence of a route, NOT evidence the sale works. Never passes a pair.
SELL_SIMULATION = an unsigned Jupiter swap transaction (token -> SOL) built for a PUBLIC holder's address and run through
                  the Solana RPC `simulateTransaction` (sigVerify=false, replaceRecentBlockhash=true).
                  err == null -> OK. A program error -> FAIL. Any transport/API problem -> UNKNOWN (pair is rejected).
Limits: it shows the sale is not blocked (freeze, transfer hook, honeypot logic). It is the holder's balance, not ours,
so it says nothing about the price we would get. Its power to catch honeypots has not been validated with a known honeypot."""
import json
import time
import urllib.error
import urllib.request

JUP = "https://lite-api.jup.ag/swap/v1"
RPC = "https://api.mainnet-beta.solana.com"
SOL = "So11111111111111111111111111111111111111112"
_last = {"t": 0.0}


def http_json(url, data=None, timeout=20, min_gap=2.1):
    """Returns (status, parsed-or-text). status None on transport failure. Paced for Jupiter's keyless 0.5 RPS."""
    if "jup.ag" in url:
        wait = min_gap - (time.time() - _last["t"])
        if wait > 0:
            time.sleep(wait)
        _last["t"] = time.time()
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "dexbot/0.1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode(errors="replace")
    except Exception as e:
        return None, repr(e)[:300]


def simulate_sell(mint, holder):
    """holder = (owner_pubkey, raw_amount). Returns (status, detail) with status OK | FAIL | UNKNOWN."""
    if not holder:
        return "UNKNOWN", "no public holder available"
    owner, amount = holder
    amt = int(min(10 ** 6, amount // 100))
    if amt <= 0:
        return "UNKNOWN", "holder balance too small"
    s, quote = http_json(f"{JUP}/quote?inputMint={mint}&outputMint={SOL}&amount={amt}&slippageBps=250")
    if s != 200 or not isinstance(quote, dict):
        if s == 400 and isinstance(quote, str) and "ROUTE" in quote.upper():
            return "FAIL", "SELL_QUOTE: no route"
        return "UNKNOWN", f"SELL_QUOTE http={s} {str(quote)[:80]}"
    s, sw = http_json(f"{JUP}/swap", {"quoteResponse": quote, "userPublicKey": owner, "wrapAndUnwrapSol": True})
    if s != 200 or not isinstance(sw, dict) or not sw.get("swapTransaction"):
        return "UNKNOWN", f"swap build http={s} {str(sw)[:80]}"
    s, sim = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": "simulateTransaction",
                             "params": [sw["swapTransaction"], {"encoding": "base64", "sigVerify": False,
                                                                "replaceRecentBlockhash": True,
                                                                "commitment": "processed"}]})
    if s != 200 or not isinstance(sim, dict) or sim.get("error") or "result" not in sim:
        return "UNKNOWN", f"simulateTransaction http={s} {str(sim)[:100]}"
    err = (sim["result"].get("value") or {}).get("err")
    if err is None:
        return "OK", "simulateTransaction err=null"
    return "FAIL", f"simulateTransaction err={json.dumps(err)[:100]}"


class SellChecker:
    """Caches entry-time results for 5 minutes; exit-time checks are always fresh."""

    def __init__(self, ttl=300):
        self.ttl, self.cache, self.stats = ttl, {}, {"OK": 0, "FAIL": 0, "UNKNOWN": 0}
        self.last_detail = {}

    def check(self, p):
        hit = self.cache.get(p.addr)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        return self.fresh(p, store=True)

    def fresh(self, p, store=False):
        st, detail = simulate_sell(p.addr, p.sell_holder)
        self.stats[st] += 1
        self.last_detail[p.addr] = detail
        if store:
            self.cache[p.addr] = (time.time(), st)
        return st
