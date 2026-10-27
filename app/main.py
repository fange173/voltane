"""
main.py — 应用装配：中间件、路由、静态资源、SPA 回退、后台任务
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.auth import warn_if_weak_secret
from app.database import ActivityLog, Expense, Record, SessionLocal, get_db, init_db, localnow, utcnow
from app.routers import admin, auth, expenses, records, stats, vehicles

STATIC_DIR = Path(__file__).parent / "static"
CSS_DIR = STATIC_DIR / "css"
JS_DIR = STATIC_DIR / "js"
TRASH_RETENTION_DAYS = int(os.getenv("TRASH_RETENTION_DAYS", "30"))
ACTIVITY_RETENTION_DAYS = int(os.getenv("ACTIVITY_RETENTION_DAYS", "180"))
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "").lower() in ("1", "true", "yes")
APP_VERSION = "0.1.0"
# 只有不兼容的接口改动才加 1，iOS 客户端据此提示更新
API_VERSION = 1


def run_housekeeping() -> None:
    """每日自动备份、清理回收站和过期动态。"""
    try:
        admin.auto_backup()
    except Exception as exc:  # 备份失败不能影响服务
        print(f"[Voltane] 自动备份失败: {exc}")
    with SessionLocal() as db:
        cutoff = utcnow() - timedelta(days=TRASH_RETENTION_DAYS)
        db.execute(delete(Record).where(Record.deleted_at.is_not(None), Record.deleted_at < cutoff))
        db.execute(delete(Expense).where(Expense.deleted_at.is_not(None), Expense.deleted_at < cutoff))
        db.execute(delete(ActivityLog).where(ActivityLog.created_at < utcnow() - timedelta(days=ACTIVITY_RETENTION_DAYS)))
        db.commit()


async def _jobs_loop() -> None:
    while True:
        if datetime.now().hour >= 3:
            await asyncio.to_thread(run_housekeeping)
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    warn_if_weak_secret()
    init_db()
    task = asyncio.create_task(_jobs_loop()) if os.getenv("DISABLE_JOBS") != "1" else None
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="Voltane",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    base = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(self)",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'"
        ),
    }
    for k, v in base.items():
        resp.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        ok = True
    except Exception:
        ok = False
    body = {
        "ok": ok, "service": "voltane", "version": APP_VERSION, "api_version": API_VERSION,
        "tz": os.getenv("TZ") or time.tzname[0], "server_time": localnow().isoformat(timespec="seconds"),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


for r in (auth.router, vehicles.router, records.router, expenses.router, stats.router, admin.router):
    app.include_router(r)


# ---------- 静态资源 ----------

_css_cache: dict = {}
_html_cache: dict = {}


def _css_bundle() -> tuple[str, str]:
    """样式按 css/NN-*.css 拆分，按文件名顺序拼接成一个请求，零构建。"""
    parts = sorted(CSS_DIR.glob("*.css"))
    key = ":".join(f"{p.name}={p.stat().st_mtime_ns}" for p in parts)
    if _css_cache.get("key") != key:
        _css_cache["css"] = "\n".join(f"/* ==== {p.name} ==== */\n{p.read_text(encoding='utf-8')}" for p in parts)
        _css_cache["etag"] = '"' + hashlib.sha1(key.encode()).hexdigest()[:16] + '"'
        _css_cache["key"] = key
    return _css_cache["css"], _css_cache["etag"]


def _asset_version() -> str:
    files = list(CSS_DIR.glob("*.css")) + list(JS_DIR.glob("*.js")) + [STATIC_DIR / "index.html"]
    return str(max(f.stat().st_mtime_ns for f in files))[-10:]


def _spa_html() -> str:
    """把 index.html 里的 ?v= 换成静态资源的最新修改时间，部署后自动破缓存。"""
    ver = _asset_version()
    if _html_cache.get("ver") != ver:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        _html_cache["html"] = re.sub(r"\?v=[^\"']*", f"?v={ver}", html)
        _html_cache["ver"] = ver
    return _html_cache["html"]


@app.get("/static/style.css")
def style_css(request: Request):
    css, etag = _css_bundle()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(css, media_type="text/css", headers={"ETag": etag, "Cache-Control": "no-cache"})


class NoCacheStatic(StaticFiles):
    """JS 子模块按相对路径导入、不带版本号，必须每次协商缓存，保证和入口同批更新。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".webmanifest")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return HTMLResponse(_spa_html(), headers={"Cache-Control": "no-cache"})
