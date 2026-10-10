from sqlalchemy.orm import Session

from .models import AuditLog


def audit(db: Session, username: str, action: str, detail: str = "") -> None:
    db.add(AuditLog(username=username, action=action, detail=detail[:2000]))
    db.commit()
