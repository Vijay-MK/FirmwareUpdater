import io
import time

from sqlalchemy import select

from app.database import SessionLocal
from app.drivers import simulator
from app.models import Device, FirmwareRelease, UpdateJob
from app.services import updater
from tests.test_workflow import FIRMWARE, SHA, add_release, add_sim_device


def request_job(s, dev_id, rel_id, backup=True, window=True):
    data = {"device_id": str(dev_id), "release_id": str(rel_id)}
    if backup:
        data["backup_confirmed"] = "true"
    if window:
        data["window_confirmed"] = "true"
    s.post("/updates/request", data, page=f"/devices/{dev_id}")
    with SessionLocal() as db:
        return None, db.scalar(select(UpdateJob).where(UpdateJob.device_id == dev_id)
                               .order_by(UpdateJob.id.desc()))


def wait_done(job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        with SessionLocal() as db:
            job = db.get(UpdateJob, job_id)
            if job.status not in ("approved", "running"):
                return job.status
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def job_status(job_id):
    with SessionLocal() as db:
        return db.get(UpdateJob, job_id).status


def test_request_requires_confirmations(admin):
    dev = add_sim_device(admin, name="conf", ip="10.4.0.1")
    rel = add_release(admin, version="2.0.1")
    assert request_job(admin, dev, rel, backup=False)[1] is None
    assert request_job(admin, dev, rel, window=False)[1] is None


def test_request_blocked_without_package(admin):
    dev = add_sim_device(admin, name="nofile", ip="10.4.0.2")
    rel = add_release(admin, version="2.0.2", with_file=False)
    assert request_job(admin, dev, rel)[1] is None


def test_downgrade_and_same_version_refused(admin):
    dev = add_sim_device(admin, name="down", ip="10.4.0.3", version="5.0.0")
    rel = add_release(admin, version="5.0.0")
    assert request_job(admin, dev, rel)[1] is None


def test_four_eyes_and_full_success_flow(admin, make_user):
    engineer = make_user("eng1", "engineer")
    approver = make_user("appr1", "approver")
    dev = add_sim_device(engineer, name="flow", ip="10.4.0.4", version="1.0.0")
    rel = add_release(engineer, version="2.1.0")
    _, job = request_job(engineer, dev, rel)
    assert job and job.status == "pending_approval"

    # an engineer cannot approve; starting before approval does nothing
    assert engineer.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}").status_code == 403
    engineer.post(f"/updates/{job.id}/start", page=f"/updates/{job.id}")
    assert job_status(job.id) == "pending_approval"

    approver.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}")
    assert job_status(job.id) == "approved"

    engineer.post(f"/updates/{job.id}/start", page=f"/updates/{job.id}")
    assert wait_done(job.id) == "completed"
    with SessionLocal() as db:
        d = db.get(Device, dev)
        assert d.firmware_version == "2.1.0" and d.version_source == "device"
    assert "Verified" in admin.get(f"/updates/{job.id}").text


def test_self_approval_blocked(make_user):
    approver = make_user("appr2", "approver")
    dev = add_sim_device(approver, name="self", ip="10.4.0.5")
    rel = add_release(approver, version="2.2.0")
    _, job = request_job(approver, dev, rel)
    approver.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}")
    assert job_status(job.id) == "pending_approval"


def test_unverified_hash_needs_acknowledgement(admin, make_user):
    approver = make_user("appr3", "approver")
    dev = add_sim_device(admin, name="unver", ip="10.4.0.6")
    rel = add_release(admin, version="2.3.0", sha="")
    _, job = request_job(admin, dev, rel)
    approver.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}")
    assert job_status(job.id) == "pending_approval"
    approver.post(f"/updates/{job.id}/approve", {"acknowledge_unverified": "true"},
                  page=f"/updates/{job.id}")
    assert job_status(job.id) == "approved"
