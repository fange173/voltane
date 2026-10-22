"""
calibration.py — 学习 k（每 1% SOC 对应的电网侧 kWh）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median
from typing import Optional

from app.engine.types import Rec, VehicleParams

# 有效样本的最小 SOC 跨度（百分点）
MIN_SAMPLE_DSOC = 20.0
SAMPLE_WINDOW_DAYS = 180
MAX_SAMPLES = 10
# 没有样本时按 90% 充电效率估算
DEFAULT_CHARGE_EFF = 0.90
DC_TYPES = ("dc", "super")


@dataclass
class Calibration:
    k: Optional[float]
    source: str  # learned | single | default | none
    samples: list = field(default_factory=list)  # [(record_id, k_i, charge_type, ts)]
    k_ac: Optional[float] = None
    k_dc: Optional[float] = None

    @property
    def capacity_est(self) -> Optional[float]:
        if self.k is None or self.source not in ("learned", "single"):
            return None
        return round(self.k * 100 * DEFAULT_CHARGE_EFF, 1)


def _sample(r: Rec) -> Optional[float]:
    if r.kind != "charge" or r.charge_type == "swap":
        return None
    if r.charge_kwh is None or r.charge_kwh <= 0:
        return None
    if r.soc_before is None or r.soc_after is None:
        return None
    if {"charge_kwh", "soc_before", "soc_after"} & set(r.estimated_fields):
        return None
    d = r.soc_after - r.soc_before
    if d < MIN_SAMPLE_DSOC:
        return None
    return r.charge_kwh / d


def calibrate(params: VehicleParams, records: list[Rec]) -> Calibration:
    """取最近 180 天内最多 10 个有效样本的中位数；没有样本时用电池容量估算。"""
    charges = sorted((r for r in records if r.kind == "charge"), key=lambda r: r.ts)
    if charges:
        cutoff = charges[-1].ts - timedelta(days=SAMPLE_WINDOW_DAYS)
    samples = []
    for r in reversed(charges):
        if r.ts < cutoff:
            break
        k = _sample(r)
        if k is not None:
            samples.append((r.id, k, r.charge_type, r.ts))
        if len(samples) >= MAX_SAMPLES:
            break
    ac = [s[1] for s in samples if s[2] not in DC_TYPES]
    dc = [s[1] for s in samples if s[2] in DC_TYPES]
    k_ac = round(median(ac), 4) if ac else None
    k_dc = round(median(dc), 4) if dc else None

    if samples:
        source = "learned" if len(samples) >= 2 else "single"
        return Calibration(round(median(s[1] for s in samples), 4), source, samples, k_ac, k_dc)
    if params.battery_kwh:
        k = round(params.battery_kwh / DEFAULT_CHARGE_EFF / 100, 4)
        return Calibration(k, "default", samples, k_ac, k_dc)
    return Calibration(None, "none", samples, k_ac, k_dc)
