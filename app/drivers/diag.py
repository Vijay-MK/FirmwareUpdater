"""Diagnostics data model shared by all drivers, plus the optional external diagnostics tool hook.

Reading a PLC's diagnostic buffer is vendor-specific. Where this tool has no native reader, an
administrator can set FWMGR_DIAG_CMD_<VENDOR> to a read-only command that prints JSON:

    {"run_state": "run|stop|unknown", "health": "ok|warning|fault|unknown",
     "facts": {"CPU temperature": "41 C"},
     "events": [{"timestamp": "2025-01-31 10:15:02", "severity": "error|warning|info",
                 "code": "16#02:4E", "message": "...", "source": "..."}]}

The output is validated and size-limited before it is shown or stored.
"""
import os
import shlex
import subprocess
from dataclasses import dataclass, field

import json

from ..config import settings

SEVERITIES = ("info", "warning", "error")
HEALTH = ("ok", "warning", "fault", "unknown")
RUN_STATES = ("run", "stop", "unknown")
MAX_EVENTS = 500
MAX_FACTS = 50
MAX_OUTPUT_CHARS = 1_000_000
TOOL_TIMEOUT_SECONDS = 60

_SEVERITY_ALIASES = {"error": "error", "err": "error", "fault": "error", "critical": "error",
                     "fatal": "error", "warning": "warning", "warn": "warning"}


@dataclass
class DiagEvent:
    timestamp: str = ""
    severity: str = "info"
    code: str = ""
    message: str = ""
    source: str = ""


@dataclass
class DiagReport:
    reachable: bool
    run_state: str = "unknown"
    health: str = "unknown"
    latency_ms: float | None = None
    facts: dict[str, str] = field(default_factory=dict)
    events: list[DiagEvent] = field(default_factory=list)
    events_available: bool = False     # True when the device's own event/diagnostic buffer was read
    notes: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        """The part stored as JSON (the scalar fields have their own database columns)."""
        return {"facts": self.facts, "events_available": self.events_available, "notes": self.notes,
                "events": [vars(e) for e in self.events]}


def _text(value, limit: int) -> str:
    return "" if value is None else str(value)[:limit]


def clean_severity(value) -> str:
    return _SEVERITY_ALIASES.get(str(value).strip().lower(), "info")


def merge_tool_output(report: DiagReport, data) -> None:
    """Merge validated output of an external diagnostics tool into the report."""
    if not isinstance(data, dict):
        raise ValueError("output must be a JSON object")
    run_state = str(data.get("run_state", "")).strip().lower()
    if run_state in RUN_STATES:
        report.run_state = run_state
    health = str(data.get("health", "")).strip().lower()
    if health in HEALTH:
        report.health = health
    facts = data.get("facts")
    if isinstance(facts, dict):
        for key, value in list(facts.items())[:MAX_FACTS]:
            report.facts[_text(key, 60)] = _text(value, 200)
    events = data.get("events")
    if isinstance(events, list):
        report.events_available = True
        for item in events[:MAX_EVENTS]:
            if isinstance(item, dict):
                report.events.append(DiagEvent(
                    timestamp=_text(item.get("timestamp"), 40), severity=clean_severity(item.get("severity")),
                    code=_text(item.get("code"), 40), message=_text(item.get("message"), 500),
                    source=_text(item.get("source"), 80)))
        if len(events) > MAX_EVENTS:
            report.notes.append(f"Only the first {MAX_EVENTS} of {len(events)} events are kept.")


def run_diag_tool(report: DiagReport, vendor: str, host: str, port: int, credentials: dict) -> bool:
    """Run the configured external tool (no shell). Returns False if none is configured.
    Failures are recorded as notes on the report instead of raising, so basic state is still shown."""
    template = settings.diag_command(vendor)
    if not template:
        return False
    try:
        args = [t.replace("{host}", host).replace("{port}", str(port)) for t in shlex.split(template)]
    except ValueError as exc:
        report.notes.append(f"Diagnostics tool command is invalid: {exc}")
        return True
    if not args:
        return False
    env = dict(os.environ)
    env["FWMGR_DEV_USER"] = credentials.get("username", "")
    env["FWMGR_DEV_PASSWORD"] = credentials.get("password", "")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS, env=env)
    except FileNotFoundError:
        report.notes.append(f"Diagnostics tool not found: {args[0]}")
        return True
    except subprocess.TimeoutExpired:
        report.notes.append(f"Diagnostics tool timed out after {TOOL_TIMEOUT_SECONDS} s.")
        return True
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-200:]
        report.notes.append(f"Diagnostics tool exited with code {proc.returncode}. {detail}".strip())
        return True
    if len(proc.stdout) > MAX_OUTPUT_CHARS:
        report.notes.append("Diagnostics tool output is too large and was ignored.")
        return True
    try:
        merge_tool_output(report, json.loads(proc.stdout))
    except ValueError as exc:
        report.notes.append(f"Diagnostics tool output is not valid: {exc}")
    return True
