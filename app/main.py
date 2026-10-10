import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, settings
from .database import SessionLocal, init_db
from .deps import LoginRequired, render
from .models import User
from .routers import admin, auth, devices, diagnostics, firmware, updates
from .security import generate_initial_password, hash_password, session_secret
from .services import ai_settings, catalog

log = logging.getLogger("fwmgr")


def bootstrap_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).limit(1)):
            return
        password = settings.admin_password or generate_initial_password()
        db.add(User(username="admin", password_hash=hash_password(password), role="admin"))
        db.commit()
        if not settings.admin_password:
            # Printed once to the server console; it is never stored in plain text.
            print(f"\n*** Initial admin account created.\n    username: admin\n"
                  f"    password: {password}\n    Change it after first login. ***\n", flush=True)


def scheduled_check() -> None:
    with SessionLocal() as db:
        try:
            catalog.sync_all_feeds(db)
            catalog.notify_all(db)
        except Exception:
            log.exception("Scheduled firmware check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_admin()
    with SessionLocal() as db:
        ai_settings.apply(db)      # AI settings saved in the UI override FWMGR_LLM_* variables
    scheduler = None
    if settings.enable_scheduler:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(scheduled_check, "interval", minutes=settings.check_interval_minutes,
                          id="firmware-check", max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="PLC Firmware Manager", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="strict",
                   https_only=settings.https_only, max_age=8 * 3600, session_cookie="fwmgr_session")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


for module in (auth, devices, diagnostics, firmware, updates, admin):
    app.include_router(module.router)
