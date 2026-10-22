"""
auth.py（路由）— 登录、退出、当前用户、改密、个人设置、邀请注册
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    check_login_allowed,
    clear_auth_cookie,
    clear_login_failures,
    client_ip,
    create_token,
    get_current_user,
    record_login_failure,
    set_auth_cookie,
    user_to_dict,
)
from app.database import Invite, User, VehicleMember, get_db, get_setting, hash_password, utcnow, verify_password
from app.services import log_activity

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    remember: bool = True


class ChangePasswordIn(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=32)
    avatar_color: str | None = Field(None, max_length=16)


class SignupIn(BaseModel):
    code: str = Field(..., min_length=4, max_length=16)
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=32)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    check_login_allowed(ip)
    try:
        user = authenticate(db, body.username.strip(), body.password)
    except HTTPException:
        record_login_failure(ip)
        raise
    clear_login_failures(ip)
    token, max_age = create_token(user, body.remember)
    set_auth_cookie(response, token, max_age)
    return user_to_dict(user)


@router.post("/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_to_dict(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, response: Response, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.credentials_version = (user.credentials_version or 0) + 1
    db.commit()
    token, max_age = create_token(user, True)
    set_auth_cookie(response, token, max_age)
    return {"ok": True}


@router.put("/settings")
def put_settings(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """合并写入个人偏好；值为 null 的键删除。"""
    cur = user.get_settings()
    for k, v in (body or {}).items():
        if not isinstance(k, str) or len(k) > 40:
            continue
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    raw = json.dumps(cur, ensure_ascii=False)
    if len(raw) > 20000:
        raise HTTPException(400, "设置内容过大")
    user.settings = raw
    db.commit()
    return cur


@router.put("/profile")
def put_profile(body: ProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.avatar_color is not None:
        user.avatar_color = body.avatar_color
    db.commit()
    return user_to_dict(user)


@router.post("/signup")
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db)):
    """通过允许注册的邀请链接自助建号并加入车辆；需要管理员先打开「允许邀请注册」。"""
    if get_setting(db, "allow_signup", "false") != "true":
        raise HTTPException(403, "管理员没有开放邀请注册")
    inv = db.scalar(select(Invite).where(Invite.code == body.code.upper()))
    if not inv or not inv.allow_signup or inv.revoked_at or inv.expires_at < utcnow() or inv.used_count >= inv.max_uses:
        raise HTTPException(400, "邀请码无效或已过期")
    name = body.username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "用户名只能包含字母、数字、下划线、点和短横线，2–32 位")
    if db.scalar(select(User).where(User.username == name)):
        raise HTTPException(409, "用户名已被占用")
    user = User(username=name, password_hash=hash_password(body.password), role="member",
                display_name=body.display_name.strip())
    db.add(user)
    db.flush()
    db.add(VehicleMember(vehicle_id=inv.vehicle_id, user_id=user.id, role=inv.role))
    inv.used_count += 1
    log_activity(db, inv.vehicle_id, user, "join", "member", user.id, f"{user.name} 通过邀请加入")
    db.commit()
    token, max_age = create_token(user, True)
    set_auth_cookie(response, token, max_age)
    return user_to_dict(user)
