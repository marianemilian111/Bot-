"""Strategy freeze. Any change to a rule or filter changes the fingerprint and the collector refuses to run."""
import hashlib
import json
from dataclasses import asdict

STRATEGY_ID = "momentum-v1"
EXCLUDED = {"start_balance", "tick_s", "recap_at"}      # account / loop settings, not strategy rules
FROZEN_FINGERPRINT = "e1d925460ea02be4"


def fingerprint(cfg) -> str:
    d = {k: v for k, v in asdict(cfg).items() if k not in EXCLUDED}
    d["size_fracs"] = {str(k): v for k, v in cfg.size_fracs.items()}
    d["strategy"] = STRATEGY_ID
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def assert_frozen(cfg):
    fp = fingerprint(cfg)
    if fp != FROZEN_FINGERPRINT:
        raise SystemExit(f"Strategy config changed: fingerprint {fp} != frozen {FROZEN_FINGERPRINT}. "
                         "Changing rules after seeing results requires a new validation period.")
    return fp
