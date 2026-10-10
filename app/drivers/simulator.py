"""In-memory simulated PLC for demos and tests. Never touches the network."""
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from ..config import settings

from .base import BaseDriver, DeviceInfo, DriverError
from .diag import DiagEvent, DiagReport

_lock = threading.Lock()
STATE: dict[tuple[str, int], dict] = {}


_file_mtime: int | None = None
_EVENT_FIELDS = ("timestamp", "severity", "code", "message", "source")


def reset() -> None:
    global _file_mtime
    with _lock:
        STATE.clear()
        _file_mtime = None


def set_state(host: str, port: int, **values) -> None:
    with _lock:
        STATE.setdefault((host, port), {}).update(values)


def replace_state(host: str, port: int, state: dict) -> None:
    """Replace a device's whole state (set_state only merges, so old events would linger)."""
    with _lock:
        STATE[(host, port)] = dict(state)


# ---- scenarios shared between processes -----------------------------------------------------
# tests/simulate_devices.py runs as its own process. It writes the simulated device states to a
# JSON file in the data directory; the running web server reloads that file when it changes, so
# "Run diagnostics now" in the UI shows the same picture as the seeded history.

def state_file() -> Path:
    return settings.data_dir / "simulator_state.json"


def _read_file() -> dict:
    try:
        raw = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _sync_from_file() -> None:
    global _file_mtime
    try:
        mtime = state_file().stat().st_mtime_ns
    except OSError:
        return
    if mtime == _file_mtime:
        return
    raw = _read_file()
    with _lock:
        for key, value in raw.items():
            host, _, port = str(key).rpartition(":")
            if host and port.isdigit() and isinstance(value, dict):
                STATE[(host, int(port))] = dict(value)
        _file_mtime = mtime


def _write_file(content: dict) -> None:
    path = state_file()
    if not content:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(content, indent=1), encoding="utf-8")
    os.replace(tmp, path)  # atomic: the server never reads a half-written file


def write_states(states: dict[tuple[str, int], dict]) -> None:
    content = _read_file()
    content.update({f"{host}:{port}": value for (host, port), value in states.items()})
    _write_file(content)


def remove_states(keys: list[tuple[str, int]]) -> None:
    content = _read_file()
    for host, port in keys:
        content.pop(f"{host}:{port}", None)
    _write_file(content)


def get_state(host: str, port: int) -> dict:
    _sync_from_file()
    with _lock:
        return dict(STATE.get((host, port), {}))


class SimulatorDriver(BaseDriver):
    name = "simulator"
    reads_version = True
    native_update = True
    notes = "Simulated PLC. No real device is contacted; flashing only changes in-memory state."

    def _state(self) -> dict:
        _sync_from_file()
        with _lock:
            return STATE.setdefault((self.host, self.port), {
                "version": self.current_version, "run_state": "stop", "online": True})

    def probe(self) -> DeviceInfo:
        st = self._state()
        if not st.get("online", True):
            return DeviceInfo(reachable=False, detail="Simulated device offline")
        return DeviceInfo(reachable=True, firmware_version=st.get("version", ""),
                          run_state=st.get("run_state", "stop"), model=st.get("model", ""),
                          serial_number=st.get("serial", ""), hardware_revision=st.get("hardware", ""),
                          detail="Simulated device")

    def collect_details(self, report: DiagReport) -> None:
        st = self._state()
        facts = st.get("facts")
        if isinstance(facts, dict) and facts:
            report.facts.update({str(k)[:60]: str(v)[:200] for k, v in facts.items()})
        else:
            report.facts["Simulated CPU temperature"] = f"{st.get('cpu_temp', 38)} C"
            report.facts["Simulated scan time"] = f"{st.get('scan_ms', 4)} ms"
        if st.get("latency_ms") is not None:
            report.latency_ms = float(st["latency_ms"])
        report.health = st.get("health", "ok")
        report.events_available = True
        for item in st.get("events", []):
            report.events.append(DiagEvent(**{k: str(v) for k, v in item.items() if k in _EVENT_FIELDS}))
        report.notes.extend(str(n) for n in st.get("notes", []))

    def apply_update(self, firmware_path: str, version: str, log: Callable[[str], None]) -> None:
        st = self._state()
        if st.get("fail_update"):
            raise DriverError("Simulated flash failure")
        if not os.path.isfile(firmware_path):
            raise DriverError("Firmware file missing")
        log("Simulator: transferring image...")
        time.sleep(0.05)
        log("Simulator: flashing...")
        with _lock:
            st["version"] = version
