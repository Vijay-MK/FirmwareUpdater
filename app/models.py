import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLES = ["viewer", "engineer", "approver", "admin"]
ROLE_LEVEL = {r: i + 1 for i, r in enumerate(ROLES)}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def has_role(self, minimum: str) -> bool:
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[minimum]


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("ip_address", "port", name="uq_device_endpoint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    vendor: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100), default="")
    driver: Mapped[str] = mapped_column(String(32))
    ip_address: Mapped[str] = mapped_column(String(45))
    port: Mapped[int] = mapped_column(Integer)
    group_name: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    firmware_version: Mapped[str] = mapped_column(String(64), default="")
    hardware_revision: Mapped[str] = mapped_column(String(64), default="")
    serial_number: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # online/offline/unknown
    version_source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/device
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    jobs: Mapped[list["UpdateJob"]] = relationship(back_populates="device", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="device", cascade="all, delete-orphan")
    diagnostics: Mapped[list["DiagSnapshot"]] = relationship(
        back_populates="device", cascade="all, delete-orphan")


class FirmwareRelease(Base):
    __tablename__ = "firmware_releases"
    __table_args__ = (UniqueConstraint("vendor", "model_pattern", "version", name="uq_release"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(32))
    model_pattern: Mapped[str] = mapped_column(String(100), default="*")  # fnmatch pattern
    version: Mapped[str] = mapped_column(String(64))
    release_date: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    download_url: Mapped[str] = mapped_column(String(500), default="")
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")           # hash of stored file
    expected_sha256: Mapped[str] = mapped_column(String(64), default="")  # hash published by vendor
    hash_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    release_notes: Mapped[str] = mapped_column(Text, default="")
    # AI / heuristic analysis (advisory only)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_risk: Mapped[str] = mapped_column(String(16), default="")
    ai_urgency: Mapped[str] = mapped_column(String(16), default="")
    ai_rationale: Mapped[str] = mapped_column(Text, default="")
    cves: Mapped[str] = mapped_column(Text, default="")
    ai_source: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def has_file(self) -> bool:
        return bool(self.file_path)



class FeedSource(Base):
    """A JSON firmware feed (vendor mirror, internal repository or custom adapter output)."""
    __tablename__ = "feed_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    auth_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(300), default="never checked")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("device_id", "release_id", name="uq_notification"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    release_id: Mapped[int] = mapped_column(ForeignKey("firmware_releases.id"))
    message: Mapped[str] = mapped_column(String(500))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    device: Mapped[Device] = relationship(back_populates="notifications")
    release: Mapped[FirmwareRelease] = relationship()


class UpdateJob(Base):
    __tablename__ = "update_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    release_id: Mapped[int] = mapped_column(ForeignKey("firmware_releases.id"))
    # pending_approval, approved, running, needs_verification, completed, failed, rejected, cancelled
    status: Mapped[str] = mapped_column(String(24), default="pending_approval")
    from_version: Mapped[str] = mapped_column(String(64), default="")
    requested_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    backup_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    window_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(String(500), default="")
    log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    device: Mapped[Device] = relationship(back_populates="jobs")
    release: Mapped[FirmwareRelease] = relationship()

    @property
    def active(self) -> bool:
        return self.status in ("pending_approval", "approved", "running", "needs_verification")


class DiagSnapshot(Base):
    """One diagnostics run for a device (state, facts and the event/diagnostic buffer as read)."""
    __tablename__ = "diag_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    collected_by: Mapped[str] = mapped_column(String(64), default="")
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    run_state: Mapped[str] = mapped_column(String(16), default="unknown")
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")  # JSON: facts, events, notes

    device: Mapped[Device] = relationship(back_populates="diagnostics")
    advice: Mapped["DiagAdvice | None"] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", uselist=False)

    @property
    def parsed(self) -> dict:
        try:
            value = json.loads(self.data or "{}")
        except ValueError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {"facts": value.get("facts") or {}, "events": value.get("events") or [],
                "notes": value.get("notes") or [],
                "events_available": bool(value.get("events_available"))}


class DiagAdvice(Base):
    """LLM troubleshooting advice for one diagnostics snapshot (the rule-based help is not stored)."""
    __tablename__ = "diag_advice"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("diag_snapshots.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(Text, default="{}")  # JSON: summary, issues[]

    snapshot: Mapped[DiagSnapshot] = relationship(back_populates="advice")

    @property
    def parsed(self) -> dict:
        try:
            value = json.loads(self.content or "{}")
        except ValueError:
            value = {}
        return value if isinstance(value, dict) else {}


class AppSetting(Base):
    """Runtime settings edited in the admin UI (AI endpoint etc.). Secrets are stored encrypted."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
