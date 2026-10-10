"""Shared web dependencies: DB session, authentication, role checks, CSRF, templates."""
import hmac
import ipaddress
import secrets
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import BASE_DIR, settings
from .database import SessionLocal
from .models import Notification, User

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("uid")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.active:
        request.session.clear()
        raise LoginRequired()
    return user


def require(role: str):
    def checker(user: User = Depends(current_user)) -> User:
        if not user.has_role(role):
            raise HTTPException(403, f"Requires {role} role")
        return user
    return checker


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    """Dependency for every state-changing (POST) route."""
    form = await request.form()
    sent = str(form.get("csrf_token", ""))
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "Invalid or missing CSRF token")


def render(request: Request, name: str, user: User | None = None, db: Session | None = None,
           **context):
    unread = 0
    if user is not None and db is not None:
        unread = db.query(Notification).filter(Notification.read.is_(False)).count()
    context.update(user=user, csrf=csrf_token(request), unread=unread,
                   flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, context)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"msg": message, "kind": kind}


def redirect_url(path: str, **query) -> str:
    if not query:
        return path
    return path + "?" + "&".join(f"{k}={quote(str(v))}" for k, v in query.items())


def check_allowed_address(ip: str) -> str | None:
    """Validate an IP literal and the optional network allowlist. Returns an error or None."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return "Enter a valid IPv4/IPv6 address (hostnames are not accepted)."
    if addr.is_loopback or addr.is_multicast or addr.is_unspecified or addr.is_link_local:
        return "Loopback, multicast, unspecified and link-local addresses are not allowed."
    nets = settings.networks
    if nets and not any(addr in n for n in nets):
        return "Address is outside the allowed networks configured by the administrator."
    return None
