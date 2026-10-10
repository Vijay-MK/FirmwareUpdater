from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import current_user, flash, get_db, render, require, verify_csrf
from ..models import Device, FirmwareRelease, UpdateJob, User
from ..services import updater

router = APIRouter(prefix="/updates")


def _job(db: Session, job_id: int) -> UpdateJob:
    job = db.get(UpdateJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("")
def list_jobs(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(select(UpdateJob).order_by(UpdateJob.id.desc()).limit(200)).all()
    return render(request, "updates.html", user, db, jobs=jobs)


@router.post("/request", dependencies=[Depends(verify_csrf)])
def request_update(request: Request, device_id: int = Form(...), release_id: int = Form(...),
                   backup_confirmed: bool = Form(False), window_confirmed: bool = Form(False),
                   comment: str = Form(""), user: User = Depends(require("engineer")),
                   db: Session = Depends(get_db)):
    device, release = db.get(Device, device_id), db.get(FirmwareRelease, release_id)
    if device is None or release is None:
        raise HTTPException(404, "Device or release not found")
    try:
        job = updater.request_update(db, user, device, release, backup_confirmed,
                                     window_confirmed, comment)
    except updater.UpdateError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(f"/devices/{device_id}", status_code=303)
    flash(request, "Update requested. An approver must approve it before it can run.")
    return RedirectResponse(f"/updates/{job.id}", status_code=303)


@router.get("/{job_id}")
def job_detail(request: Request, job_id: int, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    return render(request, "job_detail.html", user, db, job=_job(db, job_id))


@router.post("/{job_id}/approve", dependencies=[Depends(verify_csrf)])
def approve(request: Request, job_id: int, acknowledge_unverified: bool = Form(False),
            user: User = Depends(require("approver")), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    try:
        updater.approve(db, user, job, acknowledge_unverified)
    except updater.UpdateError as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, "Approved. Press 'Start update' when the device is ready.")
    return RedirectResponse(f"/updates/{job_id}", status_code=303)


@router.post("/{job_id}/start", dependencies=[Depends(verify_csrf)])
def start(request: Request, job_id: int, user: User = Depends(require("engineer")),
          db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if job.status != "approved":
        flash(request, "Only approved jobs can be started.", "error")
    else:
        audit(db, user.username, "update.start", f"job={job.id}")
        updater.start_job(job.id)
        flash(request, "Update started. This page shows progress; refresh to follow it.")
    return RedirectResponse(f"/updates/{job_id}", status_code=303)


@router.post("/{job_id}/reject", dependencies=[Depends(verify_csrf)])
def reject(request: Request, job_id: int, user: User = Depends(require("engineer")),
           db: Session = Depends(get_db)):
    try:
        updater.reject(db, user, _job(db, job_id))
        flash(request, "Job closed.")
    except updater.UpdateError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(f"/updates/{job_id}", status_code=303)


@router.post("/{job_id}/confirm", dependencies=[Depends(verify_csrf)])
def confirm(request: Request, job_id: int, version: str = Form(...),
            user: User = Depends(require("engineer")), db: Session = Depends(get_db)):
    try:
        updater.confirm_manual(db, user, _job(db, job_id), version.strip())
        flash(request, "Verification recorded.")
    except updater.UpdateError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(f"/updates/{job_id}", status_code=303)
