"""
reminders.py — 提醒到期状态、按里程预测日期、完成后顺延
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class ReminderIn:
    due_date: Optional[date] = None
    due_odo: Optional[float] = None
    interval_months: Optional[int] = None
    interval_km: Optional[float] = None
    lead_days: int = 30
    lead_km: float = 500
    last_done_date: Optional[date] = None
    last_done_odo: Optional[float] = None


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def evaluate(r: ReminderIn, today: date, odo: Optional[float], daily_km: Optional[float]) -> dict:
    """日期和里程哪个先到算哪个；按里程的提醒用日均里程估算到期日。"""
    days_left = (r.due_date - today).days if r.due_date else None
    km_left = round(r.due_odo - odo, 1) if (r.due_odo is not None and odo is not None) else None
    eta = None
    if km_left is not None and daily_km and daily_km > 0:
        eta = today + timedelta(days=max(int(km_left / daily_km), 0)) if km_left > 0 else today
    status = "ok"
    if (days_left is not None and days_left < 0) or (km_left is not None and km_left < 0):
        status = "overdue"
    elif (days_left is not None and days_left <= r.lead_days) or (km_left is not None and km_left <= r.lead_km):
        status = "soon"
    # 进度：从上次完成到到期点已走过的比例
    date_progress = km_progress = None
    if r.due_date and r.last_done_date and r.due_date > r.last_done_date:
        total = (r.due_date - r.last_done_date).days
        date_progress = min(max((today - r.last_done_date).days / total, 0), 1.2)
    if r.due_odo is not None and r.last_done_odo is not None and odo is not None and r.due_odo > r.last_done_odo:
        km_progress = min(max((odo - r.last_done_odo) / (r.due_odo - r.last_done_odo), 0), 1.2)
    effective = [d for d in (r.due_date, eta) if d]
    return {
        "status": status,
        "days_left": days_left,
        "km_left": km_left,
        "eta_date": eta.isoformat() if eta else None,
        "next_date": min(effective).isoformat() if effective else None,
        "date_progress": round(date_progress, 3) if date_progress is not None else None,
        "km_progress": round(km_progress, 3) if km_progress is not None else None,
    }


def next_due(r: ReminderIn, done_date: date, done_odo: Optional[float]) -> tuple:
    """以实际完成的日期和里程为基准顺延；没有周期的一次性提醒返回 (None, None)。"""
    nd = add_months(done_date, r.interval_months) if r.interval_months else None
    no = done_odo + r.interval_km if (r.interval_km and done_odo is not None) else None
    return nd, no
