import io

from sqlalchemy import select

from app.database import SessionLocal
from app.drivers import simulator
from app.models import Device, FirmwareRelease, UpdateJob
from app.services import updater
from tests.test_updates import job_status, request_job
from tests.test_workflow import FIRMWARE, SHA, add_release, add_sim_device


def approved_job(admin, make_user, name, ip, version):
    approver = make_user("appr_" + name, "approver")
    dev = add_sim_device(admin, name=name, ip=ip)
    rel = add_release(admin, version=version)
    _, job = request_job(admin, dev, rel)
    approver.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}")
    assert job_status(job.id) == "approved"
    return dev, rel, job


def job_log(job_id):
    with SessionLocal() as db:
        return db.get(UpdateJob, job_id).log


def device_version(dev_id):
    with SessionLocal() as db:
        return db.get(Device, dev_id).firmware_version


def test_run_state_blocks_update(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "running", "10.4.0.7", "2.4.0")
    simulator.set_state("10.4.0.7", 502, version="1.0.0", run_state="run", online=True)
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert device_version(dev) == "1.0.0"
    assert "RUN" in job_log(job.id)


def test_offline_device_blocks_update(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "offline", "10.4.0.8", "2.5.0")
    simulator.set_state("10.4.0.8", 502, version="1.0.0", online=False)
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert "not reachable" in job_log(job.id)


def test_flash_failure_marks_failed_and_keeps_version(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "flashfail", "10.4.0.9", "2.6.0")
    simulator.set_state("10.4.0.9", 502, version="1.0.0", fail_update=True, online=True)
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert device_version(dev) == "1.0.0"


def test_version_mismatch_with_device_blocks_update(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "stale", "10.4.1.0", "2.6.5")
    simulator.set_state("10.4.1.0", 502, version="1.5.0", online=True)  # device differs from inventory
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert "expected" in job_log(job.id)


def test_tampered_package_refused(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "tamper", "10.4.1.1", "2.7.0")
    with SessionLocal() as db:
        path = db.get(FirmwareRelease, rel).file_path
    with open(path, "ab") as fh:
        fh.write(b"malicious")
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert "hash changed" in job_log(job.id)


def test_active_job_blocks_second_request_and_delete(admin, make_user):
    dev, rel, job = approved_job(admin, make_user, "active", "10.4.1.2", "2.8.0")
    rel2 = add_release(admin, version="2.9.0")
    _, job2 = request_job(admin, dev, rel2)
    assert job2.id == job.id  # no second job was created
    admin.post(f"/devices/{dev}/delete", page=f"/devices/{dev}")
    with SessionLocal() as db:
        assert db.get(Device, dev) is not None


def test_vendor_without_update_tool_fails_safely(admin, make_user):
    approver = make_user("appr_siem", "approver")
    admin.post("/devices/new", {"name": "siemens1", "vendor": "Siemens", "driver": "siemens",
                                "ip_address": "10.4.1.3", "port": "102", "model": "S7 TestPLC",
                                "firmware_version": "1.0.0"}, page="/devices/new")
    admin.post("/firmware/add", {"vendor": "Siemens", "version": "2.0.0", "model_pattern": "S7*",
                                 "expected_sha256": SHA},
               files={"package": ("f.bin", io.BytesIO(FIRMWARE))}, page="/firmware")
    with SessionLocal() as db:
        dev = db.scalar(select(Device).where(Device.name == "siemens1")).id
        rel = db.scalar(select(FirmwareRelease).where(FirmwareRelease.vendor == "Siemens")).id
    _, job = request_job(admin, dev, rel)
    approver.post(f"/updates/{job.id}/approve", page=f"/updates/{job.id}")
    updater.run_job(job.id)
    assert job_status(job.id) == "failed"
    assert "No update tool configured" in job_log(job.id)
