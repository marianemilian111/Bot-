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
