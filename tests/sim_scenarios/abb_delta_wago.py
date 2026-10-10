from .common import Scenario

ABB = [
    Scenario(
        "AC500 Mixer", "ABB", "PM5650-2ETH", "10.250.4.41", 502, "Process", "Hall F, cabinet F1",
        "3.1.4", "Rev B", "SIM-AC5-0001",
        "An I/O bus module stopped answering and the SD card is missing.",
        health="warning", latency_ms=10.4,
        facts={"CPU state": "RUN", "Cycle time (max)": "9 ms", "I/O bus modules": "8 configured, 7 responding",
               "SD card": "not detected"},
        events=[
            (30, "warning", "", "Diagnosis message: I/O bus module 3 not responding, retries exceeded", "I/O bus"),
            (31, "warning", "", "SD card not detected, boot project cannot be updated", "SD card"),
            (3000, "info", "", "CPU started, boot project loaded", "CPU")],
        timeline=[(36, "ok", "run", True), (10, "warning", "run", True)]),
    Scenario(
        "AC500 Water Treatment", "ABB", "PM564-ETH", "10.250.4.42", 502, "Water", "Treatment plant",
        "2.8.2", "Rev A", "SIM-AC5-0002",
        "Healthy controller.",
        latency_ms=6.0,
        facts={"CPU state": "RUN", "Cycle time (max)": "4 ms", "I/O bus modules": "5 configured, 5 responding"},
        events=[(6000, "info", "", "CPU started, boot project loaded", "CPU")],
        timeline=[(48, "ok", "run", True), (24, "ok", "run", True)]),
]

DELTA = [
    Scenario(
        "AS332T Furnace", "Delta", "AS332T-A", "10.250.5.51", 502, "Heat treatment", "Hall G, cabinet G1",
        "2.14", "Rev A", "SIM-AS3-0001",
        "CPU stopped 5 min ago with a scan-timeout error and the ERROR LED on.",
        run_state="stop", health="fault", latency_ms=14.2,
        facts={"CPU state": "STOP (error)", "Scan time (max)": "310 ms", "Program memory used": "76 %"},
        events=[
            (5, "error", "D1004", "ERROR LED on: scan timeout, special register D1004 set", "CPU"),
            (5, "info", "", "Operating mode changed to STOP", "CPU")],
        timeline=[(24, "ok", "run", True), (2, "ok", "run", True)]),
    Scenario(
        "DVP28SV Dosing", "Delta", "DVP28SV11T2", "10.250.5.52", 502, "Dosing", "Hall G, cabinet G4",
        "1.62", "Rev B", "SIM-DVP-0002",
        "RTC battery is low and the controller logged an event that is not in the knowledge base.",
        health="warning", latency_ms=12.0,
        facts={"CPU state": "RUN", "Scan time (max)": "14 ms", "RTC": "ok, battery low"},
        events=[
            (120, "warning", "", "RTC battery low, clock may reset on power loss", "RTC"),
            (125, "warning", "0x7F31", "Internal diagnostic event, no description available", "CPU")],
        timeline=[(48, "ok", "run", True), (6, "warning", "run", True)],
        expect_unrecognised=True),
]

WAGO = [
    Scenario(
        "PFC200 Boiler House", "WAGO", "750-8212", "10.250.6.61", 502, "Utilities", "Boiler house",
        "03.09.07", "Rev 02", "SIM-PFC-0001",
        "CPU temperature is high and the SD card is nearly full.",
        health="warning", latency_ms=9.6,
        facts={"CODESYS runtime": "running", "CPU temperature": "78 C", "CPU load": "57 %",
               "RAM used": "64 %", "SD card": "93 % full"},
        events=[
            (18, "warning", "", "CPU temperature 78 C above warning threshold 75 C", "System monitor"),
            (260, "warning", "", "Storage /media/sd 93 % full", "File system"),
            (1500, "info", "", "Runtime started, application loaded", "CODESYS")],
        timeline=[(36, "ok", "run", True), (12, "warning", "run", True)]),
    Scenario(
        "750-881 Packing Hall Coupler", "WAGO", "750-881", "10.250.6.62", 502, "Packaging", "Hall B, cabinet B7",
        "03.08.04", "Rev 01", "SIM-881-0002",
        "A terminal at position 5 is missing: the I/O-CHECK blink code shows an I/O bus fault.",
        health="fault", latency_ms=8.3,
        facts={"CODESYS runtime": "running", "I/O modules": "12 configured, 11 detected", "I/O LED": "red blinking 6/3"},
        events=[
            (3, "error", "", "I/O-CHECK: terminal at position 5 missing, blink code 6/3", "I/O bus"),
            (2000, "info", "", "Runtime started, application loaded", "CODESYS")],
        timeline=[(24, "ok", "run", True), (1, "ok", "run", True)]),
]
