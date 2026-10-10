"""Vendor-specific hints for ABB, Delta and WAGO. General guidance, verify in the vendor manual."""
from . import Rule

RULES = [
    Rule("abb_ac500", "ABB: AC500 / Automation Builder diagnostic",
         r"\bPM5\d\d\b|\bPM[0-9]{3}\b|\bFBP\b|\bCM5\d\d\b|\bdiag(nosis)? (code|message)\b|error class|\bE[0-9]{4}\b|"
         r"cpu (halted|error|stopped)|\bsystem error\b",
         causes=("The CPU reports a system or I/O diagnostic (error class, error code) via the diagnosis system.",
                 "A communication module or I/O module on the terminal base failed or lost power."),
         steps=("Open Automation Builder, go online and read the PLC Diagnosis messages for the error class and code.",
                "Check the bus modules and the terminal base connection, and the status LEDs (ERR, RUN).",
                "Look up the code in the AC500 diagnosis manual.",
                "Clear the error and start the CPU only after the cause is fixed."),
         vendors=("abb",)),
    Rule("abb_sd_card", "ABB: SD card / project issue",
         r"sd card|project (not|missing|invalid)|boot project|no program",
         causes=("The SD card is missing or defective, or no boot project is stored in the CPU.",),
         steps=("Check the SD card and replace it if needed (use ABB-approved cards).",
                "Re-load the boot project and create the boot project in the CPU.",
                "Keep a current backup of the project outside the PLC."),
         vendors=("abb",)),
    Rule("delta_dvp_ah", "Delta: CPU error / ERROR LED",
         r"\bERROR LED\b|\bD1004\b|\bD10[0-9]{2}\b|\bM10[0-9]{2}\b|scan (timeout|over)|"
         r"program (check|memory)|ISPSoft|\bAS[0-9]{2}\b|\bAH[0-9]{3}\b|\bDVP\b",
         causes=("The CPU raised an error flag (the special registers hold the error code).",
                 "The program, scan time or memory is invalid."),
         steps=("In ISPSoft / WPLSoft read the error code and the special registers (for example D1004 / D1008).",
                "Compare the code with the CPU manual of your series (DVP, AS, AH).",
                "Fix the program or the hardware setting and clear the error flag."),
         vendors=("delta",)),
    Rule("wago_error", "WAGO: I/O check / error code",
         r"\bI/O ?check\b|\berr(or)? code\b|blink code|\bIO-?LED\b|\bCODESYS\b|PFC ?(200|100)|bus (error|end)|"
         r"coupler|\b750-\d+\b|terminal",
         causes=("The coupler or controller flashes an error code through the I/O LED (blink code).",
                 "A terminal in the node is missing, defective or does not match the configuration."),
         steps=("Count the blink code of the I/O LED (first the code, then the argument) and read it in the manual.",
                "Check the node in WAGO-I/O-CHECK and compare the real station with the configuration.",
                "Replace or re-seat the terminal; check for a missing end module.",
                "Use the web-based management of the PFC for log files and the CODESYS runtime state."),
         vendors=("wago",)),
]
