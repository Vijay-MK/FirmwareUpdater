import struct
import sys

import pytest

from app.drivers.base import BaseDriver, DeviceInfo
from app.drivers.diag import DiagReport, merge_tool_output
from app.drivers.rockwell import RockwellDriver

EVENT = {"timestamp": "2025-01-31 10:15:02", "severity": "error", "code": "E1", "message": "boom"}


class _Vendor(BaseDriver):
    vendor = "Delta"

    def probe(self):
        return DeviceInfo(reachable=True, detail="ok")


def test_tool_output_is_validated_and_limited():
    report = DiagReport(reachable=True)
    merge_tool_output(report, {"run_state": "RUN", "health": "bogus", "facts": {"k" * 200: "v" * 1000},
                               "events": [{"severity": "CRITICAL", "message": "m" * 2000}, "junk", 5]})
    assert report.run_state == "run" and report.health == "unknown"
    assert all(len(k) <= 60 and len(v) <= 200 for k, v in report.facts.items())
    assert len(report.events) == 1 and report.events[0].severity == "error"
    assert len(report.events[0].message) == 500 and report.events_available


def test_tool_output_event_cap():
    report = DiagReport(reachable=True)
    merge_tool_output(report, {"events": [{"message": str(i)} for i in range(2000)]})
    assert len(report.events) == 500 and report.notes


def test_tool_output_must_be_object():
    with pytest.raises(ValueError):
        merge_tool_output(DiagReport(reachable=True), ["not", "an", "object"])


def test_external_diag_tool_end_to_end(monkeypatch, tmp_path):
    script = tmp_path / "diag_tool.py"
    script.write_text("import json, sys\n"
                      f"print(json.dumps({{'run_state': 'stop', 'health': 'ok', 'events': [{EVENT!r}],"
                      " 'facts': {'host_arg': sys.argv[1]}}))\n")
    monkeypatch.setenv("FWMGR_DIAG_CMD_DELTA", f"{sys.executable} {script} {{host}}")
    report = _Vendor("10.0.0.1", 502).diagnostics()
    assert report.run_state == "stop" and report.health == "ok"
    assert report.events_available and len(report.events) == 1
    assert report.facts["host_arg"] == "10.0.0.1"


def test_external_diag_tool_failures_do_not_raise(monkeypatch):
    monkeypatch.setenv("FWMGR_DIAG_CMD_DELTA", f"{sys.executable} -c \"import sys; sys.exit(2)\"")
    assert any("code 2" in n for n in _Vendor("10.0.0.1", 502).diagnostics().notes)
    monkeypatch.setenv("FWMGR_DIAG_CMD_DELTA", f"{sys.executable} -c \"print('not json')\"")
    assert any("not valid" in n for n in _Vendor("10.0.0.1", 502).diagnostics().notes)
    monkeypatch.setenv("FWMGR_DIAG_CMD_DELTA", "/nonexistent/tool {host}")
    assert any("not found" in n for n in _Vendor("10.0.0.1", 502).diagnostics().notes)


def test_generic_driver_explains_missing_buffer(monkeypatch):
    monkeypatch.delenv("FWMGR_DIAG_CMD_DELTA", raising=False)
    report = _Vendor("10.0.0.1", 502).diagnostics()
    assert not report.events_available and any("FWMGR_DIAG_CMD_DELTA" in n for n in report.notes)


def test_diag_tool_receives_credentials_via_environment(monkeypatch):
    code = "import os,json; print(json.dumps({'facts': {'user': os.environ['FWMGR_DEV_USER']}}))"
    monkeypatch.setenv("FWMGR_DIAG_CMD_DELTA", f"{sys.executable} -c \"{code}\"")
    drv = _Vendor("10.0.0.1", 502, {"username": "svc", "password": "pw"})
    assert drv.diagnostics().facts["user"] == "svc"


def _rockwell_report(status: int, monkeypatch) -> DiagReport:
    item = struct.pack("<H16sHHHBBHI", 1, b"\x00" * 16, 1, 14, 166, 20, 11, status, 0xABCD)
    item += bytes([4]) + b"1756" + b"\x03"
    body = struct.pack("<H", 1) + struct.pack("<HH", 0x000C, len(item)) + item
    data = struct.pack("<HHII8sI", 0x0063, len(body), 0, 0, b"fwmgr000", 0) + body

    class FakeSock:
        def __init__(self):
            self.buf = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def sendall(self, b):
            pass

        def recv(self, n):
            out, self.buf = self.buf[:n], self.buf[n:]
            return out

    monkeypatch.setattr("socket.create_connection", lambda *a, **k: FakeSock())
    return RockwellDriver("10.0.0.2", 44818).diagnostics()


def test_rockwell_status_decoding(monkeypatch):
    ok = _rockwell_report(0x0005, monkeypatch)
    assert ok.health == "ok" and ok.facts["Owned (connected to a controller)"] == "yes"
    assert ok.facts["CIP device state"] == "Operational"
    assert ok.run_state == "unknown"  # never claimed: ListIdentity cannot tell RUN from PROGRAM
    assert not ok.events_available

    minor = _rockwell_report(0x0100, monkeypatch)
    assert minor.health == "warning" and [e.severity for e in minor.events] == ["warning"]

    major = _rockwell_report(0x0500, monkeypatch)
    assert major.health == "fault" and "error" in {e.severity for e in major.events}
