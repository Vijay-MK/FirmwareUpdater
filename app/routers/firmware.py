from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai
from ..audit import audit
from ..config import VENDORS
from ..deps import current_user, flash, get_db, render, require, verify_csrf
from ..models import FeedSource, FirmwareRelease, User
from ..security import encrypt_text
from ..services import catalog
from ..versioning import parse_version

router = APIRouter(prefix="/firmware")


def _get(db: Session, release_id: int) -> FirmwareRelease:
    release = db.get(FirmwareRelease, release_id)
    if release is None:
        raise HTTPException(404, "Release not found")
    return release


@router.get("")
def list_releases(request: Request, vendor: str = "", user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    releases = db.scalars(select(FirmwareRelease)).all()
    if vendor:
        releases = [r for r in releases if r.vendor == vendor]
    releases.sort(key=lambda r: (r.vendor, parse_version(r.version)), reverse=True)
    feeds = db.scalars(select(FeedSource).order_by(FeedSource.vendor, FeedSource.name)).all()
    return render(request, "firmware.html", user, db, releases=releases, feeds=feeds,
                  vendors=VENDORS, vendor=vendor)


@router.post("/add", dependencies=[Depends(verify_csrf)])
def add(request: Request, vendor: str = Form(...), version: str = Form(...),
        model_pattern: str = Form("*"), release_date: str = Form(""), release_notes: str = Form(""),
        source_url: str = Form(""), expected_sha256: str = Form(""),
        package: UploadFile | None = File(None),
        user: User = Depends(require("engineer")), db: Session = Depends(get_db)):
    if vendor not in VENDORS or not parse_version(version):
        flash(request, "Choose a vendor and enter a version containing numbers (e.g. 4.2.1).", "error")
        return RedirectResponse("/firmware", status_code=303)
    release, created = catalog.add_release(
        db, vendor=vendor, version=version.strip()[:64], model_pattern=(model_pattern.strip() or "*")[:100],
        release_date=release_date.strip()[:32], source=f"manual ({user.username})",
        source_url=source_url.strip()[:500], release_notes=release_notes[:20000],
        expected_sha256=expected_sha256.strip().lower()[:64])
    if not created:
        flash(request, "This release already exists.", "error")
        return RedirectResponse("/firmware", status_code=303)
    if package is not None and package.filename:
        try:
            catalog.store_firmware_file(release, package.file, package.filename)
            db.commit()
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/firmware", status_code=303)
    audit(db, user.username, "firmware.add", f"{vendor} {version} pattern={release.model_pattern}")
    count = len(catalog.notify_for_release(db, release))
    flash(request, f"Release added. {count} device(s) notified.")
    return RedirectResponse("/firmware", status_code=303)


@router.post("/{release_id}/upload", dependencies=[Depends(verify_csrf)])
def upload(request: Request, release_id: int, package: UploadFile = File(...),
           expected_sha256: str = Form(""), user: User = Depends(require("engineer")),
           db: Session = Depends(get_db)):
    release = _get(db, release_id)
    if expected_sha256.strip():
        release.expected_sha256 = expected_sha256.strip().lower()[:64]
    try:
        catalog.store_firmware_file(release, package.file, package.filename or "firmware.bin")
        db.commit()
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/firmware", status_code=303)
    audit(db, user.username, "firmware.upload",
          f"{release.vendor} {release.version} sha256={release.sha256} verified={release.hash_verified}")
    if release.hash_verified:
        flash(request, "Package stored. Hash matches the published SHA-256.")
    else:
        flash(request, "Package stored, but no published SHA-256 matched. "
                       "An approver must explicitly acknowledge this.", "error")
    return RedirectResponse("/firmware", status_code=303)


@router.post("/{release_id}/download", dependencies=[Depends(verify_csrf)])
def download(request: Request, release_id: int, user: User = Depends(require("engineer")),
             db: Session = Depends(get_db)):
    release = _get(db, release_id)
    try:
        catalog.download_release(db, release)
    except Exception as exc:
        flash(request, f"Download failed: {type(exc).__name__}: {str(exc)[:200]}", "error")
    else:
        audit(db, user.username, "firmware.download", f"{release.vendor} {release.version}")
        flash(request, "Downloaded and SHA-256 verified.")
    return RedirectResponse("/firmware", status_code=303)


@router.post("/{release_id}/reanalyze", dependencies=[Depends(verify_csrf)])
def reanalyze(request: Request, release_id: int, user: User = Depends(require("engineer")),
              db: Session = Depends(get_db)):
    release = _get(db, release_id)
    ai.apply_analysis(release)
    db.commit()
    flash(request, f"Analysis refreshed ({release.ai_source}).")
    return RedirectResponse("/firmware", status_code=303)


@router.post("/feeds/add", dependencies=[Depends(verify_csrf)])
def add_feed(request: Request, vendor: str = Form(...), name: str = Form(...), url: str = Form(...),
             token: str = Form(""), user: User = Depends(require("admin")),
             db: Session = Depends(get_db)):
    if vendor not in VENDORS or not url.startswith(("http://", "https://")):
        flash(request, "Choose a vendor and enter an http(s) URL.", "error")
        return RedirectResponse("/firmware", status_code=303)
    db.add(FeedSource(vendor=vendor, name=name.strip()[:100], url=url.strip()[:500],
                      auth_token_enc=encrypt_text(token) if token else None))
    db.commit()
    audit(db, user.username, "feed.add", f"{vendor} {name}")
    flash(request, "Feed added.")
    return RedirectResponse("/firmware", status_code=303)


@router.post("/feeds/{feed_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_feed(request: Request, feed_id: int, user: User = Depends(require("admin")),
                db: Session = Depends(get_db)):
    feed = db.get(FeedSource, feed_id)
    if feed:
        db.delete(feed)
        db.commit()
        audit(db, user.username, "feed.delete", feed.name)
    return RedirectResponse("/firmware", status_code=303)


@router.post("/check", dependencies=[Depends(verify_csrf)])
def check_now(request: Request, user: User = Depends(require("engineer")),
              db: Session = Depends(get_db)):
    new = catalog.sync_all_feeds(db)
    flash(request, f"Feed check complete: {new} new release(s).")
    return RedirectResponse("/firmware", status_code=303)

