"""Application settings, read from environment variables (prefix FWMGR_)."""
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

VENDORS = ["Siemens", "Rockwell", "Mitsubishi", "ABB", "Delta", "WAGO"]

# Default TCP ports used for reachability checks. All are editable per device.
DEFAULT_PORTS = {
    "Siemens": 102,      # S7comm / ISO-on-TCP
    "Rockwell": 44818,   # EtherNet/IP
    "Mitsubishi": 5007,  # SLMP (port is user-configurable on the PLC)
    "ABB": 502,          # Modbus TCP
    "Delta": 502,        # Modbus TCP
    "WAGO": 502,         # Modbus TCP
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"FWMGR_{name}", default)


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))))
    database_url: str = ""
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))
    https_only: bool = field(default_factory=lambda: _bool("HTTPS_ONLY"))
    enable_scheduler: bool = field(default_factory=lambda: _bool("ENABLE_SCHEDULER", True))
    check_interval_minutes: int = field(default_factory=lambda: int(_env("CHECK_INTERVAL_MINUTES", "360")))
    allow_self_approval: bool = field(default_factory=lambda: _bool("ALLOW_SELF_APPROVAL"))
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "1024")))
    # Comma separated CIDRs; devices may only be added inside these. Empty = any address.
    allowed_networks: str = field(default_factory=lambda: _env("ALLOWED_NETWORKS"))
    # Optional OpenAI-compatible LLM endpoint (e.g. a local Ollama / vLLM server).
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    # Fleet data (device names / versions) is only sent to the LLM if this is enabled.
    llm_send_fleet_data: bool = field(default_factory=lambda: _bool("LLM_SEND_FLEET_DATA"))
    # Switched on by an administrator in the UI ("AI settings") to disable every LLM call.
    llm_off: bool = False
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "25")))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", "firmware-manager@localhost"))
    smtp_to: str = field(default_factory=lambda: _env("SMTP_TO"))

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.firmware_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = _env("DATABASE_URL", f"sqlite:///{self.data_dir / 'fwmgr.db'}")

    @property
    def firmware_dir(self) -> Path:
        return self.data_dir / "firmware"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def networks(self):
        return [ipaddress.ip_network(n.strip(), strict=False)
                for n in self.allowed_networks.split(",") if n.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model) and not self.llm_off

    def update_command(self, vendor: str) -> str:
        """Command template for an external vendor flashing tool, e.g.
        FWMGR_UPDATE_CMD_ROCKWELL='controlflash-cli --ip {host} --file {file}'"""
        return _env(f"UPDATE_CMD_{vendor.upper()}")

    def diag_command(self, vendor: str) -> str:
        """Read-only command that prints diagnostics as JSON, e.g.
        FWMGR_DIAG_CMD_SIEMENS='python3 /opt/tools/s7_diag.py {host}'"""
        return _env(f"DIAG_CMD_{vendor.upper()}")


settings = Settings()
