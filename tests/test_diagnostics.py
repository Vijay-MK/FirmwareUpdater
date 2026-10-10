from sqlalchemy import select

from app.database import SessionLocal
from app.drivers import simulator
from app.models import Device, DiagSnapshot, UpdateJob
from app.services import diagnostics
from tests.test_workflow import add_release, add_sim_device

EVENTS = [
    {"timestamp": "2025-01-31 10:15:02", "severity": "error", "code": "16#02:4E",
     "message": "Module <script>alert(1)</script> failed", "source": "Rack 0"},
    {"timestamp": "2025-01-31 10:10:00", "severity": "warning", "code": "W1", "message": "Battery low"},
    {"timestamp": "2025-01-31 10:00:00", "severity": "info", "code": "I1", "message": "Mode change to RUN"},
]


def run_diag(s, dev_id):
    return s.post(f"/devices/{dev_id}/diagnostics/run", page=f"/devices/{dev_id}/diagnostics")


def latest(dev_id):
    with SessionLocal() as db:
        return db.scalar(select(DiagSnapshot).where(DiagSnapshot.device_id == dev_id)
                         .order_by(DiagSnapshot.id.desc()))


def test_page_without_snapshots(admin):
    dev = add_sim_device(admin, name="diag-empty", ip="10.8.0.1")
    page = admin.get(f"/devices/{dev}/diagnostics")
    assert page.status_code == 200 and "No diagnostics have been collected" in page.text
    assert "/diagnostics" in admin.get(f"/devices/{dev}").text  # linked from the device page


def test_diagnostics_shows_state_and_buffer(admin):
    dev = add_sim_device(admin, name="diag-ok", ip="10.8.0.2")
    simulator.set_state("10.8.0.2", 502, version="1.0.0", run_state="stop", online=True,
                        health="warning", events=EVENTS)
    assert run_diag(admin, dev).status_code == 303
    snap = latest(dev)
    assert snap.reachable and snap.run_state == "stop" and snap.health == "warning"
    assert snap.latency_ms is not None
    page = admin.get(f"/devices/{dev}/diagnostics").text
    assert "Battery low" in page and "16#02:4E" in page and "Rack 0" in page
    assert "errors (1)" in page and "warnings (1)" in page
    # device-supplied text must be escaped, never rendered as markup
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_severity_filter(admin):
    dev = add_sim_device(admin, name="diag-filter", ip="10.8.0.3")
    simulator.set_state("10.8.0.3", 502, version="1.0.0", online=True, events=EVENTS)
    run_diag(admin, dev)
    def buffer_table(query: str) -> str:
        # the help section below the table always lists every issue, so only look at the buffer
        text = admin.get(f"/devices/{dev}/diagnostics?{query}").text
        return text.split("<h2>Diagnostic buffer</h2>")[1].split("<h2>Help")[0]

    errors = buffer_table("severity=error")
    assert "16#02:4E" in errors and "Battery low" not in errors
    assert "Battery low" in buffer_table("severity=warning")


def test_offline_device_is_recorded(admin):
    dev = add_sim_device(admin, name="diag-off", ip="10.8.0.4")
    simulator.set_state("10.8.0.4", 502, version="1.0.0", online=False)
    run_diag(admin, dev)
    assert not latest(dev).reachable
    with SessionLocal() as db:
        assert db.get(Device, dev).status == "offline"
    assert "not reachable" in admin.get(f"/devices/{dev}/diagnostics").text


def test_viewer_can_read_but_not_run(admin, make_user):
    dev = add_sim_device(admin, name="diag-rbac", ip="10.8.0.5")
    run_diag(admin, dev)
    viewer = make_user("diagviewer", "viewer")
    page = viewer.get(f"/devices/{dev}/diagnostics")
    assert page.status_code == 200 and "Run diagnostics now" not in page.text
    assert run_diag(viewer, dev).status_code == 403


def test_run_requires_csrf(admin):
    dev = add_sim_device(admin, name="diag-csrf", ip="10.8.0.6")
    r = admin.client.post(f"/devices/{dev}/diagnostics/run", follow_redirects=False)
    assert r.status_code == 403 and latest(dev) is None


def test_requires_login(_app_client):
    assert _app_client.get("/devices/1/diagnostics", follow_redirects=False).status_code == 303


def test_unknown_device_and_foreign_snapshot_are_404(admin):
    assert admin.get("/devices/99999/diagnostics").status_code == 404
    a = add_sim_device(admin, name="diag-a", ip="10.8.0.7")
    b = add_sim_device(admin, name="diag-b", ip="10.8.0.8")
    run_diag(admin, a)
    snap_a = latest(a)
    # a snapshot belonging to another device must not be readable through this device
    assert admin.get(f"/devices/{b}/diagnostics?snapshot={snap_a.id}").status_code == 404
    assert admin.get(f"/devices/{a}/diagnostics?snapshot={snap_a.id}").status_code == 200


def test_blocked_while_update_running(admin):
    dev = add_sim_device(admin, name="diag-busy", ip="10.8.0.9")
    rel = add_release(admin, version="6.6.6")
    with SessionLocal() as db:
        job = UpdateJob(device_id=dev, release_id=rel, status="running", requested_by="x")
        db.add(job)
        db.commit()
        job_id = job.id
    run_diag(admin, dev)
    assert latest(dev) is None
    with SessionLocal() as db:
        db.delete(db.get(UpdateJob, job_id))
        db.commit()


def test_history_is_pruned(admin, monkeypatch):
    dev = add_sim_device(admin, name="diag-prune", ip="10.8.1.1")
    monkeypatch.setattr(diagnostics, "KEEP_SNAPSHOTS", 3)
    with SessionLocal() as db:
        device = db.get(Device, dev)
        for _ in range(6):
            diagnostics.run_diagnostics(db, device, "tester")
        assert len(diagnostics.history(db, dev, limit=100)) == 3


def test_deleting_device_removes_snapshots(admin):
    dev = add_sim_device(admin, name="diag-del", ip="10.8.1.2")
    run_diag(admin, dev)
    admin.post(f"/devices/{dev}/delete", page=f"/devices/{dev}")
    with SessionLocal() as db:
        assert db.get(Device, dev) is None
        assert db.scalars(select(DiagSnapshot).where(DiagSnapshot.device_id == dev)).all() == []
