from .common import Scenario

ROCKWELL = [
    Scenario(
        "ControlLogix Line 2", "Rockwell", "1756-L83E", "10.250.2.21", 44818, "Line 2", "Hall A, cabinet A4",
        "33.011", "Rev A", "SIM-CLX-0001",
        "Controller faulted 22 min ago: an array index went out of range in the batch calculation.",
        run_state="stop", health="fault", latency_ms=9.1,
        facts={"Controller mode": "Faulted (program stopped)", "Major faults": "1", "Minor faults": "0",
               "Owned / configured": "yes / yes", "Memory used": "63 %", "Last download": "11 h ago"},
        events=[
            (22, "error", "Type 4, Code 31", "Major fault: array subscript too large in routine 'Calc_Batch'", "MainProgram"),
            (23, "info", "", "Controller mode changed from RUN to FAULTED", "Controller"),
            (660, "info", "", "Controller download completed", "Engineering workstation")],
        timeline=[(36, "ok", "run", True), (12, "ok", "run", True), (1, "ok", "run", True)]),
    Scenario(
        "CompactLogix Packing", "Rockwell", "5069-L320ER", "10.250.2.22", 44818, "Packaging", "Hall B, cabinet B1",
        "34.011", "Rev B", "SIM-CLX-0002",
        "An I/O module connection keeps timing out and the SD card is nearly full.",
        health="warning", latency_ms=11.7,
        facts={"Controller mode": "Run", "Minor faults": "2", "Memory used": "58 %",
               "SD card": "92 % full", "Connections used": "74 of 120"},
        events=[
            (50, "warning", "", "Connection request error to module 'Rack2:3' (RPI 10 ms), connection timed out", "I/O tree"),
            (300, "warning", "", "Controller storage 92 % full on SD card", "Memory"),
            (1200, "info", "", "Controller mode changed from PROGRAM to RUN", "Controller")],
        timeline=[(48, "ok", "run", True), (20, "warning", "run", True), (3, "warning", "run", True)]),
    Scenario(
        "Micro850 Pump Station", "Rockwell", "2080-LC50-48QWB", "10.250.2.23", 44818, "Water", "Pump house 2",
        "12.013", "Rev A", "SIM-MCR-0003",
        "Offline for 30 hours: the Ethernet link to the remote pump house is down.",
        run_state="unknown", health="unknown", online=False, latency_ms=0.0,
        notes=["Simulated device offline"],
        timeline=[(96, "ok", "run", True), (60, "ok", "run", True), (30, "ok", "run", True),
                  (12, "unknown", "unknown", False), (4, "unknown", "unknown", False)]),
]

MITSUBISHI = [
    Scenario(
        "iQ-R Assembly", "Mitsubishi", "R16CPU", "10.250.3.31", 5007, "Assembly", "Hall D, cabinet D1",
        "1.23", "Rev C", "SIM-IQR-0001",
        "Battery is low and a module at slot 3 does not match the registered configuration.",
        health="warning", latency_ms=7.8,
        facts={"CPU operating status": "RUN", "Scan time (max)": "2.4 ms", "Battery": "low (BAT LED on)",
               "Program memory used": "49 %", "Extension units": "1 base + 1 extension"},
        events=[
            (65, "warning", "", "Battery low: BAT LED on, retentive data at risk", "CPU"),
            (66, "warning", "2400H", "Module verify error: unit at slot 3 differs from registered configuration", "Base unit"),
            (4000, "info", "", "Power on, CPU started in RUN", "CPU")],
        timeline=[(72, "ok", "run", True), (30, "warning", "run", True), (5, "warning", "run", True)]),
    Scenario(
        "FX5U Conveyor", "Mitsubishi", "FX5U-32MT/ES", "10.250.3.32", 5007, "Logistics", "Hall E, cabinet E2",
        "1.280", "Rev B", "SIM-FX5-0002",
        "CPU stopped 8 min ago with a watchdog (scan time) error and the ERR LED on.",
        run_state="stop", health="fault", latency_ms=13.5,
        facts={"CPU operating status": "STOP (error)", "Scan time (max)": "212 ms",
               "Watchdog setting": "200 ms", "Program memory used": "88 %"},
        events=[
            (8, "error", "", "Watchdog timer error: scan time exceeded 200 ms", "CPU"),
            (8, "error", "", "ERR LED on, CPU stopped", "CPU"),
            (700, "info", "", "Remote RUN executed from engineering tool", "Engineering workstation")],
        timeline=[(24, "ok", "run", True), (3, "warning", "run", True)]),
    Scenario(
        "Q03UDE Press", "Mitsubishi", "Q03UDECPU", "10.250.3.33", 5007, "Press shop", "Hall E, cabinet E6",
        "A", "Rev D", "SIM-Q03-0003",
        "Healthy legacy controller, no events beyond normal start-up.",
        latency_ms=5.5,
        facts={"CPU operating status": "RUN", "Scan time (max)": "6.1 ms", "Battery": "OK"},
        events=[(5000, "info", "", "Power on, CPU started in RUN", "CPU")],
        timeline=[(48, "ok", "run", True), (24, "ok", "run", True)]),
]
