import json
from datetime import datetime

BARS = "▁▂▃▄▅▆▇█"


def spark(vals, width=48):
    if len(vals) < 2:
        return ""
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[int(i * step)] for i in range(width)]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    return "".join(BARS[int((v - lo) / rng * 7)] for v in vals)


def render(bot, now: datetime, mode: str, tail=14) -> str:
    eq, ex, sc, rc, sz, lg = bot.eq, bot.ex, bot.scanner, bot.rug, bot.sizer, bot.log
    value = ex.value({})
    equity = eq.cash + value
    n = eq.wins + eq.losses
    wr = f"{eq.wins / n:.0%}" if n else "-"
    lines = [
        f"═══ DEX PAIR BOT [{mode}] ═══ {now:%Y-%m-%d %H:%M:%S} ═══",
        f"Balance ${eq.cash:,.2f} | Equity ${equity:,.2f} | Start ${eq.start:,.2f} | "
        f"PnL {equity - eq.start:+,.2f} ({(equity / eq.start - 1) * 100:+.1f}%)",
        f"Day peak ${eq.peak:,.2f} | drawdown {eq.drawdown(equity):.1%} (limit 30%)",
        f"Trades {n} | Wins {eq.wins} | Losses {eq.losses} | Win rate {wr} | loss streak {eq.loss_streak}",
        "── Current position(s) " + "─" * 30,
    ]
    if not ex.positions:
        lines.append("  none")
    for pos in ex.positions.values():
        mult = pos.last_price / pos.avg_entry if pos.avg_entry else 0
        lines.append(f"  {pos.symbol:<6} cost ${pos.cost:,.2f} avg {pos.avg_entry:.8g} now {pos.last_price:.8g} "
                     f"({mult:.2f}x) stage {pos.stage} parts {pos.parts_done}/{bot.cfg.entry_parts} "
                     f"age {(now - pos.opened).total_seconds() / 60:.0f}m")
    lines += [
        "── Agents " + "─" * 43,
        f"  SCANNER   {sc.status}",
        f"  RUGCHECK  {rc.status}",
        f"  SIZER     last {sz.last}",
        f"  EXECUTOR  {ex.status}",
        f"  LOGGER    {dict(lg.counts)} -> {lg.path}",
        f"  EQUITY    {eq.status}  curve {spark([v for _, v in eq.curve])}",
        "── Log (latest) " + "─" * 37,
    ]
    lines += ["  " + l for l in list(lg.tail)[-tail:]]
    return "\n".join(lines)


def write_state(bot, now, path):
    eq = bot.eq
    json.dump({
        "time": now.isoformat(), "balance": eq.cash, "equity": eq.cash + bot.ex.value({}),
        "wins": eq.wins, "losses": eq.losses, "paused": eq.paused,
        "open": [{"symbol": p.symbol, "cost": p.cost, "stage": p.stage} for p in bot.ex.positions.values()],
        "curve": [(t.isoformat(), v) for t, v in eq.curve],
    }, open(path, "w"), indent=1)
