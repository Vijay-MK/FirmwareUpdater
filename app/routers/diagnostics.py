import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import ai
from ..audit import audit
from ..config import settings
from ..deps import current_user, flash, get_db, render, require, verify_csrf
from ..drivers import DRIVERS
from ..drivers.diag import SEVERITIES
from ..models import Device, DiagAdvice, User, utcnow
from ..services import advisor, diagnostics

router = APIRouter(prefix="/devices")


def _get(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    return device


@router.get("/{device_id}/diagnostics")
def view(request: Request, device_id: int, snapshot: int = 0, severity: str = "",
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    device = _get(db, device_id)
    snaps = diagnostics.history(db, device.id)
    current = diagnostics.get_snapshot(db, device.id, snapshot) if snapshot else (snaps[0] if snaps else None)
    if snapshot and current is None:
        raise HTTPException(404, "Snapshot not found")
    data = current.parsed if current else {"facts": {}, "events": [], "notes": [], "events_available": False}
    events = data["events"]
    counts = {s: sum(1 for e in events if e.get("severity") == s) for s in SEVERITIES}
    if severity in SEVERITIES:
        events = [e for e in events if e.get("severity") == severity]
    driver_cls = DRIVERS.get(device.driver)
    help_ = (advisor.advise(device.vendor, data, reachable=current.reachable, health=current.health,
                            run_state=current.run_state) if current else None)
    ai_advice = current.advice.parsed if current and current.advice else None
    return render(request, "diagnostics.html", user, db, device=device, snap=current, snaps=snaps,
                  data=data, events=events, counts=counts, severity=severity, help=help_,
                  ai_advice=ai_advice, ai_model=current.advice.model if ai_advice else "",
                  ai_available=settings.llm_enabled and settings.llm_send_fleet_data,
                  is_latest=bool(current and snaps and current.id == snaps[0].id),
                  driver_notes=driver_cls.notes if driver_cls else "")


@router.post("/{device_id}/diagnostics/{snapshot_id}/explain", dependencies=[Depends(verify_csrf)])
def explain(request: Request, device_id: int, snapshot_id: int, user: User = Depends(require("engineer")),
            db: Session = Depends(get_db)):
    """Ask the configured LLM to explain one snapshot. Only diagnostic text is sent, never device identity."""
    device = _get(db, device_id)
    snap = diagnostics.get_snapshot(db, device.id, snapshot_id)
    if snap is None:
        raise HTTPException(404, "Snapshot not found")
    back = RedirectResponse(f"/devices/{device_id}/diagnostics?snapshot={snap.id}", status_code=303)
    data = snap.parsed
    help_ = advisor.advise(device.vendor, data, reachable=snap.reachable, health=snap.health,
                           run_state=snap.run_state)
    context = advisor.build_llm_context(device.vendor, device.model, device.firmware_version, data,
                                        reachable=snap.reachable, health=snap.health,
                                        run_state=snap.run_state, issues=help_["issues"])
    try:
        result = ai.explain_diagnostics(context)
    except ai.AdviceError as exc:
        flash(request, str(exc), "error")
        return back
    if snap.advice is None:
        snap.advice = DiagAdvice()
    snap.advice.content = json.dumps(result)
    snap.advice.model = settings.llm_model[:100]
    snap.advice.created_by = user.username
    snap.advice.created_at = utcnow()
    db.commit()
    audit(db, user.username, "device.diagnostics.explain", f"{device.name} snapshot={snap.id} model={settings.llm_model}")
    flash(request, "AI explanation added.")
    return back


@router.post("/{device_id}/diagnostics/run", dependencies=[Depends(verify_csrf)])
def run(request: Request, device_id: int, user: User = Depends(require("engineer")),
        db: Session = Depends(get_db)):
    device = _get(db, device_id)
    if any(j.status == "running" for j in device.jobs):
        flash(request, "A firmware update is running on this device; diagnostics are paused.", "error")
        return RedirectResponse(f"/devices/{device_id}/diagnostics", status_code=303)
    snap = diagnostics.run_diagnostics(db, device, user.username)
    audit(db, user.username, "device.diagnostics", f"{device.name} reachable={snap.reachable}")
    if snap.reachable:
        flash(request, "Diagnostics collected.")
    else:
        flash(request, "Device is not reachable; see details below.", "error")
    return RedirectResponse(f"/devices/{device_id}/diagnostics", status_code=303)
