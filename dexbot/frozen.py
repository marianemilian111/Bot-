"""Strategy freeze. Any change to a rule or filter changes the fingerprint and the collector refuses to run."""
import hashlib
import json
from dataclasses import asdict

STRATEGY_ID = "momentum-v1"
EXCLUDED = {"start_balance", "tick_s", "recap_at"}      # account / loop settings, not strategy rules
FROZEN_FINGERPRINT = "e1d925460ea02be4"   # momentum-v1; all variants are registered in variants.py


def fingerprint(cfg, strategy_id: str = STRATEGY_ID) -> str:
    d = {k: v for k, v in asdict(cfg).items() if k not in EXCLUDED}
    d["size_fracs"] = {str(k): v for k, v in cfg.size_fracs.items()}
    d["strategy"] = strategy_id
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def assert_frozen(cfg, strategy_id: str = STRATEGY_ID):
    from .variants import FROZEN
    fp = fingerprint(cfg, strategy_id)
    if fp != FROZEN[strategy_id]:
        raise SystemExit(f"{strategy_id}: config fingerprint {fp} != frozen {FROZEN[strategy_id]}. "
                         "Changing rules after seeing results requires a new validation period.")
    return fp
