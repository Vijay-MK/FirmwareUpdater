import json

import pytest
from sqlalchemy import select

from app import ai
from app.config import VENDORS, settings
from app.database import SessionLocal
from app.drivers import simulator
from app.models import AuditLog, Device, DiagAdvice, DiagSnapshot
from app.services import advisor
from tests import simulate_devices as sim
from tests.sim_scenarios import SCENARIOS


@pytest.fixture()
def fleet(_app_client):
    with SessionLocal() as db:
        sim.seed(db, log=lambda *_: None)
    yield
    with SessionLocal() as db:
        sim.clear(db)


def device_id(sc):
    with SessionLocal() as db:
        return db.scalar(select(Device.id).where(Device.ip_address == sc.ip, Device.port == sc.port))


def by_name(name):
    return next(sc for sc in SCENARIOS if sc.name == name)


def issues_for(sc):
    state = sim.sim_state(sc, sim.utcnow())
    return advisor.advise(sc.vendor, {"events": state["events"]}, reachable=sc.online,
                          health=sc.health, run_state=sc.run_state)


def test_scenarios_are_consistent():
    assert {sc.vendor for sc in SCENARIOS} == set(VENDORS)           # every company is simulated
    assert len({(sc.ip, sc.port) for sc in SCENARIOS}) == len(SCENARIOS)
    assert len({sc.name for sc in SCENARIOS}) == len(SCENARIOS)
    assert {sc.health for sc in SCENARIOS} >= {"ok", "warning", "fault", "unknown"}
    assert any(not sc.online for sc in SCENARIOS) and any(sc.run_state == "stop" for sc in SCENARIOS)
    for sc in SCENARIOS:
        assert sc.serial.startswith("SIM-")                           # never looks like a real serial
        assert all(ev[1] in ("info", "warning", "error") for ev in sc.events)


def test_seed_updates_database(fleet):
    with SessionLocal() as db:
        for sc in SCENARIOS:
            dev = db.scalar(select(Device).where(Device.ip_address == sc.ip, Device.port == sc.port))
            assert dev.vendor == sc.vendor and dev.firmware_version == sc.firmware
            assert dev.notes.startswith(sim.MARKER)
            assert dev.status == ("online" if sc.online else "offline")
            assert dev.last_seen is not None
            snaps = db.scalars(select(DiagSnapshot).where(DiagSnapshot.device_id == dev.id)
                               .order_by(DiagSnapshot.id)).all()
            assert len(snaps) == len(sc.timeline) + 1
            assert [s.collected_at for s in snaps] == sorted(s.collected_at for s in snaps)
            last = snaps[-1]
            assert (last.health, last.run_state, last.reachable) == (
                (sc.health, sc.run_state, True) if sc.online else ("unknown", "unknown", False))
            if sc.online:
                assert len(last.parsed["events"]) == len(sc.events)


def test_seed_twice_does_not_duplicate(fleet):
    with SessionLocal() as db:
        sim.seed(db, log=lambda *_: None)
        found = db.scalars(select(Device).where(Device.notes.like(f"{sim.MARKER}%"))).all()
        assert len(found) == len(SCENARIOS)


def test_real_device_is_never_overwritten_or_removed(_app_client):
    sc = SCENARIOS[0]
    with SessionLocal() as db:
        db.add(Device(name="real-plc", vendor="Siemens", driver="siemens", ip_address=sc.ip,
                      port=sc.port, notes="the real one"))
        db.commit()
        messages = []
        sim.seed(db, [sc], log=messages.append)
        assert messages and "real device" in messages[0]
        assert db.scalar(select(Device).where(Device.ip_address == sc.ip)).name == "real-plc"
        assert sim.clear(db, [sc]) == 0
        real = db.scalar(select(Device).where(Device.ip_address == sc.ip))
        assert real.name == "real-plc"
        db.delete(real)
        db.commit()


def test_state_file_is_shared_with_the_running_app(fleet):
    sc = by_name("S7-1500 Filling Line 1")
    simulator.reset()                       # as if the web server process had just started
    assert simulator.get_state(sc.ip, sc.port)["health"] == "fault"
    assert f"{sc.ip}:{sc.port}" in json.loads(simulator.state_file().read_text())


def test_run_now_in_ui_matches_the_scenario(admin, fleet):
    dev = device_id(by_name("FX5U Conveyor"))
    simulator.reset()
    resp = admin.post(f"/devices/{dev}/diagnostics/run", page=f"/devices/{dev}/diagnostics")
    assert resp.status_code == 303
    page = admin.get(f"/devices/{dev}/diagnostics").text
    assert "Watchdog timer error" in page and "h-fault" in page and "s-stop" in page


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda sc: sc.name)
def test_help_for_every_scenario(sc):
    advice = issues_for(sc)
    has_problem = any(ev[1] in ("warning", "error") for ev in sc.events)
    if sc.online and not has_problem and sc.health == "ok":
        assert not advice["issues"] and "No warnings or errors" in advice["overall"]
        return
    assert advice["issues"], sc.name
    for issue in advice["issues"]:
        assert issue["causes"] and issue["steps"]
    keys = {i["key"] for i in advice["issues"]}
    if sc.expect_unrecognised:
        assert "unrecognised" in keys
    elif sc.online:
        assert "unrecognised" not in keys, f"{sc.name}: events without help: {keys}"
    else:
        assert keys == {"communication"}                              # offline device
    worst = "error" if (sc.health == "fault" or not sc.online) else "warning"
    assert advice["issues"][0]["severity"] == worst


def test_vendor_specific_help_wins():
    keys = [i["key"] for i in issues_for(by_name("S7-1500 Filling Line 1"))["issues"]]
    assert "siemens_diag_buffer" in keys
    assert "delta_dvp_ah" in {i["key"] for i in issues_for(by_name("AS332T Furnace"))["issues"]}
    ev = {"code": "", "message": "CPU changed to STOP due to error OB86", "source": ""}
    assert advisor.match_rule("Siemens", ev).key == "siemens_diag_buffer"
    other = advisor.match_rule("WAGO", ev)
    assert other is None or not other.vendors   # a Siemens-only rule must not fire for WAGO


def test_security_event_says_to_escalate():
    advice = issues_for(by_name("S7-1500 Utilities DMZ"))
    sec = next(i for i in advice["issues"] if i["key"] == "security_access")
    assert sec["escalate"] and sec["count"] == 3

