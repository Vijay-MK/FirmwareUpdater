"""Firmware catalog: manual import, JSON feed sync, matching releases to devices, notifications."""
import fnmatch
import hashlib
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai
from ..config import VENDORS, settings
from ..models import Device, FeedSource, FirmwareRelease, Notification, utcnow
from ..security import decrypt_text
from ..versioning import is_newer, parse_version

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_firmware_file(release: FirmwareRelease, stream, filename: str) -> None:
    """Save an uploaded firmware package into the repository, enforcing the size limit."""
    safe = SAFE_NAME.sub("_", Path(filename or "firmware.bin").name) or "firmware.bin"
    target_dir = settings.firmware_dir / release.vendor.lower() / SAFE_NAME.sub("_", release.version)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    digest = hashlib.sha256()
    with open(target, "wb") as out:
        while chunk := stream.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                out.close()
                target.unlink(missing_ok=True)
                raise ValueError(f"File exceeds the {settings.max_upload_mb} MB limit")
            digest.update(chunk)
            out.write(chunk)
    release.file_path = str(target)
    release.sha256 = digest.hexdigest()
    check_hash(release)


def check_hash(release: FirmwareRelease) -> None:
    expected = (release.expected_sha256 or "").strip().lower()
    release.hash_verified = bool(expected and release.sha256 and expected == release.sha256.lower())


def matches(release: FirmwareRelease, device: Device) -> bool:
    if release.vendor != device.vendor:
        return False
    pattern = (release.model_pattern or "*").strip() or "*"
    return fnmatch.fnmatch((device.model or "").lower(), pattern.lower())


def available_updates(db: Session, device: Device) -> list[FirmwareRelease]:
    """Newer releases that match this device, newest first."""
    if not device.firmware_version:
        return []
    releases = db.scalars(select(FirmwareRelease).where(FirmwareRelease.vendor == device.vendor)).all()
    found = [r for r in releases if matches(r, device) and is_newer(r.version, device.firmware_version)]
    found.sort(key=lambda r: parse_version(r.version), reverse=True)
    return found


def add_release(db: Session, **fields) -> tuple[FirmwareRelease, bool]:
    """Create a release unless it already exists. Returns (release, created)."""
    existing = db.scalar(select(FirmwareRelease).where(
        FirmwareRelease.vendor == fields["vendor"],
        FirmwareRelease.model_pattern == fields.get("model_pattern", "*"),
        FirmwareRelease.version == fields["version"]))
    if existing:
        return existing, False
    release = FirmwareRelease(**fields)
    check_hash(release)
    ai.apply_analysis(release)
    db.add(release)
    db.commit()
    return release, True


def notify_for_release(db: Session, release: FirmwareRelease) -> list[Notification]:
    created = []
    for device in db.scalars(select(Device).where(Device.vendor == release.vendor)).all():
        if not (matches(release, device) and device.firmware_version
                and is_newer(release.version, device.firmware_version)):
            continue
        if db.scalar(select(Notification).where(Notification.device_id == device.id,
                                                Notification.release_id == release.id)):
            continue
        urgency = f" (urgency: {release.ai_urgency})" if release.ai_urgency else ""
        note = Notification(
            device_id=device.id, release_id=release.id,
            message=f"{device.name}: firmware {release.version} available "
                    f"(installed {device.firmware_version}){urgency}")
        db.add(note)
        created.append(note)
    db.commit()
    return created


def notify_all(db: Session) -> int:
    count = 0
    for release in db.scalars(select(FirmwareRelease)).all():
        count += len(notify_for_release(db, release))
    return count


def send_email(subject: str, body: str) -> None:
    if not (settings.smtp_host and settings.smtp_to):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_from, settings.smtp_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(msg)
    except OSError:
        pass  # email is best-effort; in-app notifications are the source of truth


# ---- feeds ---------------------------------------------------------------------------
# Feed format (JSON): {"releases": [{"version": "4.2.1", "model_pattern": "S7-1500*",
#   "release_date": "2025-01-31", "release_notes": "...", "source_url": "...",
#   "download_url": "...", "sha256": "<hex>"}]}
# Vendors do not offer a common public API, so a feed is typically a mirror/adapter that the
# company controls (or a vendor-provided JSON feed where one exists).

def _validate_feed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Feed URL must be http(s)")


def sync_feed(db: Session, feed: FeedSource) -> tuple[int, str]:
    """Fetch one feed; returns (new_release_count, status message)."""
    new = 0
    try:
        _validate_feed_url(feed.url)
        headers = {}
        if feed.auth_token_enc:
            headers["Authorization"] = f"Bearer {decrypt_text(feed.auth_token_enc)}"
        resp = httpx.get(feed.url, headers=headers, timeout=30, follow_redirects=False)
        resp.raise_for_status()
        items = resp.json().get("releases", [])
        for item in items:
            version = str(item.get("version", "")).strip()
            if not version or not parse_version(version):
                continue
            release, created = add_release(
                db, vendor=feed.vendor, version=version[:64],
                model_pattern=str(item.get("model_pattern") or "*")[:100],
                release_date=str(item.get("release_date", ""))[:32], source=feed.name[:100],
                source_url=str(item.get("source_url", ""))[:500],
                download_url=str(item.get("download_url", ""))[:500],
                expected_sha256=str(item.get("sha256", "")).strip().lower()[:64],
                release_notes=str(item.get("release_notes", ""))[:20000])
            if created:
                new += 1
            notify_for_release(db, release)
        status = f"OK: {len(items)} releases in feed, {new} new"
    except Exception as exc:
        status = f"Error: {type(exc).__name__}: {str(exc)[:200]}"
    feed.last_checked = utcnow()
    feed.last_status = status
    db.commit()
    return new, status


def sync_all_feeds(db: Session) -> int:
    total = 0
    for feed in db.scalars(select(FeedSource).where(FeedSource.enabled.is_(True))).all():
        new, _ = sync_feed(db, feed)
        total += new
    if total:
        send_email("New PLC firmware available", f"{total} new firmware release(s) were found. "
                   "Open the Firmware Manager to review them.")
    return total


def download_release(db: Session, release: FirmwareRelease) -> None:
    """Download the package from download_url into the repository.

    Requires the vendor-published SHA-256: a download without a matching hash is rejected
    and deleted, so unverified files can never reach a PLC.
    """
    if not release.download_url:
        raise ValueError("Release has no download URL")
    if not release.expected_sha256:
        raise ValueError("No published SHA-256 for this release; upload the file manually instead")
    _validate_feed_url(release.download_url)
    name = Path(urlparse(release.download_url).path).name or "firmware.bin"
    target_dir = settings.firmware_dir / release.vendor.lower() / SAFE_NAME.sub("_", release.version)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / SAFE_NAME.sub("_", name)
    limit = settings.max_upload_mb * 1024 * 1024
    digest, written = hashlib.sha256(), 0
    try:
        with httpx.stream("GET", release.download_url, timeout=120, follow_redirects=False) as resp:
            resp.raise_for_status()
            with open(target, "wb") as out:
                for chunk in resp.iter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise ValueError(f"Download exceeds the {settings.max_upload_mb} MB limit")
                    digest.update(chunk)
                    out.write(chunk)
        if digest.hexdigest() != release.expected_sha256.lower():
            raise ValueError("SHA-256 mismatch: downloaded file does not match the published hash")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    release.file_path = str(target)
    release.sha256 = digest.hexdigest()
    check_hash(release)
    db.commit()
