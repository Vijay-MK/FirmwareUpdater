"""Offline password reset: `python -m app.reset_password [username]`.

Prompts for the new password (not echoed), so it never appears in shell history or the process list.
Also unlocks the account and re-activates it.
"""
import getpass
import sys

from sqlalchemy import select

from .database import SessionLocal, init_db
from .models import User
from .security import hash_password, validate_password


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "admin"
    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if not user:
            print(f"No such user: {username}")
            return 1
        password = getpass.getpass(f"New password for '{username}': ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return 1
        error = validate_password(password)
        if error:
            print(error)
            return 1
        user.password_hash = hash_password(password)
        user.active = True
        db.commit()
    print(f"Password for '{username}' updated. Restart is not required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
