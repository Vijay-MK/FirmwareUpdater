"""Collect, store and read device diagnostics. Everything here is read-only towards the PLC."""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..drivers import DriverError, driver_for
from ..drivers.diag import DiagReport
from ..models import Device, DiagSnapshot, utcnow

log = logging.getLogger("fwmgr")
KEEP_SNAPSHOTS = 50


def run_diagnostics(db: Session, device: Device, username: str) -> DiagSnapshot:
    """Query the device, store the result as a snapshot and update the device's online status."""
    try:
        report = driver_for(device).diagnostics()
    except DriverError as exc:
        report = DiagReport(reachable=False, notes=[str(exc)])
    except Exception:  # a driver bug must not break the page; details go to the server log
        log.exception("Diagnostics failed for device %s", device.id)
        report = DiagReport(reachable=False, notes=["Unexpected error while collecting diagnostics."])

    snapshot = DiagSnapshot(device_id=device.id, collected_by=username, reachable=report.reachable,
                            run_state=report.run_state, health=report.health,
                            latency_ms=report.latency_ms, data=json.dumps(report.payload()))
    db.add(snapshot)
    device.status = "online" if report.reachable else "offline"
    if report.reachable:
        device.last_seen = utcnow()
    db.commit()
    _prune(db, device.id)
    return snapshot


def _prune(db: Session, device_id: int) -> None:
    old = db.scalars(select(DiagSnapshot).where(DiagSnapshot.device_id == device_id)
                     .order_by(DiagSnapshot.id.desc()).offset(KEEP_SNAPSHOTS)).all()
    for snap in old:
        db.delete(snap)
    if old:
        db.commit()


def history(db: Session, device_id: int, limit: int = 20) -> list[DiagSnapshot]:
    return list(db.scalars(select(DiagSnapshot).where(DiagSnapshot.device_id == device_id)
                           .order_by(DiagSnapshot.id.desc()).limit(limit)).all())


def get_snapshot(db: Session, device_id: int, snapshot_id: int) -> DiagSnapshot | None:
    snap = db.get(DiagSnapshot, snapshot_id)
    return snap if snap and snap.device_id == device_id else None
