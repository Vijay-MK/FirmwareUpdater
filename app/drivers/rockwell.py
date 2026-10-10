"""Rockwell / Allen-Bradley: reads the CIP Identity via EtherNet/IP ListIdentity (TCP 44818)."""
import socket
import struct

from .base import BaseDriver, DeviceInfo
from .diag import DiagEvent, DiagReport

LIST_IDENTITY = 0x0063


def build_list_identity() -> bytes:
    # command, length, session handle, status, sender context (8), options
    return struct.pack("<HHII8sI", LIST_IDENTITY, 0, 0, 0, b"fwmgr000", 0)


def parse_list_identity(data: bytes) -> dict:
    """Parse an EtherNet/IP ListIdentity reply. Raises ValueError on malformed data."""
    if len(data) < 24 + 2:
        raise ValueError("reply too short")
    command, length, _session, status = struct.unpack_from("<HHII", data, 0)
    if command != LIST_IDENTITY or status != 0:
        raise ValueError("unexpected encapsulation reply")
    body = data[24:24 + length]
    (count,) = struct.unpack_from("<H", body, 0)
    if count < 1:
        raise ValueError("no identity items")
    item_type, item_len = struct.unpack_from("<HH", body, 2)
    if item_type != 0x000C:
        raise ValueError("unexpected item type")
    item = body[6:6 + item_len]
    # version(2) sockaddr(16) vendor(2) devtype(2) product code(2) rev maj(1) rev min(1)
    # status(2) serial(4) name len(1) name state(1)
    offset = 2 + 16
    vendor_id, device_type, product_code, rev_major, rev_minor, _st, serial = struct.unpack_from(
        "<HHHBBHI", item, offset)
    offset += 14
    name_len = item[offset]
    name = item[offset + 1: offset + 1 + name_len].decode("ascii", errors="replace")
    state_pos = offset + 1 + name_len
    state = item[state_pos] if state_pos < len(item) else None
    return {"vendor_id": vendor_id, "device_type": device_type, "product_code": product_code,
            "firmware_version": f"{rev_major}.{rev_minor:03d}", "serial_number": f"{serial:08X}",
            "model": name, "status": _st, "state": state}


# CIP Identity "Status" word, bits 8-11 (Volume 1, Identity Object).
_FAULT_BITS = ((8, "Minor recoverable fault", "warning"), (9, "Minor unrecoverable fault", "warning"),
               (10, "Major recoverable fault", "error"), (11, "Major unrecoverable fault", "error"))
_STATES = {0: "Nonexistent", 1: "Device self-testing", 2: "Standby", 3: "Operational",
           4: "Major recoverable fault", 5: "Major unrecoverable fault"}


class RockwellDriver(BaseDriver):
    name = "rockwell"
    vendor = "Rockwell"
    reads_version = True
    _ident: dict | None = None
    notes = ("Reads firmware revision, serial and product name via CIP Identity (EtherNet/IP). "
             "Flashing: configure ControlFLASH Plus / vendor CLI via FWMGR_UPDATE_CMD_ROCKWELL.")

    def collect_details(self, report: DiagReport) -> None:
        ident = self._ident
        if not ident:
            report.notes.append("CIP identity could not be read, so no device status is available.")
            return
        status = ident["status"]
        report.facts["CIP vendor ID / product code"] = f"{ident['vendor_id']} / {ident['product_code']}"
        report.facts["CIP device type"] = str(ident["device_type"])
        report.facts["CIP status word"] = f"0x{status:04X}"
        report.facts["Owned (connected to a controller)"] = "yes" if status & 0x1 else "no"
        report.facts["Configured"] = "yes" if status & 0x4 else "no"
        if ident["state"] is not None:
            report.facts["CIP device state"] = _STATES.get(ident["state"], str(ident["state"]))
        for bit, text, severity in _FAULT_BITS:
            if status & (1 << bit):
                report.events.append(DiagEvent(severity=severity, code=f"status bit {bit}", message=text,
                                               source="CIP Identity"))
        severities = {e.severity for e in report.events}
        report.health = "fault" if "error" in severities else "warning" if "warning" in severities else "ok"
        report.notes.append("RUN/PROGRAM mode and the controller fault log are not available through the "
                            "identity query; use Studio 5000 or FWMGR_DIAG_CMD_ROCKWELL for them.")

    def probe(self) -> DeviceInfo:
        self._ident = None
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(build_list_identity())
                data = b""
                while len(data) < 24:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                if len(data) >= 24:
                    (length,) = struct.unpack_from("<H", data, 2)
                    while len(data) < 24 + length:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        data += chunk
        except OSError as exc:
            return DeviceInfo(reachable=False, detail=f"Not reachable: {exc}")
        try:
            ident = parse_list_identity(data)
        except (ValueError, struct.error) as exc:
            return DeviceInfo(reachable=True, detail=f"Port open but identity not readable: {exc}")
        self._ident = ident
        return DeviceInfo(reachable=True, firmware_version=ident["firmware_version"],
                          serial_number=ident["serial_number"], model=ident["model"],
                          detail="CIP identity read")
