"""Simulate a fleet of PLCs (all vendors, many states) and store it in the app's database.

    python -m tests.simulate_devices seed     # create/refresh the simulated devices and history
    python -m tests.simulate_devices list     # show what is simulated and the current status
    python -m tests.simulate_devices clear    # remove only the simulated devices

What it does
  * creates one device per scenario (driver "simulator", notes start with "[SIMULATED]");
  * writes each device's live state to <data dir>/simulator_state.json, which the running web
    server picks up, so "Run diagnostics now" in the UI returns the same picture;
  * stores a diagnostics history (older snapshots with backdated times, then a fresh one) and
    updates the device rows (status, last seen, firmware, serial).

Safety: it only replaces devices that carry the [SIMULATED] marker. A real device that uses the
same IP address and port is left alone and reported. It uses the same database as the app
(FWMGR_DATA_DIR / FWMGR_DATABASE_URL); the web server can keep running while it works.
The data is invented. Event codes imitate each vendor's style, they are not authoritative.
"""
import argparse
import json
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, init_db
from app.drivers import simulator
from app.models import Device, DiagSnapshot, utcnow
from app.services import diagnostics
from tests.sim_scenarios import SCENARIOS, Scenario

MARKER = "[SIMULATED]"
SCRIPT_USER = "simulate_devices"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _events(sc: Scenario, now, newest_allowed=None) -> list[dict]:
    """Scenario events as buffer entries, newest first. Optionally drop events after a time."""
    items = sorted(sc.events, key=lambda e: e[0])
    return [{"timestamp": (now - timedelta(minutes=m)).strftime(TS_FORMAT), "severity": sev,
             "code": code, "message": msg, "source": src}
            for m, sev, code, msg, src in items
            if newest_allowed is None or now - timedelta(minutes=m) <= newest_allowed]


def sim_state(sc: Scenario, now) -> dict:
    """The live state the simulator driver reports for this scenario."""
    return {"version": sc.firmware, "run_state": sc.run_state, "online": sc.online, "health": sc.health,
            "model": sc.model, "serial": sc.serial, "hardware": sc.hardware, "latency_ms": sc.latency_ms,
            "facts": dict(sc.facts), "events": _events(sc, now), "notes": list(sc.notes)}


def _history_payload(sc: Scenario, now, hours_ago: float, reachable: bool) -> dict:
    facts = {"Address": f"{sc.ip}:{sc.port}",
             "Connection check": "Simulated device" if reachable else "Simulated device offline"}
    if not reachable:
        return {"facts": facts, "events_available": False, "notes": [], "events": []}
    facts.update({"Model": sc.model, "Firmware version": sc.firmware, "Hardware revision": sc.hardware,
                  "Serial number": sc.serial, **sc.facts})
    # an old snapshot cannot contain events that happened after it was taken
    events = _events(sc, now, newest_allowed=now - timedelta(hours=hours_ago))
    return {"facts": facts, "events_available": True, "notes": [], "events": events}


def _existing(db: Session, sc: Scenario) -> Device | None:
    return db.scalar(select(Device).where(Device.ip_address == sc.ip, Device.port == sc.port))


def _is_simulated(device: Device) -> bool:
    return device.driver == "simulator" and (device.notes or "").startswith(MARKER)


def _add_history(db: Session, device: Device, sc: Scenario, now) -> None:
    for i, (hours_ago, health, run_state, reachable) in enumerate(sc.timeline):  # oldest first
        db.add(DiagSnapshot(
            device_id=device.id, collected_at=now - timedelta(hours=hours_ago),
            collected_by=SCRIPT_USER, reachable=reachable, run_state=run_state, health=health,
            latency_ms=round(sc.latency_ms * (1 + 0.05 * (i % 3)), 1) if reachable else None,
            data=json.dumps(_history_payload(sc, now, hours_ago, reachable))))
    db.commit()


def seed(db: Session, scenarios: list[Scenario] | None = None, log=print) -> list[Device]:
    """Create or refresh the simulated devices. Returns the devices that were written."""
    scenarios = SCENARIOS if scenarios is None else scenarios
    now = utcnow()
    written: list[Device] = []
    states: dict[tuple[str, int], dict] = {}
    for sc in scenarios:
        old = _existing(db, sc)
        if old is not None and not _is_simulated(old):
            log(f"SKIPPED {sc.name}: {sc.ip}:{sc.port} is used by the real device '{old.name}'")
            continue
        if old is not None:
            db.delete(old)          # cascades to snapshots; a refresh starts from a clean history
            db.commit()
        device = Device(name=sc.name, vendor=sc.vendor, model=sc.model, driver="simulator",
                        ip_address=sc.ip, port=sc.port, group_name=sc.group, location=sc.location,
                        notes=f"{MARKER} {sc.story}", firmware_version=sc.firmware,
                        hardware_revision=sc.hardware, serial_number=sc.serial, status="unknown",
                        version_source="manual")
        db.add(device)
        db.commit()
        _add_history(db, device, sc, now)
        state = sim_state(sc, now)
        states[(sc.ip, sc.port)] = state
        simulator.replace_state(sc.ip, sc.port, state)
        diagnostics.run_diagnostics(db, device, SCRIPT_USER)   # newest snapshot, status, last_seen
        if not sc.online:
            seen = [h for h, _, _, reach in sc.timeline if reach]
            device.last_seen = now - timedelta(hours=min(seen) if seen else 24)
            db.commit()
        written.append(device)
    simulator.write_states(states)
    return written


def clear(db: Session, scenarios: list[Scenario] | None = None) -> int:
    """Remove only devices that carry the [SIMULATED] marker."""
    removed = 0
    keys = []
    for sc in SCENARIOS if scenarios is None else scenarios:
        device = _existing(db, sc)
        if device is not None and _is_simulated(device):
            db.delete(device)
            removed += 1
            keys.append((sc.ip, sc.port))
    db.commit()
    simulator.remove_states(keys)
    return removed


def show(db: Session, scenarios: list[Scenario] | None = None, log=print) -> None:
    for sc in SCENARIOS if scenarios is None else scenarios:
        device = _existing(db, sc)
        if device is None:
            log(f"{sc.vendor:<11}{sc.name:<30}not in database")
            continue
        snap = db.scalar(select(DiagSnapshot).where(DiagSnapshot.device_id == device.id)
                         .order_by(DiagSnapshot.id.desc()))
        mark = "simulated" if _is_simulated(device) else "REAL DEVICE (not touched)"
        state = f"{snap.health}/{snap.run_state}" if snap else "no diagnostics"
        log(f"{sc.vendor:<11}{sc.name:<30}{device.status:<9}{state:<16}{mark}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate PLC devices and diagnostics in the app database.")
    parser.add_argument("action", choices=["seed", "list", "clear"])
    args = parser.parse_args(argv)
    init_db()
    print(f"Database: {settings.database_url}")
    with SessionLocal() as db:
        if args.action == "seed":
            devices = seed(db)
            print(f"Simulated {len(devices)} devices. State file: {simulator.state_file()}")
            show(db)
        elif args.action == "list":
            show(db)
        else:
            print(f"Removed {clear(db)} simulated devices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

