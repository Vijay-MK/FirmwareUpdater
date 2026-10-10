"""Update workflow: request -> approve -> execute -> verify. Every step is audited and logged.

Safety rules enforced here (not just in the UI):
  * a human must request AND a different human with the approver role must approve
    (self-approval only if FWMGR_ALLOW_SELF_APPROVAL=1)
  * the stored file is re-hashed immediately before flashing and must match
  * vendor/model must match, and downgrades are refused
  * the PLC must be reachable and must not be in RUN
  * backup and maintenance window must be confirmed by the requester
  * only one active job per device
"""
import os
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..database import SessionLocal
from ..drivers import DriverError, driver_for
from ..models import Device, FirmwareRelease, UpdateJob, User, utcnow
from ..versioning import is_newer, same_version
from . import catalog

_device_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


class UpdateError(Exception):
    pass


def _lock_for(device_id: int) -> threading.Lock:
    with _locks_guard:
        return _device_locks.setdefault(device_id, threading.Lock())


def _log(job: UpdateJob, db: Session, message: str) -> None:
    job.log = (job.log or "") + f"[{utcnow():%Y-%m-%d %H:%M:%S}] {message}\n"
    db.commit()


def request_update(db: Session, user: User, device: Device, release: FirmwareRelease,
                   backup_confirmed: bool, window_confirmed: bool, comment: str = "") -> UpdateJob:
    if not catalog.matches(release, device):
        raise UpdateError("This firmware does not match the device vendor/model.")
    if not release.has_file or not os.path.isfile(release.file_path or ""):
        raise UpdateError("Firmware package is not in the repository. Upload or download it first.")
    if not device.firmware_version:
        raise UpdateError("Current firmware version is unknown. Set it on the device first.")
    if not is_newer(release.version, device.firmware_version):
        raise UpdateError("Only upgrades are allowed from this tool (no downgrade or same version).")
    if not backup_confirmed:
        raise UpdateError("Confirm that the PLC program/configuration has been backed up.")
    if not window_confirmed:
        raise UpdateError("Confirm that a maintenance window is approved for this device.")
    if db.scalar(select(UpdateJob).where(
            UpdateJob.device_id == device.id,
            UpdateJob.status.in_(["pending_approval", "approved", "running", "needs_verification"]))):
        raise UpdateError("This device already has an active update job.")
    job = UpdateJob(device_id=device.id, release_id=release.id, from_version=device.firmware_version,
                    requested_by=user.username, backup_confirmed=True, window_confirmed=True,
                    comment=comment[:500])
    db.add(job)
    db.commit()
    _log(job, db, f"Requested by {user.username}: {device.firmware_version} -> {release.version}")
    audit(db, user.username, "update.request", f"job={job.id} device={device.name} "
          f"to={release.version}")
    return job


def approve(db: Session, user: User, job: UpdateJob, acknowledge_unverified: bool = False) -> None:
    if job.status != "pending_approval":
        raise UpdateError("Job is not awaiting approval.")
    if not user.has_role("approver"):
        raise UpdateError("Approver role required.")
    if user.username == job.requested_by and not settings.allow_self_approval:
        raise UpdateError("A different person must approve this update (four-eyes principle).")
    if not job.release.hash_verified and not acknowledge_unverified:
        raise UpdateError("The package hash is not verified against a vendor-published SHA-256. "
                          "Tick the acknowledgement only if you verified integrity another way.")
    job.status = "approved"
    job.approved_by = user.username
    _log(job, db, f"Approved by {user.username}"
         + ("" if job.release.hash_verified else " (hash NOT verified; acknowledged)"))
    audit(db, user.username, "update.approve", f"job={job.id}")


def reject(db: Session, user: User, job: UpdateJob) -> None:
    if job.status not in ("pending_approval", "approved"):
        raise UpdateError("Job can no longer be rejected or cancelled.")
    if not (user.has_role("approver") or user.username == job.requested_by):
        raise UpdateError("Only an approver or the requester can cancel this job.")
    job.status = "rejected" if user.username != job.requested_by else "cancelled"
    _log(job, db, f"{job.status.capitalize()} by {user.username}")
    audit(db, user.username, f"update.{job.status}", f"job={job.id}")


def confirm_manual(db: Session, user: User, job: UpdateJob, version: str) -> None:
    """Close a 'needs_verification' job after a human checked the version on the device."""
    if job.status != "needs_verification":
        raise UpdateError("Job is not awaiting verification.")
    if not same_version(version, job.release.version):
        job.status = "failed"
        _log(job, db, f"{user.username} reported version {version}, expected {job.release.version}")
        audit(db, user.username, "update.verify_failed", f"job={job.id} reported={version}")
        return
    job.status = "completed"
    job.device.firmware_version = job.release.version
    _log(job, db, f"Manually verified by {user.username}: {version}")
    audit(db, user.username, "update.verified", f"job={job.id}")


def preflight(db: Session, job: UpdateJob, log) -> None:
    device, release = job.device, job.release
    if not catalog.matches(release, device):
        raise UpdateError("Vendor/model mismatch.")
    if not release.file_path or not os.path.isfile(release.file_path):
        raise UpdateError("Firmware file is missing from the repository.")
    actual = catalog.sha256_file(release.file_path)
    if actual != release.sha256:
        raise UpdateError("Stored firmware file hash changed since import. Refusing to flash.")
    log("Package hash re-verified against repository record.")
    driver = driver_for(device)
    if not driver.update_tool_configured():
        raise UpdateError(
            f"No update tool configured for {device.vendor}. Set FWMGR_UPDATE_CMD_"
            f"{device.vendor.upper()} (see docs).")
    info = driver.probe()
    if not info.reachable:
        raise UpdateError(f"Device not reachable: {info.detail}")
    log(f"Device reachable ({info.detail}).")
    if info.run_state == "run":
        raise UpdateError("PLC is in RUN. Put it in STOP in a controlled way before updating.")
    if info.firmware_version and not same_version(info.firmware_version, job.from_version):
        raise UpdateError(f"Device reports firmware {info.firmware_version}, expected "
                          f"{job.from_version}. Refresh the device and re-request.")
    if not is_newer(release.version, info.firmware_version or job.from_version):
        raise UpdateError("Target version is not newer than the installed version.")


def _execute(db: Session, job: UpdateJob, log) -> None:
    device, release = job.device, job.release
    preflight(db, job, log)
    log(f"Starting update to {release.version}")
    driver_for(device).apply_update(release.file_path, release.version, log)
    log("Flash command finished. Verifying...")
    after = driver_for(device)
    new_version = after.probe().firmware_version if after.reads_version else ""
    if new_version and same_version(new_version, release.version):
        device.firmware_version = new_version
        device.version_source = "device"
        job.status = "completed"
        log(f"Verified: device reports {new_version}.")
    elif new_version:
        job.status = "failed"
        log(f"Device reports {new_version}, expected {release.version}. "
            "Check the device before using it.")
    else:
        job.status = "needs_verification"
        log("Tool finished but this driver cannot read the version back. "
            "Verify on the device and confirm the new version.")


def run_job(job_id: int) -> None:
    """Execute an approved job. Safe to call from a background thread."""
    with SessionLocal() as db:
        job = db.get(UpdateJob, job_id)
        if job is None or job.status != "approved":
            return
        lock = _lock_for(job.device_id)
        if not lock.acquire(blocking=False):
            return
        try:
            job.status = "running"
            db.commit()
            log = lambda m: _log(job, db, m)  # noqa: E731
            try:
                _execute(db, job, log)
            except (UpdateError, DriverError) as exc:
                job.status = "failed"
                log(f"FAILED: {exc}")
            except Exception as exc:  # unexpected: record, never leave job in 'running'
                job.status = "failed"
                log(f"FAILED unexpectedly: {type(exc).__name__}: {exc}")
            db.commit()
            audit(db, "system", "update.result", f"job={job.id} status={job.status}")
        finally:
            lock.release()


def start_job(job_id: int) -> None:
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
