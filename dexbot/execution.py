"""Execution engine for a FUTURE pilot - built and tested without money.

Nothing here signs or sends a transaction. `LiveBroker.submit` raises `ExecutionDisabled`; the post-submit path
(confirmation, reconciliation, restart recovery, duplicate protection) is exercised only against `FakeChain` in tests.
A quote or a simulation is never recorded as an executed trade: only a broker confirmation creates a fill.

Flow per order: risk check -> quote (fresh, <= QUOTE_TTL_S) -> impact check -> simulation -> journal SUBMITTING ->
broker.submit -> journal SUBMITTED(signature) -> confirm -> journal CONFIRMED / FAILED -> reconcile balances.
The journal is written BEFORE every external call, so a restart can resume without sending anything twice."""
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

QUOTE_TTL_S = 15.0
RPC_RETRIES = 3


class ExecutionDisabled(RuntimeError):
    """Raised by the only real broker: signing/sending is not implemented and needs a separate pilot approval."""


class RpcError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- risk limits
@dataclass
class Limits:
    max_total_exposure_usd: float = 100.0      # cost basis of all open positions + pending buys
    max_position_usd: float = 50.0
    max_daily_loss_usd: float = 25.0           # realised + imputed (failed exits) per UTC day
    max_slippage: float = 0.025                # quoted price impact / move vs detection price
    max_data_age_s: float = 120.0


class RiskGuard:
    """Blocks NEW entries. It never sells or liquidates anything: open positions keep being managed by their exit rules."""

    def __init__(self, limits: Limits):
        self.l = limits
        self.state_error: Optional[str] = None
        self.loss_by_day: Dict[str, float] = {}

    def record_loss(self, day: str, usd: float):
        if usd > 0:
            self.loss_by_day[day] = self.loss_by_day.get(day, 0.0) + usd

    def check_entry(self, usd, exposure_usd, day, data_age_s, impact=None):
        if self.state_error:
            return False, f"STATE_ERROR: {self.state_error}"
        if data_age_s is None or data_age_s > self.l.max_data_age_s:
            return False, f"STALE_DATA: {data_age_s}s > {self.l.max_data_age_s}s"
        if usd > self.l.max_position_usd:
            return False, f"POSITION_LIMIT: ${usd:.2f} > ${self.l.max_position_usd:.2f}"
        if exposure_usd + usd > self.l.max_total_exposure_usd:
            return False, f"EXPOSURE_LIMIT: ${exposure_usd + usd:.2f} > ${self.l.max_total_exposure_usd:.2f}"
        if self.loss_by_day.get(day, 0.0) >= self.l.max_daily_loss_usd:
            return False, f"DAILY_LOSS_LIMIT: ${self.loss_by_day[day]:.2f} >= ${self.l.max_daily_loss_usd:.2f}"
        if impact is not None and impact > self.l.max_slippage:
            return False, f"SLIPPAGE_LIMIT: {impact:.2%} > {self.l.max_slippage:.2%}"
        return True, "ok"


# ----------------------------------------------------------------------------------------------- journal
@dataclass
class Order:
    coid: str                                  # client order id (idempotency key)
    side: str                                  # BUY | SELL
    mint: str
    usd: float = 0.0                           # BUY: planned spend
    amount_raw: int = 0                        # SELL: exact base units
    state: str = "NEW"                         # NEW, BLOCKED, QUOTED, SIMULATED, SUBMITTING, SUBMITTED, CONFIRMED, FAILED, DRY_RUN_STOP
    reason: str = ""
    quote: Optional[dict] = None
    signature: Optional[str] = None
    filled_raw: Optional[int] = None           # only from a confirmation, never from a quote
    proceeds_lamports: Optional[int] = None
    history: List[str] = field(default_factory=list)


class Journal:
    def __init__(self, path):
        self.path, self.orders = path, {}
        if os.path.exists(path):
            for line in open(path):
                o = Order(**json.loads(line))
                self.orders[o.coid] = o                # last write wins -> current state

    def write(self, o: Order, note: str):
        o.history.append(f"{time.time():.3f} {o.state} {note}")
        self.orders[o.coid] = o
        with open(self.path, "a") as f:
            f.write(json.dumps(asdict(o)) + "\n")
            f.flush(); os.fsync(f.fileno())


def coid_for(signal_id: str, leg: str) -> str:
    return hashlib.sha256(f"{signal_id}|{leg}".encode()).hexdigest()[:24]


# ----------------------------------------------------------------------------------------------- brokers
class LiveBroker:
    """The only non-test broker. Deliberately cannot trade."""

    def submit(self, order, quote):
        raise ExecutionDisabled("signing and sending are not implemented; a real pilot needs separate approval")

    def status(self, signature):
        raise ExecutionDisabled("no live submissions exist")

    def balance_raw(self, mint):
        raise ExecutionDisabled("no wallet is configured")


# ----------------------------------------------------------------------------------------------- engine
class ExecutionEngine:
    def __init__(self, journal: Journal, guard: RiskGuard, quote: Callable, simulate: Callable, broker,
                 clock: Callable = time.time):
        self.j, self.g, self.quote_fn, self.sim_fn, self.broker, self.now = journal, guard, quote, simulate, broker, clock

    # -- helpers
    def _rpc(self, fn, *a):
        last = None
        for _ in range(RPC_RETRIES):
            try:
                return fn(*a)
            except RpcError as e:
                last = e
        raise last

    def exposure_usd(self):
        """Cost basis still at risk: open part of confirmed buys + every buy whose outcome is not final yet."""
        pending = sum(o.usd for o in self.j.orders.values() if o.side == "BUY" and o.state in ("SUBMITTING", "SUBMITTED"))
        held = 0.0
        for mint in {o.mint for o in self.j.orders.values()}:
            buys = [o for o in self.j.orders.values() if o.mint == mint and o.side == "BUY" and o.state == "CONFIRMED"]
            raw = sum(o.filled_raw or 0 for o in buys)
            if raw:
                held += sum(o.usd for o in buys) * self.position_raw(mint) / raw
        return pending + held

    def position_raw(self, mint):
        b = sum(o.filled_raw or 0 for o in self.j.orders.values() if o.mint == mint and o.side == "BUY" and o.state == "CONFIRMED")
        s = sum(o.filled_raw or 0 for o in self.j.orders.values() if o.mint == mint and o.side == "SELL" and o.state == "CONFIRMED")
        return b - s

    def _fresh_quote(self, o):
        q = self._rpc(self.quote_fn, o)
        if q is None:
            return None
        if self.now() - q["t"] > QUOTE_TTL_S:                    # expired before use -> one re-quote, never a stale fill
            q = self._rpc(self.quote_fn, o)
            if q is None or self.now() - q["t"] > QUOTE_TTL_S:
                return "EXPIRED"
        return q

    def _submit(self, o, q, note):
        o.state = "SUBMITTING"; self.j.write(o, note)           # journal first: a crash here is resolved by recover()
        try:
            sig = self.broker.submit(o, q)                       # NEVER retried: a lost response may hide a landed tx
        except ExecutionDisabled as e:
            o.state, o.reason = "DRY_RUN_STOP", str(e); self.j.write(o, "not sent (no signing/sending in this build)")
            return o
        except RpcError as e:
            o.state, o.reason = "SUBMITTING", f"rpc error during submit: {e}"; self.j.write(o, "outcome unknown -> recover()")
            self.g.state_error = "submit outcome unknown"
            return o
        o.state, o.signature = "SUBMITTED", sig; self.j.write(o, "submitted")
        return self.confirm(o)

    def confirm(self, o):
        try:
            st = self._rpc(self.broker.status, o.signature)
        except RpcError as e:
            self.g.state_error = f"cannot confirm {o.coid}: {e}"
            return o
        if st is None:
            return o                                             # still pending; recover() / next call will look again
        if st["ok"]:
            o.state, o.filled_raw, o.proceeds_lamports = "CONFIRMED", st.get("filled_raw"), st.get("proceeds_lamports")
            self.j.write(o, "confirmed on chain")
        else:
            o.state, o.reason = "FAILED", st.get("error", "failed"); self.j.write(o, "failed on chain")
        return o

    # -- public
    def buy(self, signal_id, leg, mint, usd, data_age_s, day):
        coid = coid_for(signal_id, leg)
        if coid in self.j.orders:                                # duplicate protection: same signal + tranche = same order
            return self.j.orders[coid]
        o = Order(coid, "BUY", mint, usd=usd)
        ok, why = self.g.check_entry(usd, self.exposure_usd(), day, data_age_s)
        if not ok:
            o.state, o.reason = "BLOCKED", why; self.j.write(o, why); return o
        q = self._fresh_quote(o)
        if q in (None, "EXPIRED"):
            o.state, o.reason = "BLOCKED", "no route" if q is None else "quote expired twice"; self.j.write(o, o.reason); return o
        ok, why = self.g.check_entry(usd, self.exposure_usd(), day, data_age_s, impact=q.get("impact"))
        if not ok:
            o.state, o.reason = "BLOCKED", why; self.j.write(o, why); return o
        o.quote, o.state = q, "QUOTED"; self.j.write(o, "quoted")
        sim = self._rpc(self.sim_fn, o, q)
        if not sim["ok"]:
            o.state, o.reason = "BLOCKED", f"simulation failed: {sim.get('error')}"; self.j.write(o, o.reason); return o
        o.state = "SIMULATED"; self.j.write(o, "simulation ok (not an execution)")
        return self._submit(o, q, "buy")

    def sell(self, signal_id, leg, mint, amount_raw):
        """Exits are never blocked by the risk guard. Without a route the position stays open (no invented sale)."""
        coid = coid_for(signal_id, leg)
        if coid in self.j.orders and self.j.orders[coid].state in ("SUBMITTING", "SUBMITTED", "CONFIRMED"):
            return self.j.orders[coid]
        amount_raw = min(amount_raw, self.position_raw(mint))
        o = Order(coid, "SELL", mint, amount_raw=amount_raw)
        if amount_raw <= 0:
            o.state, o.reason = "BLOCKED", "nothing to sell"; self.j.write(o, o.reason); return o
        q = self._fresh_quote(o)
        if q in (None, "EXPIRED"):
            o.state, o.reason = "BLOCKED", "EXIT_NO_ROUTE" if q is None else "EXIT_QUOTE_EXPIRED"
            self.j.write(o, "position stays open; retry next cycle"); return o
        o.quote, o.state = q, "QUOTED"; self.j.write(o, "quoted")
        sim = self._rpc(self.sim_fn, o, q)
        if not sim["ok"]:
            o.state, o.reason = "BLOCKED", f"EXIT_SIMULATION_FAILED: {sim.get('error')}"; self.j.write(o, "position stays open"); return o
        o.state = "SIMULATED"; self.j.write(o, "simulation ok (not an execution)")
        return self._submit(o, q, "sell")

    def recover(self):
        """After a restart: resolve every order whose outcome is unknown; never resubmit one that may have been sent."""
        for o in list(self.j.orders.values()):
            if o.state == "SUBMITTED":
                self.confirm(o)
            elif o.state == "SUBMITTING":                        # crashed around submit: the network decides, not a resend
                sig = getattr(self.broker, "find_by_coid", lambda c: None)(o.coid)
                if sig:
                    o.signature, o.state = sig, "SUBMITTED"; self.j.write(o, "found after restart"); self.confirm(o)
                else:
                    o.state, o.reason = "FAILED", "not found on chain after restart"; self.j.write(o, o.reason)
        return self.reconcile()

    def reconcile(self):
        """Journal position vs broker balance for every mint. A mismatch blocks new entries (STATE_ERROR)."""
        bad = {}
        for mint in {o.mint for o in self.j.orders.values()}:
            try:
                chain = self._rpc(self.broker.balance_raw, mint)
            except (RpcError, ExecutionDisabled) as e:
                self.g.state_error = f"reconcile unavailable: {e}"
                return {"ok": False, "error": str(e)}
            if chain != self.position_raw(mint):
                bad[mint] = {"journal": self.position_raw(mint), "chain": chain}
        if bad:
            self.g.state_error = f"balance mismatch {bad}"
        return {"ok": not bad, "mismatch": bad}
