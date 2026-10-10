from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import current_user, flash, get_db, render, verify_csrf
from ..models import User
from ..security import hash_password, login_throttle, validate_password, verify_password

router = APIRouter()

# Pre-computed so that unknown usernames cost the same time as wrong passwords.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(request: Request, username: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    key = f"{username.lower()}|{request.client.host if request.client else ''}"
    if login_throttle.blocked(key):
        audit(db, username[:64], "login.blocked", "too many failures")
        return render(request, "login.html", error="Too many failed attempts. Try again in 5 minutes.")
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not (user and user.active and ok):
        login_throttle.fail(key)
        audit(db, username[:64], "login.failed", "")
        return render(request, "login.html", error="Invalid username or password.")
    login_throttle.reset(key)
    request.session.clear()  # new session on login (prevents fixation)
    request.session["uid"] = user.id
    audit(db, user.username, "login", "")
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    audit(db, user.username, "logout", "")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account")
def account(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return render(request, "account.html", user, db)


@router.post("/account/password", dependencies=[Depends(verify_csrf)])
def change_password(request: Request, current: str = Form(...), new: str = Form(...),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    error = None
    if not verify_password(current, user.password_hash):
        error = "Current password is incorrect."
    else:
        error = validate_password(new)
    if error:
        flash(request, error, "error")
    else:
        user.password_hash = hash_password(new)
        db.commit()
        audit(db, user.username, "password.change", "")
        flash(request, "Password changed.")
    return RedirectResponse("/account", status_code=303)
