from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ai
from ..audit import audit
from ..config import settings
from ..deps import current_user, flash, get_db, render, require, verify_csrf
from ..models import ROLES, AuditLog, Device, FirmwareRelease, Notification, UpdateJob, User
from ..security import hash_password, validate_password
from ..services import ai_settings, catalog

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    devices = db.scalars(select(Device)).all()
    outdated = [(d, catalog.available_updates(db, d)) for d in devices]
    outdated = [(d, u) for d, u in outdated if u]
    pending = db.scalars(select(UpdateJob).where(UpdateJob.status == "pending_approval")).all()
    recent = db.scalars(select(Notification).order_by(Notification.id.desc()).limit(8)).all()
    by_vendor = dict(db.execute(select(Device.vendor, func.count()).group_by(Device.vendor)).all())
    return render(request, "dashboard.html", user, db, total=len(devices), outdated=outdated,
                  offline=sum(1 for d in devices if d.status == "offline"), pending=pending,
                  recent=recent, by_vendor=by_vendor,
                  releases=db.scalar(select(func.count()).select_from(FirmwareRelease)))


@router.get("/notifications")
def notifications(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Notification).order_by(Notification.id.desc()).limit(200)).all()
    return render(request, "notifications.html", user, db, items=items)


@router.post("/notifications/read", dependencies=[Depends(verify_csrf)])
def mark_read(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.read.is_(False))).all():
        n.read = True
    db.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.get("/audit")
def audit_log(request: Request, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(500)).all()
    return render(request, "audit.html", user, db, rows=rows)


@router.get("/users")
def users(request: Request, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    return render(request, "users.html", user, db, users=db.scalars(select(User)).all(), roles=ROLES)


@router.post("/users/new", dependencies=[Depends(verify_csrf)])
def create_user(request: Request, username: str = Form(...), password: str = Form(...),
                role: str = Form(...), user: User = Depends(require("admin")),
                db: Session = Depends(get_db)):
    error = validate_password(password)
    if role not in ROLES:
        error = "Unknown role."
    if not username.strip() or len(username) > 64:
        error = "Username is required (max 64 characters)."
    if error:
        flash(request, error, "error")
        return RedirectResponse("/users", status_code=303)
    db.add(User(username=username.strip(), password_hash=hash_password(password), role=role))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "That username already exists.", "error")
        return RedirectResponse("/users", status_code=303)
    audit(db, user.username, "user.create", f"{username} role={role}")
    flash(request, "User created.")
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/update", dependencies=[Depends(verify_csrf)])
def update_user(request: Request, user_id: int, role: str = Form(...), active: bool = Form(False),
                new_password: str = Form(""), user: User = Depends(require("admin")),
                db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None or role not in ROLES:
        flash(request, "Invalid request.", "error")
    elif target.id == user.id and (role != "admin" or not active):
        flash(request, "You cannot demote or deactivate your own account.", "error")
    elif new_password and (error := validate_password(new_password)):
        flash(request, error, "error")
    else:
        target.role, target.active = role, active
        if new_password:
            target.password_hash = hash_password(new_password)
        db.commit()
        audit(db, user.username, "user.update", f"{target.username} role={role} active={active}")
        flash(request, "User updated.")
    return RedirectResponse("/users", status_code=303)


@router.get("/ai-settings")
def ai_settings_page(request: Request, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    return render(request, "ai_settings.html", user, db, cfg=ai_settings.view(db))


@router.post("/ai-settings", dependencies=[Depends(verify_csrf)])
def ai_settings_save(request: Request, base_url: str = Form(""), model: str = Form(""),
                     api_key: str = Form(""), consent: bool = Form(False), off: bool = Form(False),
                     user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    error = ai_settings.save(db, user.username, base_url, model, api_key, consent, off)
    if error:
        flash(request, error, "error")
    else:
        # The key itself is never written to the audit log.
        audit(db, user.username, "ai.settings.update",
              f"url={settings.llm_base_url} model={settings.llm_model} key_changed={bool(api_key.strip())} "
              f"share_data={consent} off={off}")
        flash(request, "AI settings saved.")
    return RedirectResponse("/ai-settings", status_code=303)


@router.post("/ai-settings/test", dependencies=[Depends(verify_csrf)])
def ai_settings_test(request: Request, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    try:
        flash(request, ai.check_connection())
        result = "ok"
    except ai.AdviceError as exc:
        flash(request, str(exc), "error")
        result = "failed"
    audit(db, user.username, "ai.settings.test", f"url={settings.llm_base_url} result={result}")
    return RedirectResponse("/ai-settings", status_code=303)


@router.post("/ai-settings/remove-key", dependencies=[Depends(verify_csrf)])
def ai_settings_remove_key(request: Request, user: User = Depends(require("admin")),
                           db: Session = Depends(get_db)):
    ai_settings.remove_key(db)
    audit(db, user.username, "ai.settings.key_removed", "")
    flash(request, "The saved API key was removed.")
    return RedirectResponse("/ai-settings", status_code=303)


@router.post("/ai-settings/reset", dependencies=[Depends(verify_csrf)])
def ai_settings_reset(request: Request, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    ai_settings.clear(db)
    audit(db, user.username, "ai.settings.reset", "saved AI settings removed; environment values apply")
    flash(request, "Saved AI settings removed. Environment variables (if any) apply again.")
    return RedirectResponse("/ai-settings", status_code=303)


@router.get("/assistant")
def assistant_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return render(request, "assistant.html", user, db, answer=None, question="")


@router.post("/assistant", dependencies=[Depends(verify_csrf)])
def assistant(request: Request, question: str = Form(...), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    lines = []
    for d in db.scalars(select(Device)).all():
        newer = ", ".join(r.version for r in catalog.available_updates(db, d)) or "none"
        lines.append(f"{d.name} | {d.vendor} {d.model} | installed {d.firmware_version or '?'} | "
                     f"status {d.status} | newer available: {newer}")
    answer = ai.answer_question(question[:1000], "\n".join(lines))
    return render(request, "assistant.html", user, db, answer=answer, question=question)
