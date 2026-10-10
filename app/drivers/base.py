"""Driver interface. A driver talks to one family of PLCs.

Honest capability model: reading the firmware version from the device and flashing are
vendor-specific. Where this tool has no native protocol implementation, the driver only
checks reachability and flashing is delegated to the vendor's own CLI tool, configured by an
administrator through FWMGR_UPDATE_CMD_<VENDOR>.
"""
import os
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from ..config import settings
from .diag import DiagReport, run_diag_tool


class DriverError(Exception):
    pass


class NotSupported(DriverError):
    pass


@dataclass
class DeviceInfo:
    reachable: bool
    firmware_version: str = ""
    hardware_revision: str = ""
    serial_number: str = ""
    model: str = ""
    run_state: str = "unknown"   # run / stop / unknown
    detail: str = ""


class BaseDriver:
    name = "generic"
    vendor = ""
    reads_version = False        # can read firmware version from the device itself
    native_update = False        # can flash without an external tool
    notes = "Reachability check only. Firmware flashing uses the configured vendor tool."
    timeout = 3.0

    def __init__(self, host: str, port: int, credentials: dict | None = None,
                 current_version: str = ""):
        self.host = host
        self.port = port
        self.credentials = credentials or {}
        self.current_version = current_version

    # ---- discovery -------------------------------------------------------------
    def probe(self) -> DeviceInfo:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                return DeviceInfo(reachable=True, detail="TCP port reachable")
        except OSError as exc:
            return DeviceInfo(reachable=False, detail=f"Not reachable: {exc}")

    # ---- update ------------------------------------------------------------------
    def update_tool_configured(self) -> bool:
        return self.native_update or bool(settings.update_command(self.vendor or self.name))

    def apply_update(self, firmware_path: str, version: str, log: Callable[[str], None]) -> None:
        template = settings.update_command(self.vendor or self.name)
        if not template:
            raise NotSupported(
                f"No update tool configured for {self.vendor or self.name}. Set the environment "
                f"variable FWMGR_UPDATE_CMD_{(self.vendor or self.name).upper()} to the vendor "
                "CLI command, e.g. 'tool --ip {host} --file {file}'.")
        values = {"host": self.host, "port": str(self.port), "file": firmware_path, "version": version}
        args = [token.format(**values) for token in shlex.split(template)]
        env = dict(os.environ)
        env["FWMGR_DEV_USER"] = self.credentials.get("username", "")
        env["FWMGR_DEV_PASSWORD"] = self.credentials.get("password", "")
        log(f"Running external tool: {shlex.join(args[:1])} (arguments omitted from log)")
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=3600, env=env)
        except FileNotFoundError:
            raise DriverError(f"Update tool not found: {args[0]}")
        except subprocess.TimeoutExpired:
            raise DriverError("Update tool timed out after 60 minutes")
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if output:
            log("Tool output:\n" + output[-3000:])
        if proc.returncode != 0:
            raise DriverError(f"Update tool exited with code {proc.returncode}")

    # ---- diagnostics (read-only) -----------------------------------------------
    def collect_details(self, report: DiagReport) -> None:
        """Driver-specific diagnostics. Subclasses with a native protocol override this."""

    def diagnostics(self) -> DiagReport:
        started = time.perf_counter()
        info = self.probe()
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        report = DiagReport(reachable=info.reachable, run_state=info.run_state,
                            latency_ms=elapsed if info.reachable else None)
        report.facts["Address"] = f"{self.host}:{self.port}"
        report.facts["Connection check"] = info.detail
        for label, value in (("Model", info.model), ("Firmware version", info.firmware_version),
                             ("Hardware revision", info.hardware_revision),
                             ("Serial number", info.serial_number)):
            if value:
                report.facts[label] = value
        if not info.reachable:
            return report
        self.collect_details(report)
        vendor = self.vendor or self.name
        configured = run_diag_tool(report, vendor, self.host, self.port, self.credentials)
        if not configured and not report.events_available:
            report.notes.append(
                f"The diagnostic buffer cannot be read natively for {vendor}. Set "
                f"FWMGR_DIAG_CMD_{vendor.upper()} to a read-only tool that prints diagnostics as JSON.")
        return report

    def read_version(self) -> str:
        """Firmware version as reported by the device, or '' if the driver cannot read it."""
        info = self.probe()
        return info.firmware_version if info.reachable else ""
