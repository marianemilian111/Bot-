#!/usr/bin/env python3
"""Reachability check of the read-only sources. Exit 2 (BLOCAT) if a mandatory one is unreachable from this machine."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.sellsim import RPC, SOL, http_json  # noqa: E402

def jupiter_probe():
    """Quote SOL -> some currently listed Solana token (found via DexScreener) until one quote returns 200."""
    st, prof = http_json("https://api.dexscreener.com/token-profiles/latest/v1")
    mints = [x["tokenAddress"] for x in prof if x.get("chainId") == "solana"][:6] if isinstance(prof, list) else []
    last = (None, "no candidate tokens from DexScreener")
    for m in mints:
        last = http_json(f"https://lite-api.jup.ag/swap/v1/quote?inputMint={SOL}&outputMint={m}&amount=1000000")
        if last[0] == 200:
            return last
    return last


CHECKS = [
    ("DexScreener", True, lambda: http_json("https://api.dexscreener.com/token-profiles/latest/v1")),
    ("RugCheck", True, lambda: http_json("https://api.rugcheck.xyz/v1/stats/new_tokens")),
    ("Jupiter quote", True, lambda: jupiter_probe()),
    ("Solana RPC", True, lambda: http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": "getHealth"})),
    ("GeckoTerminal (history, optional)", False, lambda: http_json("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1")),
]
rows, blocked = [], []
for name, mandatory, fn in CHECKS:
    status, body = fn()
    ok = status == 200
    rows.append({"source": name, "mandatory": mandatory, "http": status, "ok": ok,
                 "detail": "" if ok else str(body)[:160]})
    if mandatory and not ok:
        blocked.append(name)
os.makedirs("logs", exist_ok=True)
json.dump({"rows": rows, "blocked": blocked}, open("logs/preflight.json", "w"), indent=1)
for r in rows:
    print(f"{'OK ' if r['ok'] else 'FAIL'} {r['source']:<34} http={r['http']} {r['detail']}")
if blocked:
    print("BLOCAT:", ", ".join(blocked))
    sys.exit(2)
