"""Scenario definition shared by the simulated-device data files.

IMPORTANT: this is SIMULATED data. Models and firmware strings are plausible, serial numbers are
fake (SIM-...), and event codes/texts imitate the style of each vendor's diagnostic buffer. They
are NOT an authoritative list of vendor error codes. Always check real codes in the vendor manual.
"""
from dataclasses import dataclass, field


@dataclass
class Scenario:
    name: str
    vendor: str            # one of app.config.VENDORS
    model: str
    ip: str
    port: int
    group: str
    location: str
    firmware: str
    hardware: str
    serial: str
    story: str             # one line shown in the device notes: what is going on with this PLC
    run_state: str = "run"
    health: str = "ok"
    online: bool = True
    latency_ms: float = 5.0
    facts: dict = field(default_factory=dict)
    # (minutes_ago, severity, code, message, source); any order, shown newest first
    events: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # earlier diagnostics runs: (hours_ago, health, run_state, reachable), oldest first
    timeline: list = field(default_factory=list)
    # True when one event is intentionally not in the knowledge base (shows the fallback help)
    expect_unrecognised: bool = False
