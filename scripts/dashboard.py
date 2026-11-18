#!/usr/bin/env python3
"""PAPER ONLY dashboard (Markdown for GitHub Actions Summary). Fed from state/paper_state.json and logs/night_*.log only.
--demo renders synthetic numbers, stamped DEMO everywhere; it never reads real files and is never used by the smoke test."""
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexbot.config import Config  # noqa: E402
from dexbot.dashboard import spark  # noqa: E402
from dexbot.frozen import STRATEGY_ID, fingerprint  # noqa: E402

DEMO = "--demo" in sys.argv
TAG = " (DEMO)" if DEMO else ""


def clean(s, n=14, extra=""):
    """Untrusted text (token symbols): keep it inert inside Markdown tables."""
    return re.sub(r"[^A-Za-z0-9_.$%" + extra + "-]", "", str(s))[:n] or "?"


def newest(pat):
    f = sorted(glob.glob(pat), key=os.path.getmtime)
    return f[-1] if f else None


def demo_data():
    t0 = datetime(2000, 1, 1, 2, 0)
    eq, curve = 1000.0, [(t0.isoformat(), 1000.0)]
    trades = []
    for i, pnl in enumerate([-40, 120, -35, 260, -50, -45, 90]):
        eq += pnl
        o = t0 + timedelta(minutes=40 * i + 5)
        trades.append({"symbol": f"DEMO{i}", "opened": o.isoformat(), "closed": (o + timedelta(minutes=18)).isoformat(),
                       "cost": 300.0, "proceeds": 300.0 + pnl, "reasons": ["TP1_2x", "TRAIL_25%"] if pnl > 0 else ["HARD_STOP"]})
        curve.append(((o + timedelta(minutes=18)).isoformat(), eq))
    pos = {"x": {"symbol": "DEMOOPEN", "opened": (t0 + timedelta(hours=5)).isoformat(), "cost": 250.0, "tokens": 1000.0,
                 "tokens_bought": 1000.0, "last_price": 0.3, "stage": 0, "parts_done": 2, "reasons": []}}
    return {"start": 1000.0, "cash": eq - 250.0, "wins": 4, "losses": 3, "loss_streak": 1, "peak": 1330.0, "paused": False,
            "curve": curve, "positions": pos, "trades": trades, "failed_exits": 1}, [
        "2000-01-01 03:00:00 EXIT_FAILED DEMO3 HARD_STOP: sell simulation failed, 100% written off (proceeds $0)",
        "2000-01-01 07:00:00 ENTER    DEMOOPEN part 1/2 size=$125.00 (DEMO)"]


def load():
    if DEMO:
        return demo_data()
    sp = "state/paper_state_momentum-v1.json" if os.path.exists("state/paper_state_momentum-v1.json") else "state/paper_state.json"
    st = json.load(open(sp)) if os.path.exists(sp) else None
    lg = newest("logs/night_momentum-v1_*.log") or newest("logs/night_*.log")
    lines = open(lg).read().splitlines() if lg else []
    return st, lines


def maxdd(vals):
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak else 0)
    return dd


st, lines = load()
cfg = Config()
src = "DEMO - SYNTHETIC NUMBERS, NOT REAL" if DEMO else "live public data (DexScreener, RugCheck, Jupiter, Solana RPC), paper fills"
o = [f"# Paper dashboard{TAG}", ""]
o.append(f"> **PAPER ONLY** - virtual balance, no wallet, no real orders. Source: {src}.")
if DEMO:
    o.append("> **DEMO**: every number on this page is invented to preview the layout. It says nothing about any strategy.")
o += ["", f"## Strategies{TAG}", "", "| Strategy | Status | Note |", "|---|---|---|",
      f"| Momentum (`{STRATEGY_ID}`) | **ACTIVE** | rules and filters frozen, fingerprint `{fingerprint(cfg)}` |",
      "| Sniper | disabled | no verifiable rules or data yet |",
      "| Dip Hunter | disabled | no verifiable rules or data yet |",
      "| Whale Tail | disabled | no wallet list / data source |", ""]

if st is None:
    o += ["## Balance", "", "No `state/paper_state.json` found: the collector has not run in this job. "
          "**0 tranzactii inchise - PnL estimat: N/A**"]
    print("\n".join(o))
    sys.exit(0)

trades, positions = st["trades"], st["positions"]
closed = len(trades)
curve = [float(v) for _, v in st["curve"]]
open_val = sum(p["tokens"] * p["last_price"] for p in positions.values())
equity = st["cash"] + open_val
start = st["start"]
dd = maxdd(curve) if len(curve) > 1 else 0.0
o += [f"## Balance{TAG}", "", "| | |", "|---|---|",
      f"| Virtual start balance | ${start:,.2f} |",
      f"| Cash | ${st['cash']:,.2f} |",
      f"| Open positions (marked at last price) | ${open_val:,.2f} |",
      f"| Equity (estimate) | ${equity:,.2f} |",
      f"| Max drawdown (equity marks) | {dd:.1%} |"]
if closed == 0:
    o.append("| Net PnL (estimated) | **N/A** |")
    o += ["", f"**0 tranzactii inchise - PnL estimat: N/A**" + (" (DEMO)" if DEMO else "")]
else:
    pnl = sum(t["proceeds"] - t["cost"] for t in trades)
    best = max(t["proceeds"] - t["cost"] for t in trades)
    o.append(f"| Net PnL of closed trades (estimated, after modelled fee {cfg.fee:.1%}/leg + depth slippage, failed exits included) | **{pnl:+,.2f}** ({pnl / start * 100:+.1f}% of start) |")
    o.append(f"| Net PnL without the best trade | {pnl - best:+,.2f} |")
    o += ["", f"Closed trades: **{closed}** (wins {st['wins']} / losses {st['losses']}). "
          "Estimate from paper fills; not realised profit." + (" **DEMO.**" if DEMO else "")]

o += ["", f"## Equity curve{TAG}", ""]
if len(curve) >= 2:
    o += ["```", f"{spark(curve, 60)}", f"start ${curve[0]:,.2f}  last ${curve[-1]:,.2f}  min ${min(curve):,.2f}  "
          f"max ${max(curve):,.2f}  max drawdown {dd:.1%}  ({len(curve)} marks)", "```"]
else:
    o.append("Fewer than 2 equity marks yet (marks are added on every close and hourly).")

o += ["", f"## Open positions{TAG}", ""]
if positions:
    o += ["| Symbol | Cost | Avg entry | Last | x | Stage | Parts | Opened |", "|---|---|---|---|---|---|---|---|"]
    for p in positions.values():
        avg = p["cost"] / p["tokens_bought"] if p["tokens_bought"] else 0
        x = p["last_price"] / avg if avg else 0
        o.append(f"| {clean(p['symbol'])} | ${p['cost']:,.2f} | {avg:.8g} | {p['last_price']:.8g} | {x:.2f}x | "
                 f"{p['stage']} | {p['parts_done']}/{cfg.entry_parts} | {p['opened'][:16]} |")
else:
    o.append("None.")

fails = [l for l in lines if " EXIT_FAILED " in l]
deferred = [l for l in lines if " EXIT_DEFERRED " in l]
o += ["", f"## Failed exits{TAG}", "",
      f"EXIT_FAILED (sale blocked in simulation, position written off): **{st.get('failed_exits', 0)}**  |  "
      f"EXIT_DEFERRED (sale unverifiable, retried): **{len(deferred)}**"]
if fails:
    o += ["", "```"] + [l[:200] for l in fails[-5:]] + ["```"]

o += ["", f"## Trade history{TAG}", ""]
if trades:
    o += ["| Symbol | Opened | Closed | Cost | Proceeds | PnL | % | Exit reasons |", "|---|---|---|---|---|---|---|---|"]
    for t in trades[-20:]:
        pnl = t["proceeds"] - t["cost"]
        o.append(f"| {clean(t['symbol'])} | {t['opened'][:16]} | {t['closed'][:16]} | ${t['cost']:,.2f} | ${t['proceeds']:,.2f} | "
                 f"{pnl:+,.2f} | {pnl / t['cost'] * 100:+.1f}% | {clean(', '.join(dict.fromkeys(t['reasons'])), 40, ', ')} |")
else:
    o.append("No closed trades.")

ev = [l for l in lines if re.search(r" (ENTER|EXIT|EXIT_FAILED|EXIT_DEFERRED|CLOSED|PAUSE|RESUME) ", l)]
o += ["", f"## Recent events{TAG}", "", "```"] + ([l[:200] for l in ev[-12:]] or ["(none)"]) + ["```"]
flt = sum(" FILTERED " in l for l in lines)
o += ["", f"Pairs rejected by a filter (FILTERED lines): {flt}. Costs and fills are modelled; SELL_SIMULATION is not validated "
      "against a known honeypot. **Not evidence of profit.**"]
print("\n".join(o))
