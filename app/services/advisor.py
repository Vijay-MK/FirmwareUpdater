"""Troubleshooting help for a diagnostics snapshot.

Layer 1 (always, offline, deterministic): the built-in knowledge base maps event text to probable
causes and fix steps. Layer 2 (optional, on request): an LLM adds a prioritised summary. The LLM
only sees diagnostic text and never device names, addresses or serial numbers, and only if the
administrator enabled FWMGR_LLM_SEND_FLEET_DATA. All text is advisory: nothing here changes a PLC.
"""
import re
from functools import lru_cache

from ..knowledge import Rule, all_rules

SEVERITY_ORDER = {"error": 2, "warning": 1, "info": 0}
MAX_EVENTS_PER_ISSUE = 5
MAX_CONTEXT_EVENTS = 25
GENERIC_DISCLAIMER = ("General guidance, not a substitute for the vendor manual and your plant's safety and "
                      "change procedures. Never act on a running process without authorisation.")


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[Rule, re.Pattern], ...]:
    return tuple((r, re.compile(r.pattern, re.IGNORECASE)) for r in all_rules())


def match_rule(vendor: str, event: dict) -> Rule | None:
    vendor = (vendor or "").lower()          # Device.vendor is stored as "Siemens", rules use "siemens"
    text = " ".join(str(event.get(k, "")) for k in ("code", "message", "source"))
    for rule, pattern in _compiled():
        if rule.vendors and vendor not in rule.vendors:
            continue
        if pattern.search(text):
            return rule
    return None


def _issue(rule: Rule | None, vendor: str) -> dict:
    if rule is None:
        return {"key": "unrecognised", "title": "Event not in the built-in knowledge base",
                "causes": ["The event text does not match any known pattern."],
                "steps": [f"Look up the code in the {vendor} diagnostics manual for your CPU and firmware.",
                          "Check what changed around the event time (download, power cycle, network work).",
                          "If it repeats, collect the full diagnostic buffer and ask the vendor support."],
                "caution": "", "escalate": False, "vendor_specific": False, "events": [], "count": 0,
                "severity": "info"}
    return {"key": rule.key, "title": rule.title, "causes": list(rule.causes), "steps": list(rule.steps),
            "caution": rule.caution, "escalate": rule.escalate, "vendor_specific": bool(rule.vendors),
            "events": [], "count": 0, "severity": "info"}


def advise(vendor: str, snap_data: dict, *, reachable: bool, health: str, run_state: str) -> dict:
    """Rule-based advice. Returns {"issues": [...], "overall": str, "disclaimer": str}."""
    issues: dict[str, dict] = {}
    for event in snap_data.get("events", []):
        sev = event.get("severity", "info")
        if sev not in ("warning", "error"):
            continue                                   # informational entries need no fix
        rule = match_rule(vendor, event)
        issue = issues.setdefault(rule.key if rule else "unrecognised", _issue(rule, vendor))
        issue["count"] += 1
        if len(issue["events"]) < MAX_EVENTS_PER_ISSUE:
            issue["events"].append({k: event.get(k, "") for k in ("timestamp", "severity", "code", "message", "source")})
        if SEVERITY_ORDER[sev] > SEVERITY_ORDER[issue["severity"]]:
            issue["severity"] = sev

    if not reachable:
        rule = next(r for r, _ in _compiled() if r.key == "communication")
        issue = issues.setdefault(rule.key, _issue(rule, vendor))
        issue["severity"] = "error"
        issue["events"].insert(0, {"timestamp": "", "severity": "error", "code": "",
                                   "message": "Device did not answer the diagnostics request.", "source": "manager"})
        issue["count"] += 1
    elif health == "fault" and not issues:
        issues["fault_no_event"] = {
            "key": "fault_no_event", "title": "Device reports a fault but no diagnostic event was read",
            "causes": ["The device status shows a fault, but its event buffer is empty or unreadable here."],
            "steps": ["Check the LEDs of the CPU and modules and the vendor engineering tool for the active fault.",
                      "Configure a diagnostics tool for this vendor (FWMGR_DIAG_CMD_<VENDOR>) to read the buffer."],
            "caution": "", "escalate": False, "vendor_specific": False, "events": [], "count": 0, "severity": "error"}

    ordered = sorted(issues.values(), key=lambda i: (-SEVERITY_ORDER[i["severity"]], -i["count"], i["title"]))
    if not ordered:
        overall = "No warnings or errors were found in the data that was read."
        if health == "unknown":
            overall += " The device health could not be determined, so this is not a guarantee."
    else:
        worst = ordered[0]["severity"]
        overall = f"{len(ordered)} issue(s) need attention; most severe: {worst}."
        if run_state == "stop":
            overall += " The device is in STOP: check whether this is planned before restarting."
    return {"issues": ordered, "overall": overall, "disclaimer": GENERIC_DISCLAIMER}


def build_llm_context(vendor: str, model: str, firmware: str, snap_data: dict, *, reachable: bool,
                      health: str, run_state: str, issues: list[dict]) -> str:
    """Text for the LLM. Deliberately excludes device name, IP address, serial, location and group."""
    lines = [f"Vendor: {vendor}", f"Model: {model or 'unknown'}", f"Firmware: {firmware or 'unknown'}",
             f"Reachable: {reachable}", f"Health: {health}", f"Run state: {run_state}", "Events (untrusted device text):"]
    count = 0
    for issue in issues:
        for e in issue["events"]:
            if count >= MAX_CONTEXT_EVENTS:
                break
            lines.append(f"- [{e['severity']}] {e['timestamp']} code={e['code']} src={e['source']}: "
                         f"{str(e['message'])[:200]} (matched topic: {issue['title']})")
            count += 1
    if count == 0:
        lines.append("- none")
    return "\n".join(lines)
