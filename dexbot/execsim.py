"""Paper execution at the real position size (read-only: quotes and unsigned simulations, no signing, no orders).

BUY  : Jupiter quote SOL -> token for the USD size. The fill is the quote's own output (it already contains pool fees
       and price impact); a fixed network/priority fee per swap is added on top.
SELL : Jupiter quote token -> SOL for the exact token amount held, swap transaction built for a public holder that owns at
       least that amount, run through Solana RPC simulateTransaction. err == null -> OK and the fill is the quote output.
       Every result carries the stage (holder lookup / SELL_QUOTE / swap build / simulateTransaction) and the exact error.
A stop is never assumed to execute: the exit is filled only by a passing simulation at that moment.
Limits: the simulation uses a holder's balance, not ours, and a quote is not a guaranteed fill."""
import time

from .sellsim import JUP, RPC, SOL, http_json

NET_FEE_USD = 0.02          # assumed network + priority fee per swap (paper assumption, stated in every report)
_dec, _sol = {}, {"t": 0.0, "usd": None}


def token_decimals(mint):
    if mint not in _dec:
        s, r = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]})
        _dec[mint] = ((r.get("result") or {}).get("value") or {}).get("decimals") if s == 200 and isinstance(r, dict) else None
    return _dec[mint]


def sol_usd():
    """SOL price from DexScreener (deepest SOL pair). Cached 60 s. None if unavailable."""
    if time.time() - _sol["t"] < 60 and _sol["usd"]:
        return _sol["usd"]
    s, d = http_json(f"https://api.dexscreener.com/latest/dex/tokens/{SOL}")
    best = None
    if s == 200 and isinstance(d, dict):
        for p in d.get("pairs") or []:
            if p.get("chainId") == "solana" and p["baseToken"]["address"] == SOL and p.get("priceUsd"):
                liq = (p.get("liquidity") or {}).get("usd") or 0
                if best is None or liq > best[0]:
                    best = (liq, float(p["priceUsd"]))
    _sol.update(t=time.time(), usd=best[1] if best else None)
    return _sol["usd"]


def buy_quote(mint, usd):
    """-> dict(status OK|FAIL|UNKNOWN, stage, detail, tokens, fill_px, quoted_impact)"""
    su, dec = sol_usd(), token_decimals(mint)
    if not su or dec is None:
        return {"status": "UNKNOWN", "stage": "price/decimals", "detail": f"sol_usd={su} decimals={dec}"}
    lamports = int((usd - NET_FEE_USD) / su * 1e9)
    s, q = http_json(f"{JUP}/quote?inputMint={SOL}&outputMint={mint}&amount={lamports}&slippageBps=250")
    if s != 200 or not isinstance(q, dict) or "outAmount" not in q:
        st = "FAIL" if s == 400 and "ROUTE" in str(q).upper() else "UNKNOWN"
        return {"status": st, "stage": "BUY_QUOTE", "detail": f"http={s} {str(q)[:120]}"}
    tokens = int(q["outAmount"]) / 10 ** dec
    return {"status": "OK", "stage": "BUY_QUOTE", "detail": "quote ok", "tokens": tokens, "fill_px": usd / tokens if tokens else None,
            "quoted_impact": float(q.get("priceImpactPct") or 0), "t": time.time()}


def sell_simulation(mint, holders, tokens):
    """Simulate selling exactly `tokens` of `mint`. -> dict(status, stage, detail, proceeds_usd, quoted_impact)"""
    su, dec = sol_usd(), token_decimals(mint)
    if not su or dec is None:
        return {"status": "UNKNOWN", "stage": "price/decimals", "detail": f"sol_usd={su} decimals={dec}"}
    raw = int(tokens * 10 ** dec)
    holder = next((h for h in (holders or []) if h[1] >= raw * 1.01), None)
    if holder is None:
        return {"status": "UNKNOWN", "stage": "holder lookup", "detail": f"no public holder with >= {tokens:,.0f} tokens"}
    s, q = http_json(f"{JUP}/quote?inputMint={mint}&outputMint={SOL}&amount={raw}&slippageBps=250")
    if s != 200 or not isinstance(q, dict) or "outAmount" not in q:
        st = "FAIL" if s == 400 and "ROUTE" in str(q).upper() else "UNKNOWN"
        return {"status": st, "stage": "SELL_QUOTE", "detail": f"http={s} {str(q)[:120]}"}
    s, sw = http_json(f"{JUP}/swap", {"quoteResponse": q, "userPublicKey": holder[0], "wrapAndUnwrapSol": True})
    if s != 200 or not isinstance(sw, dict) or not sw.get("swapTransaction"):
        return {"status": "UNKNOWN", "stage": "swap build", "detail": f"http={s} {str(sw)[:120]}"}
    s, sim = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": "simulateTransaction",
                             "params": [sw["swapTransaction"], {"encoding": "base64", "sigVerify": False,
                                                                "replaceRecentBlockhash": True, "commitment": "processed"}]})
    if s != 200 or not isinstance(sim, dict) or sim.get("error") or "result" not in sim:
        return {"status": "UNKNOWN", "stage": "simulateTransaction", "detail": f"http={s} {str(sim)[:120]}"}
    v = sim["result"].get("value") or {}
    if v.get("err") is not None:
        logs = [l for l in (v.get("logs") or []) if "rror" in l][-1:]
        return {"status": "FAIL", "stage": "simulateTransaction", "detail": f"err={str(v['err'])[:100]} {' '.join(logs)[:120]}"}
    return {"status": "OK", "stage": "simulateTransaction", "detail": "err=null",
            "proceeds_usd": int(q["outAmount"]) / 1e9 * su - NET_FEE_USD, "quoted_impact": float(q.get("priceImpactPct") or 0)}
