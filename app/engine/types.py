"""
types.py — 计算引擎的输入输出结构（纯数据，不依赖数据库）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

FUEL_TYPES = ("ice", "hev", "phev", "erev")
ELEC_TYPES = ("phev", "erev", "bev")

# 质量等级：A 精确 / B 估算 / C 粗略 / D 不足
QUALITY_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def worse(a: str, b: str) -> str:
    return a if QUALITY_ORDER[a] >= QUALITY_ORDER[b] else b


@dataclass
class VehicleParams:
    energy_type: str = "phev"
    tank_l: Optional[float] = None
    battery_kwh: Optional[float] = None
    charge_limit: float = 100.0
    # 油表读数误差（占油箱容积的百分比）
    gauge_err_pct: float = 5.0
    min_segment_km: float = 30.0
    kwh_per_l: float = 8.9
    allow_gauge_anchor: bool = True

    @property
    def has_fuel(self) -> bool:
        return self.energy_type in FUEL_TYPES

    @property
    def has_elec(self) -> bool:
        return self.energy_type in ELEC_TYPES


@dataclass
class Rec:
    """一条加油 / 充电 / 快照记录。odo 为空表示里程未知（补录）。"""

    id: int
    kind: str  # fuel | charge | snapshot
    ts: datetime
    odo: Optional[float] = None
    odo_estimated: bool = False
    fuel_l: Optional[float] = None
    fuel_full: bool = False
    fuel_before_pct: Optional[float] = None
    fuel_after_pct: Optional[float] = None
    charge_kwh: Optional[float] = None
    charge_type: Optional[str] = None
    charge_full: bool = False
    soc_before: Optional[float] = None
    soc_after: Optional[float] = None
    cost_cents: int = 0
    extra_cents: int = 0
    missed_before: bool = False
    snapshot_tag: Optional[str] = None
    estimated_fields: frozenset = frozenset()
    place_name: Optional[str] = None
    fuel_grade: Optional[str] = None
    user_id: Optional[int] = None


@dataclass
class Summary:
    """历史或周期汇总：一段时间内合计的补能量和金额。"""

    id: int
    start: date
    end: date
    fuel_l: Optional[float] = None
    fuel_cents: Optional[int] = None
    elec_kwh: Optional[float] = None
    elec_cents: Optional[int] = None
    start_odo: Optional[float] = None
    end_odo: Optional[float] = None
    # extra：填的是没逐条记录的部分；total：填的是总数，自动扣除已逐条记录的部分
    mode: str = "extra"
    fuel_price: Optional[float] = None
    elec_price: Optional[float] = None


@dataclass
class Mark:
    tag: str = "normal"
    excluded: bool = False


@dataclass
class Level:
    value: float
    err: float
    conf: str  # exact | measured | estimated

    @property
    def precise(self) -> bool:
        return self.conf in ("exact", "measured")


@dataclass
class Segment:
    energy: str  # fuel | elec | combined
    start_id: int
    end_id: int
    start_ts: datetime
    end_ts: datetime
    start_odo: float
    end_odo: float
    amount: Optional[float]  # 消耗量：L 或 kWh（综合段为油量）
    err: Optional[float]
    quality: str
    status: str = "ok"  # ok | negative | missed | incomplete | uncertain
    kind: str = "drive"  # drive | idle | short
    tag: str = "normal"
    excluded: bool = False
    anomaly: Optional[str] = None  # high | low | parked
    cost_cents: Optional[int] = None
    # 综合段专用
    elec_kwh: Optional[float] = None
    elec_err: Optional[float] = None
    elec_quality: Optional[str] = None
    record_ids: list = field(default_factory=list)

    @property
    def distance(self) -> float:
        return round(self.end_odo - self.start_odo, 1)

    @property
    def days(self) -> float:
        return max((self.end_ts - self.start_ts).total_seconds() / 86400.0, 0.0)

    @property
    def valid(self) -> bool:
        return self.status == "ok" and self.amount is not None

    @property
    def per100(self) -> Optional[float]:
        if not self.valid or self.distance < 1:
            return None
        return self.amount / self.distance * 100

    @property
    def rel_err(self) -> Optional[float]:
        if self.err is None or not self.amount:
            return None
        return abs(self.err / self.amount)
