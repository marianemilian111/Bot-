"""Paper-only strategy variants. momentum-v1 is never modified; v2 is a separate, clearly experimental config.
Exit rules, costs, and the safety checks (mint/freeze, LP, SELL_SIMULATION; UNKNOWN = reject) are identical in both."""
from dataclasses import replace

from .config import Config

V1 = "momentum-v1"
V2 = "momentum-v2-explore"
LABELS = {V1: "Momentum v1 (frozen, validation candidate)", V2: "Momentum v2 explore (experimental thresholds)"}


def make(variant: str, start_balance: float = 1000.0) -> Config:
    base = Config(start_balance=start_balance)
    if variant == V1:
        return base
    if variant == V2:
        return replace(base, min_liq=20_000, min_vol5m=10_000, min_signals=1)
    raise SystemExit(f"unknown variant {variant}")


# fingerprints are computed from the configs above and pinned here; editing a rule changes the fingerprint and aborts runs
FROZEN = {V1: "e1d925460ea02be4", V2: "25db7812f99c7f4d"}


# ---- separate hypothesis (paper only): migrated, already-liquid pools in a wider age window, real position size
V3 = "momentum-v3-migrated"
V3B = "random-baseline-v3"
LABELS[V3] = "Momentum v3 migrated pools 6-72h ($50 positions)"
LABELS[V3B] = "Random baseline v3 (same candidates, size, exits)"
V3_RULES = {
    "min_age_s": 6 * 3600, "max_age_s": 72 * 3600, "position_usd": 50.0, "start_balance": 250.0, "entry_parts": 2,
    "discovery": "geckoterminal: pools sorted by h24 volume p1-3 + trending 24h; no boosts/profiles; dedupe by pool address",
    "fills": "jupiter quote at size + net fee", "net_fee_usd": 0.02,
    "exit_fill": "only a passing sell simulation at size; FAIL -> $0 write-off; UNKNOWN -> retry next cycle",
    "baseline": "paired: one random draw (seeded) among same-cycle pairs passing universe+safety, per Momentum entry",
    "baseline_seed": 20261118, "early_stop_min": 20, "excluded_base_symbols": "SOL, stables, wrapped majors",
}


def make_v3(start_balance: float = 250.0) -> Config:
    """v1 thresholds and exits unchanged; only the age window is wider (min age is enforced by the v3 scanner)."""
    return replace(Config(start_balance=start_balance), max_age_s=V3_RULES["max_age_s"])


def fingerprint_v3(cfg) -> str:
    import hashlib
    import json
    from .frozen import fingerprint
    return hashlib.sha256((fingerprint(cfg, V3) + json.dumps(V3_RULES, sort_keys=True)).encode()).hexdigest()[:16]


def assert_v3(cfg) -> str:
    fp = fingerprint_v3(cfg)
    if fp != FROZEN[V3]:
        raise SystemExit(f"{V3}: rules fingerprint {fp} != frozen {FROZEN[V3]}; a changed rule needs a new test plan")
    return fp


_make_v1v2 = make


def make(variant: str, start_balance: float = 1000.0) -> Config:     # noqa: F811
    if variant in (V3, V3B):
        return make_v3(start_balance)
    return _make_v1v2(variant, start_balance)


FROZEN[V3] = "afaf71d4a6d592db"


# ---- v5 (paper only, separate): v1 universe and safety, ONE change - at least 1 Momentum signal instead of 2 -
#      executed exactly like v3 ($50 in 2 x $25 sized quotes, sell simulation of the exact amount, same exits and costs).
#      Chosen from diagnostic data (runs 44303696774 + 44312261755): the only pair that passed liquidity, volume and
#      holders in those runs was stopped solely by "signals >= 2". Validation uses new data only (docs/PREREG_V5.md).
V5 = "momentum-v5-1signal"
V5B = "random-baseline-v5"
LABELS[V5] = "Momentum v5: v1 universe, >= 1 signal ($50 positions)"
LABELS[V5B] = "Random baseline v5 (same candidates, size, exits)"
V5_RULES = {
    "min_age_s": 0, "max_age_s": 6 * 3600, "position_usd": 50.0, "start_balance": 250.0, "entry_parts": 2,
    "min_signals": 1, "changed_vs_v1": "min_signals 2 -> 1 (only change); execution as v3",
    "discovery": "LiveFeed: DexScreener profiles + boosts latest/top, GeckoTerminal new/trending (discovery only, never a signal)",
    "fills": "jupiter quote at size + net fee", "net_fee_usd": 0.02,
    "exit_fill": "only a passing sell simulation at size; FAIL -> $0 imputed; UNKNOWN -> retry next cycle",
    "baseline": "paired: one random draw among same-cycle pairs passing universe+safety, per Momentum entry",
    "baseline_seed": 20261119, "stale_data_s": 120,
}


def make_v5(start_balance: float = 250.0) -> Config:
    return replace(Config(start_balance=start_balance), min_signals=1)


def fingerprint_v5(cfg) -> str:
    import hashlib
    import json
    from .frozen import fingerprint
    return hashlib.sha256((fingerprint(cfg, V5) + json.dumps(V5_RULES, sort_keys=True)).encode()).hexdigest()[:16]


def assert_v5(cfg) -> str:
    fp = fingerprint_v5(cfg)
    if fp != FROZEN[V5]:
        raise SystemExit(f"{V5}: rules fingerprint {fp} != frozen {FROZEN[V5]}; a changed rule needs a new pre-registration")
    return fp


_make_prev = make


def make(variant: str, start_balance: float = 1000.0) -> Config:     # noqa: F811
    if variant in (V5, V5B):
        return make_v5(start_balance)
    return _make_prev(variant, start_balance)


FROZEN[V5] = "d9d4191e906de84d"


# ---- v6 (paper only, separate): the screenshot strategy, independent run (docs/PREREG_V6_INDEPENDENT.md)
V6 = "capture-v6"
V6B = "random-baseline-v6"
LABELS[V6] = "Capture v6 (screenshot rules, equity sizing from $70)"
LABELS[V6B] = "Random baseline v6 (same candidates, sizing, exits)"
V6_RULES = {
    "universe": "age <= 6 h; DEX pumpfun/pumpswap/raydium*/meteora*; liq >= 40k; vol5m >= 25k; holders >= 300; mint+freeze closed; "
                "LP of the TRADED pool >= 95 % locked/burned (duration not verifiable)",
    "signals": "VOL_SPIKE (avg over existing 5-min windows when < 1 h), BUY_RATIO > 70 % (>= 20 txns), HOLDER_GROWTH >= 10 %/10 min, "
               "SMART_MONEY >= 3 tracked wallets in 10 min, KOL bought in 10 min (no wallet list -> UNKNOWN, never counted); >= 2",
    "rug": "dev < 5 % (creator address), top10 < 35 % raw incl. AMM, bundle proxy, sell simulation of the exact amount PASS",
    "no_chase": "+300 % in 1 h without >= 10 % pullback from the 1 h high (GeckoTerminal 5 m candles of the traded pool; unknown -> skip)",
    "sizer": "equity-based 20/30/40 % for 2/3/>=4 signals, x0.5 after 2 losses in a row, compounding, max 2 open, $70 paper",
    "execution": "2 tranches each with its own Jupiter quote, 2.5 % cap, +$0.02/swap; exits only via passing sell simulation of the "
                 "exact amount; FAIL -> imputed $0; UNKNOWN -> retry",
    "exits": "40 % at 2x, 30 % at 4x, 25 % trailing rest, hard stop -12 %, time stop 25 min, DEV_SELL (creator balance fell), "
             "LP_PULLED (traded pool liquidity < 50 % of entry: proxy)",
    "session": "entries 23:00-07:00 Europe/Bucharest; 07:00 recap; 30 % drawdown from session peak -> pause entries",
    "baseline": "paired random draw among same-cycle pairs passing universe + safety, same sizing/exits", "seed": 20261120,
}


def make_v6(start_balance: float = 70.0) -> Config:
    return Config(start_balance=start_balance)


def fingerprint_v6(cfg) -> str:
    import hashlib
    import json
    from .frozen import fingerprint
    return hashlib.sha256((fingerprint(cfg, V6) + json.dumps(V6_RULES, sort_keys=True)).encode()).hexdigest()[:16]


def assert_v6(cfg) -> str:
    fp = fingerprint_v6(cfg)
    if fp != FROZEN[V6]:
        raise SystemExit(f"{V6}: rules fingerprint {fp} != frozen {FROZEN[V6]}")
    return fp


FROZEN[V6] = "f9f64f071c181687"
