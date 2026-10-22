"""
access.py — 车辆级鉴权：所有带 vehicle_id 的接口都经过这里
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import User, Vehicle, VehicleMember

ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}
ROLE_LABEL = {"owner": "车主", "editor": "共同记录", "viewer": "只看"}


def membership(db: Session, vehicle_id: int, user_id: int):
    return db.get(VehicleMember, (vehicle_id, user_id))


def require_vehicle(db: Session, user: User, vehicle_id: int, min_role: str = "viewer") -> tuple[Vehicle, str]:
    """查不到车和没有权限都返回 404，不暴露车辆是否存在。"""
    m = membership(db, vehicle_id, user.id)
    v = db.get(Vehicle, vehicle_id) if m else None
    if not m or not v:
        raise HTTPException(404, "车辆不存在")
    if ROLE_RANK[m.role] < ROLE_RANK[min_role]:
        if ROLE_RANK[m.role] == 0:
            raise HTTPException(403, "你在这辆车是「只看」角色，不能修改")
        raise HTTPException(403, "只有车主可以进行这项操作")
    if v.is_archived and min_role != "viewer" and min_role != "owner":
        raise HTTPException(403, "车辆已归档，只能查看")
    return v, m.role


def can_delete_entity(role: str, user: User, created_by) -> bool:
    """车主能删所有记录；共同记录人只能删自己记的。"""
    return role == "owner" or (role == "editor" and created_by == user.id)


def my_vehicle_ids(db: Session, user: User) -> list[int]:
    return list(db.scalars(select(VehicleMember.vehicle_id).where(VehicleMember.user_id == user.id)))


def owner_count(db: Session, vehicle_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(VehicleMember).where(
            VehicleMember.vehicle_id == vehicle_id, VehicleMember.role == "owner"
        )
    ) or 0
