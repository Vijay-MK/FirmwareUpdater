from ..security import decrypt_json
from .base import BaseDriver, DeviceInfo, DriverError, NotSupported
from .rockwell import RockwellDriver
from .simulator import SimulatorDriver


def _generic(name: str, vendor: str, notes: str) -> type[BaseDriver]:
    return type(f"{vendor}Driver", (BaseDriver,), {"name": name, "vendor": vendor, "notes": notes})


DRIVERS: dict[str, type[BaseDriver]] = {
    "siemens": _generic("siemens", "Siemens",
                        "Reachability (S7comm port 102). Flash via TIA Portal / vendor CLI "
                        "set in FWMGR_UPDATE_CMD_SIEMENS. Enter the firmware version manually."),
    "rockwell": RockwellDriver,
    "mitsubishi": _generic("mitsubishi", "Mitsubishi",
                           "Reachability (SLMP port). Flash via GX Works3 / vendor tool set in "
                           "FWMGR_UPDATE_CMD_MITSUBISHI. Enter the firmware version manually."),
    "abb": _generic("abb", "ABB",
                    "Reachability (Modbus TCP). Flash via Automation Builder / vendor tool set in "
                    "FWMGR_UPDATE_CMD_ABB. Enter the firmware version manually."),
    "delta": _generic("delta", "Delta",
                      "Reachability (Modbus TCP). Flash via DIAdesigner / vendor tool set in "
                      "FWMGR_UPDATE_CMD_DELTA. Enter the firmware version manually."),
    "wago": _generic("wago", "WAGO",
                     "Reachability (Modbus TCP). Flash via WAGO tooling set in "
                     "FWMGR_UPDATE_CMD_WAGO. Enter the firmware version manually."),
    "simulator": SimulatorDriver,
}


def driver_for(device) -> BaseDriver:
    cls = DRIVERS.get(device.driver)
    if cls is None:
        raise DriverError(f"Unknown driver: {device.driver}")
    return cls(device.ip_address, device.port, decrypt_json(device.credentials_enc),
               device.firmware_version)


__all__ = ["DRIVERS", "driver_for", "BaseDriver", "DeviceInfo", "DriverError", "NotSupported"]
