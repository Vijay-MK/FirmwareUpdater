import hashlib
import sys

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.drivers.base import BaseDriver, DriverError
from app.models import Device, FeedSource, FirmwareRelease
from app.services import catalog
from tests.test_workflow import add_sim_device


class FakeResponse:
    def __init__(self, payload=None, body=b""):
        self._payload, self._body = payload, body

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload

    def iter_bytes(self, n):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_feed_sync_creates_releases_and_notifies(admin, monkeypatch):
    add_sim_device(admin, name="feeddev", ip="10.6.0.1", version="1.0.0", model="FeedPLC 1")
    payload = {"releases": [
        {"version": "7.1.0", "model_pattern": "FeedPLC*", "release_notes": "CVE-2025-0001 security fix",
         "sha256": "ab" * 32, "download_url": "https://vendor.example/fw.bin"},
        {"version": "", "release_notes": "ignored: no version"},
    ]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(payload))
    with SessionLocal() as db:
        feed = FeedSource(vendor="WAGO", name="test feed", url="https://vendor.example/feed.json")
        db.add(feed)
        db.commit()
        new, status = catalog.sync_feed(db, feed)
        assert new == 1 and status.startswith("OK")
        rel = db.scalar(select(FirmwareRelease).where(FirmwareRelease.version == "7.1.0"))
        assert rel.ai_urgency == "high" and "CVE-2025-0001" in rel.cves
        # second sync is idempotent
        assert catalog.sync_feed(db, feed)[0] == 0
    assert "feeddev: firmware 7.1.0 available" in admin.get("/notifications").text


def test_feed_error_is_recorded_not_raised(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "get", boom)
    with SessionLocal() as db:
        feed = FeedSource(vendor="ABB", name="broken", url="https://down.example/f.json")
        db.add(feed)
        db.commit()
        new, status = catalog.sync_feed(db, feed)
        assert new == 0 and status.startswith("Error")


def test_feed_rejects_non_http_url():
    with SessionLocal() as db:
        feed = FeedSource(vendor="ABB", name="file-feed", url="file:///etc/passwd")
        db.add(feed)
        db.commit()
        assert catalog.sync_feed(db, feed)[1].startswith("Error")


def _release(db, version, expected, url="https://vendor.example/fw.bin"):
    rel = FirmwareRelease(vendor="Delta", version=version, model_pattern="*", download_url=url,
                          expected_sha256=expected)
    db.add(rel)
    db.commit()
    return rel


def test_download_verifies_hash(monkeypatch):
    body = b"firmware-bytes"
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeResponse(body=body))
    with SessionLocal() as db:
        good = _release(db, "8.0.0", hashlib.sha256(body).hexdigest())
        catalog.download_release(db, good)
        assert good.hash_verified and good.file_path


def test_download_hash_mismatch_deletes_file(monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeResponse(body=b"evil"))
    with SessionLocal() as db:
        bad = _release(db, "8.1.0", "00" * 32)
        with pytest.raises(ValueError, match="mismatch"):
            catalog.download_release(db, bad)
        assert not bad.file_path
    leftovers = list((settings.firmware_dir / "delta" / "8.1.0").glob("*"))
    assert leftovers == []


def test_download_requires_published_hash():
    with SessionLocal() as db:
        rel = _release(db, "8.2.0", "")
        with pytest.raises(ValueError, match="SHA-256"):
            catalog.download_release(db, rel)


def test_upload_size_limit(monkeypatch):
    import io
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    rel = FirmwareRelease(vendor="ABB", version="9.0.0")
    with pytest.raises(ValueError, match="limit"):
        catalog.store_firmware_file(rel, io.BytesIO(b"x" * (2 * 1024 * 1024)), "big.bin")


def test_upload_filename_is_sanitised():
    import io
    rel = FirmwareRelease(vendor="ABB", version="9.1.0")
    catalog.store_firmware_file(rel, io.BytesIO(b"abc"), "../../../etc/evil name.bin")
    assert str(settings.firmware_dir) in rel.file_path and ".." not in rel.file_path


def test_external_tool_runs_without_shell_and_reports_failure(monkeypatch, tmp_path):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"x")
    logs = []
    monkeypatch.setenv("FWMGR_UPDATE_CMD_ABB", f"{sys.executable} -c \"import sys; print('flashing', sys.argv[1]); sys.exit(0)\" {{file}}")
    class ABB(BaseDriver):
        vendor = "ABB"
    ABB("10.0.0.1", 502).apply_update(str(fw), "2.0", logs.append)
    assert any("flashing" in m for m in logs)

    monkeypatch.setenv("FWMGR_UPDATE_CMD_ABB", f"{sys.executable} -c \"import sys; sys.exit(3)\"")
    with pytest.raises(DriverError, match="code 3"):
        ABB("10.0.0.1", 502).apply_update(str(fw), "2.0", logs.append)


def test_external_tool_credentials_not_in_argv(monkeypatch, tmp_path):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"x")
    logs = []
    code = "import os; print('user=' + os.environ['FWMGR_DEV_USER'])"
    monkeypatch.setenv("FWMGR_UPDATE_CMD_ABB", f"{sys.executable} -c \"{code}\"")
    class ABB(BaseDriver):
        vendor = "ABB"
    ABB("10.0.0.1", 502, {"username": "root", "password": "pw"}).apply_update(str(fw), "2", logs.append)
    assert any("user=root" in m for m in logs)
    assert not any("pw" == m for m in logs)
