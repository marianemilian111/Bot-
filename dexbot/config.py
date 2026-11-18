from dataclasses import dataclass


@dataclass
class Config:
    # account
    start_balance: float = 1000.0
    # universe
    max_age_s: int = 6 * 3600
    min_liq: float = 40_000
    min_vol5m: float = 25_000
    min_holders: int = 300
    # scanner signals
    spike_mult: float = 5.0          # vol5m > 5x the 1h average 5m volume
    buy_ratio_min: float = 0.70
    holder_growth_min: float = 0.10  # +10% holders vs 10 min ago
    smart_min: int = 3               # of the 40 tracked wallets, within 10 min
    min_signals: int = 2
    # rug check
    dev_max_pct: float = 5.0
    top10_max_pct: float = 35.0
    # sizer
    size_fracs = {2: 0.20, 3: 0.30, 4: 0.40}  # >=4 signals -> 40%
    max_open: int = 2
    loss_streak_trigger: int = 2
    loss_streak_scale: float = 0.5
    # executor
    max_slippage: float = 0.025
    entry_parts: int = 2
    fee: float = 0.003               # per leg (swap + priority fee estimate)
    tp1_mult: float = 2.0
    tp1_frac: float = 0.40
    tp2_mult: float = 4.0
    tp2_frac: float = 0.30
    trail_pct: float = 0.25
    hard_stop: float = 0.12
    time_stop_s: int = 25 * 60
    # rules
    no_chase_mult: float = 4.0       # +300% in 1h
    pullback_min: float = 0.10       # pullback from 1h high that lifts the no-chase rule
    dd_limit: float = 0.30
    # loop
    tick_s: int = 10
    recap_at: str = "07:00"
