"""
auth.py — JWT Cookie 鉴权、登录限流、当前用户
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import User, get_db, hash_password, utcnow, verify_password

SECRET_KEY = os.getenv("SECRET_KEY", "voltane-dev-secret-change-me")
# 只有部署在反向代理之后才信任 X-Forwarded-For，直连时忽略以防伪造绕过限流
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes")
# HTTPS 反代部署时设为 true，会话 Cookie 只走加密信道
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
ALGORITHM = "HS256"
COOKIE_NAME = "voltane_token"
SESSION_EXPIRE_HOURS = 24
REMEMBER_EXPIRE_DAYS = 90

# 登录失败限流：同 IP 连续失败 N 次后锁定 M 秒
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "8"))
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "300"))

_guard_lock = threading.Lock()
_failures: dict[str, list[float]] = defaultdict(list)
_locked_until: dict[str, float] = {}


def warn_if_weak_secret() -> None:
    weak = {"voltane-dev-secret-change-me", "change-me-to-a-long-random-secret", ""}
    if SECRET_KEY in weak or len(SECRET_KEY) < 24:
        print("[Voltane] 警告: SECRET_KEY 过于简单或仍为默认值，请在 docker-compose.yml 中设置随机长密钥。")


def create_token(user: User, remember: bool = False) -> tuple[str, int]:
    life = timedelta(days=REMEMBER_EXPIRE_DAYS) if remember else timedelta(hours=SESSION_EXPIRE_HOURS)
    payload = {"uid": user.id, "cv": int(user.credentials_version or 0), "exp": utcnow() + life}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM), int(life.total_seconds())


def token_expires_at(max_age: int) -> str:
    """令牌到期时间，UTC，ISO 格式带 Z，给 iOS 客户端判断刷新时机。"""
    return (utcnow() + timedelta(seconds=max_age)).isoformat(timespec="seconds") + "Z"


def token_body(token: str, max_age: int) -> dict:
    return {"token": token, "expires_at": token_expires_at(max_age)}


def set_auth_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=max_age,
                        path="/", secure=COOKIE_SECURE)


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=COOKIE_SECURE)


def client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    for ip in list(_locked_until):
        if _locked_until[ip] <= now:
            _locked_until.pop(ip, None)
            _failures.pop(ip, None)
    for ip in list(_failures):
        win = [t for t in _failures[ip] if now - t < LOGIN_LOCK_SECONDS]
        if win:
            _failures[ip] = win
        else:
            _failures.pop(ip, None)


def check_login_allowed(ip: str) -> None:
    now = time.time()
    with _guard_lock:
        _sweep(now)
        until = _locked_until.get(ip, 0)
        if until > now:
            raise HTTPException(429, f"登录失败次数过多，请 {int(until - now)} 秒后再试")


def record_login_failure(ip: str) -> None:
    now = time.time()
    with _guard_lock:
        _sweep(now)
        _failures[ip].append(now)
        if len(_failures[ip]) >= LOGIN_MAX_FAILURES:
            _locked_until[ip] = now + LOGIN_LOCK_SECONDS
            _failures[ip] = []


def clear_login_failures(ip: str) -> None:
    with _guard_lock:
        _failures.pop(ip, None)
        _locked_until.pop(ip, None)


_DUMMY_HASH = hash_password("voltane-dummy-password")


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        verify_password(password, _DUMMY_HASH)
        raise HTTPException(401, "用户名或密码错误")
    if not verify_password(password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账户已被禁用")
    return user


def get_current_user(
    request: Request,
    token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """优先读 Authorization: Bearer；带了请求头但令牌无效时直接 401，不回退去读 Cookie。"""
    via_bearer = authorization is not None
    if via_bearer:
        scheme, _, value = authorization.partition(" ")
        token = value.strip() if scheme.lower() == "bearer" else None
    request.state.via_bearer = via_bearer
    if not token:
        raise HTTPException(401, "登录已失效，请重新登录" if via_bearer else "未登录")
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid, cv = int(data["uid"]), int(data.get("cv", 0))
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "登录已失效，请重新登录") from exc
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已禁用")
    if cv != int(user.credentials_version or 0):
        raise HTTPException(401, "登录已失效，请重新登录")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


AVATAR_COLORS = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6"]


def user_brief(user: Optional[User]) -> Optional[dict]:
    if not user:
        return None
    color = user.avatar_color or AVATAR_COLORS[user.id % len(AVATAR_COLORS)]
    return {"id": user.id, "username": user.username, "name": user.name, "color": color}


def user_to_dict(user: User) -> dict:
    return {
        **user_brief(user),
        "role": user.role,
        "is_admin": user.role == "admin",
        "must_change_password": user.must_change_password,
        "settings": user.get_settings(),
    }
