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
