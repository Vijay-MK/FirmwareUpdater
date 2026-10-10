import hashlib
import io

from sqlalchemy import select

from app.database import SessionLocal
from app.drivers import simulator
from app.models import Device, FirmwareRelease, UpdateJob
from app.services import updater

FIRMWARE = b"FAKE-FIRMWARE-IMAGE" * 100
SHA = hashlib.sha256(FIRMWARE).hexdigest()


def add_sim_device(s, name="PLC-1", ip="10.1.2.3", port=502, version="1.0.0", model="TestPLC 100"):
    r = s.post("/devices/new", {"name": name, "vendor": "WAGO", "driver": "simulator",
                                "ip_address": ip, "port": str(port), "model": model,
                                "firmware_version": version}, page="/devices/new")
    assert r.status_code == 303, r.text
    with SessionLocal() as db:
        return db.scalar(select(Device).where(Device.ip_address == ip, Device.port == port)).id


def add_release(s, version="2.0.0", sha=SHA, with_file=True, notes="Security fix for CVE-2024-1111."):
    files = {"package": ("fw.bin", io.BytesIO(FIRMWARE))} if with_file else None
    r = s.post("/firmware/add", {"vendor": "WAGO", "version": version, "model_pattern": "TestPLC*",
                                 "expected_sha256": sha, "release_notes": notes},
               files=files, page="/firmware")
    assert r.status_code == 303
    with SessionLocal() as db:
        return db.scalar(select(FirmwareRelease).where(FirmwareRelease.version == version)).id


def test_login_required(_app_client):
    r = _app_client.get("/devices", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_csrf_enforced(admin):
    r = admin.client.post("/devices/new", data={"name": "x"}, follow_redirects=False)
    assert r.status_code == 403


def test_bad_login(_app_client):
    from tests.conftest import Session
    from fastapi.testclient import TestClient
    from app.main import app
    s = Session(TestClient(app, cookies={}))
    r = s.login("admin", "wrong-password")
    assert r.status_code == 200 and "Invalid" in r.text


def test_rbac_viewer_cannot_add_device(make_user):
    viewer = make_user("viewer1", "viewer")
    r = viewer.post("/devices/new", {"name": "n", "vendor": "WAGO", "driver": "wago",
                                     "ip_address": "10.9.9.9", "port": "502"}, page="/devices")
    assert r.status_code == 403
    assert viewer.get("/devices").status_code == 200
    assert viewer.get("/users").status_code == 403
    assert viewer.get("/audit").status_code == 403


def test_device_validation(admin):
    for ip in ("127.0.0.1", "not-an-ip", "0.0.0.0", "224.0.0.1"):
        r = admin.post("/devices/new", {"name": "bad", "vendor": "WAGO", "driver": "wago",
                                        "ip_address": ip, "port": "502"}, page="/devices/new")
        assert r.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.name == "bad")) is None
    # driver/vendor mismatch is refused
    admin.post("/devices/new", {"name": "mismatch", "vendor": "WAGO", "driver": "siemens",
                                "ip_address": "10.5.5.5", "port": "502"}, page="/devices/new")
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.name == "mismatch")) is None


def test_duplicate_endpoint_rejected(admin):
    add_sim_device(admin, name="dup-a", ip="10.2.0.1")
    admin.post("/devices/new", {"name": "dup-b", "vendor": "WAGO", "driver": "simulator",
                                "ip_address": "10.2.0.1", "port": "502"}, page="/devices/new")
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.name == "dup-b")) is None


def test_credentials_not_stored_plaintext(admin):
    admin.post("/devices/new", {"name": "cred", "vendor": "WAGO", "driver": "simulator",
                                "ip_address": "10.2.0.2", "port": "502", "username": "root",
                                "password": "TopSecret!"}, page="/devices/new")
    with SessionLocal() as db:
        d = db.scalar(select(Device).where(Device.name == "cred"))
        assert d.credentials_enc and "TopSecret!" not in d.credentials_enc
    assert "TopSecret!" not in admin.get(f"/devices/{d.id}").text


def test_release_notifies_matching_devices_only(admin):
    dev = add_sim_device(admin, name="notify", ip="10.3.0.1", version="1.0.0")
    add_sim_device(admin, name="other-model", ip="10.3.0.2", version="1.0.0", model="Different")
    add_release(admin, version="3.0.0", with_file=False)
    page = admin.get("/notifications").text
    assert "notify: firmware 3.0.0 available" in page
    assert "other-model: firmware 3.0.0" not in page
    assert "3.0.0" in admin.get(f"/devices/{dev}").text


def test_hash_verification_flag(admin):
    good = add_release(admin, version="4.0.0", sha=SHA)
    bad = add_release(admin, version="4.1.0", sha="0" * 64)
    with SessionLocal() as db:
        assert db.get(FirmwareRelease, good).hash_verified is True
        assert db.get(FirmwareRelease, bad).hash_verified is False
