from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import DEFAULT_PORTS, VENDORS
from ..deps import check_allowed_address, current_user, flash, get_db, render, require, verify_csrf
from ..drivers import DRIVERS, DriverError, driver_for
from ..models import Device, UpdateJob, User, utcnow
from ..security import encrypt_json
from ..services import catalog

router = APIRouter(prefix="/devices")


def _driver_choices() -> dict[str, str]:
    return {k: v.notes for k, v in DRIVERS.items()}


def _get(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    return device


def _validate(vendor: str, driver: str, ip: str, port: int, name: str) -> str | None:
    if vendor not in VENDORS:
        return "Unknown vendor."
    if driver != "simulator" and driver != vendor.lower():
        return "Driver does not match the vendor."
    if not name.strip():
        return "Name is required."
    if not 1 <= port <= 65535:
        return "Port must be between 1 and 65535."
    return check_allowed_address(ip)


def refresh_device(db: Session, device: Device) -> str:
    """Probe the device and store what it reports. Returns a human-readable message."""
    try:
        info = driver_for(device).probe()
    except DriverError as exc:
        return str(exc)
    device.status = "online" if info.reachable else "offline"
    if info.reachable:
        device.last_seen = utcnow()
        if info.firmware_version:
            device.firmware_version = info.firmware_version
            device.version_source = "device"
        if info.serial_number:
            device.serial_number = info.serial_number
        if info.model and not device.model:
            device.model = info.model
    db.commit()
    if info.reachable:
        for release in catalog.available_updates(db, device):
            catalog.notify_for_release(db, release)
    return info.detail


@router.get("")
def list_devices(request: Request, q: str = "", vendor: str = "", user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    devices = db.scalars(select(Device).order_by(Device.group_name, Device.name)).all()
    if vendor:
        devices = [d for d in devices if d.vendor == vendor]
    if q:
        ql = q.lower()
        devices = [d for d in devices if ql in f"{d.name} {d.ip_address} {d.model} {d.group_name}".lower()]
    updates = {d.id: catalog.available_updates(db, d) for d in devices}
    return render(request, "devices.html", user, db, devices=devices, updates=updates, q=q,
                  vendor=vendor, vendors=VENDORS)


@router.get("/new")
def new_form(request: Request, user: User = Depends(require("engineer")), db: Session = Depends(get_db)):
    return render(request, "device_form.html", user, db, device=None, vendors=VENDORS,
                  drivers=_driver_choices(), ports=DEFAULT_PORTS)


@router.post("/new", dependencies=[Depends(verify_csrf)])
def create(request: Request, name: str = Form(...), vendor: str = Form(...), driver: str = Form(...),
           ip_address: str = Form(...), port: int = Form(...), model: str = Form(""),
           firmware_version: str = Form(""), group_name: str = Form(""), location: str = Form(""),
           notes: str = Form(""), username: str = Form(""), password: str = Form(""),
           user: User = Depends(require("engineer")), db: Session = Depends(get_db)):
    error = _validate(vendor, driver, ip_address, port, name)
    if error:
        flash(request, error, "error")
        return RedirectResponse("/devices/new", status_code=303)
    device = Device(name=name.strip()[:100], vendor=vendor, driver=driver, ip_address=ip_address.strip(),
                    port=port, model=model.strip()[:100], firmware_version=firmware_version.strip()[:64],
                    group_name=group_name.strip()[:100], location=location.strip()[:200],
                    notes=notes[:2000])
    if username or password:
        device.credentials_enc = encrypt_json({"username": username, "password": password})
    db.add(device)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "A device with this IP address and port already exists.", "error")
        return RedirectResponse("/devices/new", status_code=303)
    audit(db, user.username, "device.create", f"{device.name} {device.ip_address}:{device.port}")
    refresh_device(db, device)
    flash(request, f"Device '{device.name}' added.")
    return RedirectResponse(f"/devices/{device.id}", status_code=303)


@router.post("/refresh-all", dependencies=[Depends(verify_csrf)])
def refresh_all(request: Request, user: User = Depends(require("engineer")),
                db: Session = Depends(get_db)):
    devices = db.scalars(select(Device)).all()
    for d in devices:
        refresh_device(db, d)
    flash(request, f"Refreshed {len(devices)} device(s).")
    return RedirectResponse("/devices", status_code=303)


@router.get("/{device_id}")
def detail(request: Request, device_id: int, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    device = _get(db, device_id)
    jobs = db.scalars(select(UpdateJob).where(UpdateJob.device_id == device.id)
                      .order_by(UpdateJob.id.desc())).all()
    driver_cls = DRIVERS.get(device.driver)
    return render(request, "device_detail.html", user, db, device=device, jobs=jobs,
                  updates=catalog.available_updates(db, device),
                  driver_notes=driver_cls.notes if driver_cls else "",
                  can_read_version=bool(driver_cls and driver_cls.reads_version))


@router.post("/{device_id}/refresh", dependencies=[Depends(verify_csrf)])
def refresh(request: Request, device_id: int, user: User = Depends(require("engineer")),
            db: Session = Depends(get_db)):
    device = _get(db, device_id)
    message = refresh_device(db, device)
    flash(request, f"{device.status.capitalize()}: {message}",
          "ok" if device.status == "online" else "error")
    return RedirectResponse(f"/devices/{device_id}", status_code=303)


@router.get("/{device_id}/edit")
def edit_form(request: Request, device_id: int, user: User = Depends(require("engineer")),
              db: Session = Depends(get_db)):
    return render(request, "device_form.html", user, db, device=_get(db, device_id), vendors=VENDORS,
                  drivers=_driver_choices(), ports=DEFAULT_PORTS)


@router.post("/{device_id}/edit", dependencies=[Depends(verify_csrf)])
def edit(request: Request, device_id: int, name: str = Form(...), ip_address: str = Form(...),
         port: int = Form(...), model: str = Form(""), firmware_version: str = Form(""),
         group_name: str = Form(""), location: str = Form(""), notes: str = Form(""),
         username: str = Form(""), password: str = Form(""),
         user: User = Depends(require("engineer")), db: Session = Depends(get_db)):
    device = _get(db, device_id)
    if any(j.active for j in device.jobs):
        flash(request, "Cannot edit a device while it has an active update job.", "error")
        return RedirectResponse(f"/devices/{device_id}", status_code=303)
    error = _validate(device.vendor, device.driver, ip_address, port, name)
    if error:
        flash(request, error, "error")
        return RedirectResponse(f"/devices/{device_id}/edit", status_code=303)
    device.name, device.ip_address, device.port = name.strip()[:100], ip_address.strip(), port
    device.model, device.group_name = model.strip()[:100], group_name.strip()[:100]
    device.location, device.notes = location.strip()[:200], notes[:2000]
    if firmware_version.strip() != device.firmware_version:
        device.firmware_version = firmware_version.strip()[:64]
        device.version_source = "manual"
    if username or password:  # leave blank to keep existing credentials
        device.credentials_enc = encrypt_json({"username": username, "password": password})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Another device already uses this IP address and port.", "error")
        return RedirectResponse(f"/devices/{device_id}/edit", status_code=303)
    audit(db, user.username, "device.edit", f"{device.name} {device.ip_address}:{device.port}")
    flash(request, "Device updated.")
    return RedirectResponse(f"/devices/{device_id}", status_code=303)


@router.post("/{device_id}/delete", dependencies=[Depends(verify_csrf)])
def delete(request: Request, device_id: int, user: User = Depends(require("admin")),
           db: Session = Depends(get_db)):
    device = _get(db, device_id)
    if any(j.active for j in device.jobs):
        flash(request, "Cannot delete a device while it has an active update job.", "error")
        return RedirectResponse(f"/devices/{device_id}", status_code=303)
    name = device.name
    db.delete(device)
    db.commit()
    audit(db, user.username, "device.delete", name)
    flash(request, f"Device '{name}' deleted.")
    return RedirectResponse("/devices", status_code=303)

