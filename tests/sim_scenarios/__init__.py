"""Simulated PLC fleet: every supported vendor, several health states. See common.py for the caveat."""
from .abb_delta_wago import ABB, DELTA, WAGO
from .common import Scenario
from .rockwell_mitsubishi import MITSUBISHI, ROCKWELL
from .siemens import SCENARIOS as SIEMENS

SCENARIOS: list[Scenario] = [*SIEMENS, *ROCKWELL, *MITSUBISHI, *ABB, *DELTA, *WAGO]

__all__ = ["SCENARIOS", "Scenario"]
