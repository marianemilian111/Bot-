from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Pair:
    addr: str
    symbol: str
    source: str                 # pump.fun | raydium | meteora
    age_s: float                # since launch / migration
    price: float
    liquidity: Optional[float]   # None = unknown (DexScreener gives null for pump.fun curves)
    vol5m: float
    vol_avg5m_1h: float         # average 5m volume over the last hour
    buys5m: int
    sells5m: int
    holders: Optional[int]
    holders_10m: Optional[int]
    price_1h_ago: Optional[float]
    pullback_pct: Optional[float]   # drop from the 1h high (0..1)
    smart_buyers_10m: int = 0
    kol_bought: bool = False
    # security data - None means "unknown" and is treated as FAIL
    dev_pct: Optional[float] = None
    top10_pct: Optional[float] = None
    bundled: Optional[bool] = None
    lp_status: Optional[str] = None     # burned | locked_30d | locked_short | unlocked
    mint_frozen: Optional[bool] = None
    mint_auth_open: Optional[bool] = None
    honeypot: Optional[bool] = None     # result of the sell simulation
    dev_sold: bool = False
    lp_pulled: bool = False
    pair_addr: Optional[str] = None


@dataclass
class Flag:
    pair: Pair
    signals: List[str]
    universe_fail: Optional[str] = None


@dataclass
class Position:
    addr: str
    symbol: str
    opened: datetime
    planned_usd: float
    signals: List[str]
    tokens: float = 0.0
    tokens_bought: float = 0.0
    cost: float = 0.0
    proceeds: float = 0.0
    parts_done: int = 0
    stage: int = 0              # 0 = none, 1 = TP1 hit, 2 = TP2 hit (runner)
    peak: float = 0.0
    last_price: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def avg_entry(self) -> float:
        return self.cost / self.tokens_bought if self.tokens_bought else 0.0


@dataclass
class Trade:
    symbol: str
    opened: datetime
    closed: datetime
    cost: float
    proceeds: float
    reasons: List[str]

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.cost * 100 if self.cost else 0.0
