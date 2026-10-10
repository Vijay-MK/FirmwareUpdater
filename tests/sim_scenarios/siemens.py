from .common import Scenario

SCENARIOS = [
    Scenario(
        "S7-1500 Filling Line 1", "Siemens", "CPU 1516-3 PN/DP", "10.250.1.11", 102, "Line 1", "Hall A, cabinet A1",
        "V2.9.4", "FS 05", "SIM-S7-0001",
        "CPU stopped 14 min ago after a programming error (DB not loaded) and an I/O access error.",
        run_state="stop", health="fault", latency_ms=6.2,
        facts={"Operating mode": "STOP", "CPU load": "0 %", "Cycle time (min/avg/max)": "1 / 6 / 48 ms",
               "Work memory used": "71 %", "Load memory used": "38 %", "PROFINET devices": "12 configured, 12 online",
               "Last restart": "warm restart, 9 h ago"},
        events=[
            (14, "error", "OB121", "Programming error: DB 12 'RecipeData' not loaded, access to DB12.DBW40", "FC_Recipe"),
            (14, "error", "16#4A02", "CPU changed from RUN to STOP: programming error without error OB", "CPU"),
            (13, "warning", "16#3A01", "I/O access error: module in slot 4 (DQ 16x24VDC) not reachable", "Rack 0"),
            (95, "info", "16#4301", "Download of block FC_Recipe completed", "CPU"),
            (96, "info", "16#4302", "CPU changed from STOP to RUN", "CPU"),
            (540, "info", "16#4300", "Power on, warm restart", "CPU")],
        timeline=[(48, "ok", "run", True), (24, "ok", "run", True), (6, "ok", "run", True), (1, "ok", "run", True)]),
    Scenario(
        "S7-1200 Packaging", "Siemens", "CPU 1214C DC/DC/DC", "10.250.1.12", 102, "Packaging", "Hall B, cabinet B3",
        "V4.5.2", "FS 03", "SIM-S7-0002",
        "A PROFINET drive keeps dropping out; the CPU still runs.",
        health="warning", latency_ms=8.9,
        facts={"Operating mode": "RUN", "CPU load": "34 %", "Cycle time (min/avg/max)": "1 / 3 / 11 ms",
               "PROFINET devices": "6 configured, 5 online", "Memory card": "SIMATIC MC 4 MB, 41 % used"},
        events=[
            (35, "warning", "16#3C02", "IO device 'drive-03' failed (PROFINET), station 5 not reachable", "PROFINET IO"),
            (205, "info", "16#3C03", "IO device 'drive-03' returned", "PROFINET IO"),
            (210, "warning", "16#3C02", "IO device 'drive-03' failed (PROFINET), station 5 not reachable", "PROFINET IO"),
            (800, "info", "16#4300", "Power on, warm restart", "CPU")],
        timeline=[(30, "ok", "run", True), (12, "warning", "run", True), (4, "ok", "run", True)]),
    Scenario(
        "S7-1500 Utilities DMZ", "Siemens", "CPU 1511-1 PN", "10.250.1.13", 102, "Utilities", "Hall C, cabinet C2",
        "V2.9.2", "FS 04", "SIM-S7-0003",
        "Repeated failed web-server logins from an unknown address and a lost time-server link.",
        health="warning", latency_ms=7.4,
        facts={"Operating mode": "RUN", "CPU load": "22 %", "Access level": "Full access protected",
               "Web server": "enabled, HTTPS", "Clock source": "NTP, last sync 3 h ago"},
        events=[
            (9, "warning", "SEC-401", "Login failed for user 'admin' from 10.250.9.77 (web server)", "Web server"),
            (10, "warning", "SEC-401", "Login failed for user 'admin' from 10.250.9.77 (web server)", "Web server"),
            (11, "warning", "SEC-401", "Login failed for user 'service' from 10.250.9.77 (web server)", "Web server"),
            (180, "warning", "NTP-12", "NTP server 10.250.0.5 not reachable, time sync lost", "Time sync"),
            (1500, "info", "16#4300", "Power on, warm restart", "CPU")],
        timeline=[(24, "ok", "run", True), (6, "warning", "run", True)]),
    Scenario(
        "S7-1500 Compressor House", "Siemens", "CPU 1513-1 PN", "10.250.1.14", 102, "Utilities", "Hall C, cabinet C5",
        "V2.8.1", "FS 04", "SIM-S7-0004",
        "Healthy controller, but on an older firmware (V2.8.1) than the others.",
        latency_ms=4.8,
        facts={"Operating mode": "RUN", "CPU load": "18 %", "Cycle time (min/avg/max)": "1 / 2 / 7 ms",
               "PROFINET devices": "4 configured, 4 online"},
        events=[(2400, "info", "16#4300", "Power on, warm restart", "CPU"),
                (2399, "info", "16#4302", "CPU changed from STOP to RUN", "CPU")],
        timeline=[(72, "ok", "run", True), (48, "ok", "run", True), (24, "ok", "run", True)]),
]
