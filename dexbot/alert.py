"""ALERT_ONLY: live read-only evaluation. No orders, no paper entries, no PnL.
Discovery (profiles/boosts) only supplies candidate mints; the alert reason comes from market data.
Order: universe -> >=2 signals -> no-chase -> RugCheck. SELL_SIMULATION is unavailable, so even a
pair that passes everything else is reported as ALERT_ONLY and is never a valid entry."""
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

from .agents import RugCheck, Scanner
from .config import Config
from .live import LiveFeed


def evaluate(cfg, scanner, rug, p):
    sig = scanner.signals(p)
    uf = scanner.universe_fail(p)
    if uf:
        return "REJECT", uf, sig
    if len(sig) < cfg.min_signals:
        return "REJECT", f"signals {len(sig)} < {cfg.min_signals}", sig
    if p.price_1h_ago and p.price / p.price_1h_ago >= cfg.no_chase_mult and \
            not (p.pullback_pct is not None and p.pullback_pct >= cfg.pullback_min):
        return "REJECT", "no-chase: +300% in 1h without pullback", sig
    ok, why = rug.check(p)
    if not ok:
        return "REJECT", "RUGCHECK " + why, sig
    return "ALERT_ONLY", "passes all checks", sig


def run_alert_only(cfg: Config, minutes: int, interval_s: int = 60):
    os.makedirs("logs", exist_ok=True)
    path = f"logs/alert_only_{datetime.now():%Y%m%d_%H%M}.log"
    out = open(path, "a", buffering=1)
    feed, scanner, rug = LiveFeed(cfg), Scanner(cfg), RugCheck(cfg)
    seen, reasons, alerts, mints = set(), Counter(), [], set()
    end = time.time() + minutes * 60

    def emit(line):
        print(line, flush=True)
        out.write(line + "\n")

    emit(f"# ALERT_ONLY start {datetime.now(timezone.utc).isoformat()} minutes={minutes} (no orders, no entries)")
    while time.time() < end:
        feed.step(datetime.now())
        for p in feed.snapshot():
            if p.age_s > cfg.max_age_s:
                continue
            mints.add(p.addr)
            verdict, why, sig = evaluate(cfg, scanner, rug, p)
            key = (p.addr, verdict, why.split(" (")[0])
            if key in seen:
                continue
            seen.add(key)
            reasons[(verdict, re.sub(r'[\$\d][\d,\.]*', 'N', why)[:70])] += 1
            if verdict == "ALERT_ONLY":
                why = "passes universe/signals/no-chase/RugCheck; SELL_SIMULATION=UNKNOWN -> NOT a valid entry"
                alerts.append(p.addr)
            liq = "unknown" if p.liquidity is None else f"${p.liquidity:,.0f}"
            emit(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {verdict:<10} mint={p.addr} "
                 f"pair={p.pair_addr} {p.source} age={p.age_s / 60:.0f}m vol5m=${p.vol5m:,.0f} liq={liq} "
                 f"buys/sells={p.buys5m}/{p.sells5m} signals={'+'.join(sig) or '-'} | {why}")
        time.sleep(max(0, min(interval_s, end - time.time())))
    emit(f"# END distinct pairs evaluated={len(mints)} alerts={len(alerts)}")
    for (v, r), n in reasons.most_common():
        emit(f"#  {n:>3} x {v} {r}")
    out.close()
    print(f"\nLog: {path}")
