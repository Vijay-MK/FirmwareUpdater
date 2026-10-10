"""AI (LLM) connection settings edited by an administrator in the UI.

Values saved here override the FWMGR_LLM_* environment variables. The API key is stored encrypted
(Fernet, same key file as device credentials) and is never sent back to the browser.
Settings are applied to the in-process `settings` object, so run a single server process (or
restart after changing them) if you use several workers.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AppSetting, utcnow
from ..security import decrypt_text, encrypt_text

KEYS = ("llm_base_url", "llm_model", "llm_api_key_enc", "llm_send_fleet_data", "llm_off")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,100}$")
API_KEY_RE = re.compile(r"^[\x21-\x7e]{1,500}$")      # printable, no spaces: cannot inject headers

_env_defaults: dict | None = None


def _remember_env() -> dict:
    global _env_defaults
    if _env_defaults is None:
        _env_defaults = {"llm_base_url": settings.llm_base_url, "llm_model": settings.llm_model,
                         "llm_api_key": settings.llm_api_key,
                         "llm_send_fleet_data": settings.llm_send_fleet_data}
    return _env_defaults


def validate_base_url(url: str) -> tuple[str, str | None]:
    """Returns (clean_url, error). https is required unless the host is local/private."""
    url = (url or "").strip()
    if not url or len(url) > 300:
        return "", "Enter the base URL, for example https://api.openai.com/v1 (max. 300 characters)."
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        return "", "The base URL is not valid."
    if parts.scheme not in ("http", "https") or not host:
        return "", "The base URL must start with https:// (or http:// for a local server)."
    if parts.username or parts.password or parts.query or parts.fragment:
        return "", "Do not put credentials, query strings or fragments in the base URL; use the API key field."
    if parts.scheme == "http":
        try:
            addr = ipaddress.ip_address(host)
            local = (addr.is_loopback or addr.is_private) and not addr.is_link_local
        except ValueError:
            local = host.lower() == "localhost"
        if not local:
            return "", ("Plain http is only accepted for localhost or a private IP address. "
                        "Use https:// for a cloud service, otherwise the API key travels unencrypted.")
    return url.rstrip("/"), None


def validate_model(model: str) -> tuple[str, str | None]:
    model = (model or "").strip()
    if not MODEL_RE.match(model):
        return "", "Enter the model name, for example gpt-4o-mini (letters, digits and . _ : / @ + - only)."
    return model, None


def _get(db: Session, key: str) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row else None


def _put(db: Session, key: str, value: str, who: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=who, updated_at=utcnow()))
    else:
        row.value, row.updated_by, row.updated_at = value, who, utcnow()


def _stored_key(db: Session) -> str:
    try:
        return decrypt_text(_get(db, "llm_api_key_enc"))
    except Exception:       # encryption.key was replaced: treat as "no key saved"
        return ""


def apply(db: Session) -> None:
    """Make the in-process settings reflect environment defaults plus the saved overrides."""
    env = _remember_env()
    stored = {k: _get(db, k) for k in KEYS}
    settings.llm_base_url = stored["llm_base_url"] if stored["llm_base_url"] is not None else env["llm_base_url"]
    settings.llm_model = stored["llm_model"] if stored["llm_model"] is not None else env["llm_model"]
    settings.llm_api_key = _stored_key(db) if stored["llm_api_key_enc"] else env["llm_api_key"]
    settings.llm_send_fleet_data = (stored["llm_send_fleet_data"] == "1"
                                    if stored["llm_send_fleet_data"] is not None else env["llm_send_fleet_data"])
    settings.llm_off = stored["llm_off"] == "1"


def save(db: Session, who: str, base_url: str, model: str, api_key: str, consent: bool,
         off: bool) -> str | None:
    """Validate and store. A blank api_key keeps the saved one. Returns an error message or None."""
    clean_url, error = validate_base_url(base_url)
    if error:
        return error
    clean_model, error = validate_model(model)
    if error:
        return error
    api_key = (api_key or "").strip()
    if api_key and not API_KEY_RE.match(api_key):
        return "The API key contains spaces or unusual characters. Paste only the key itself."
    _remember_env()
    _put(db, "llm_base_url", clean_url, who)
    _put(db, "llm_model", clean_model, who)
    if api_key:
        _put(db, "llm_api_key_enc", encrypt_text(api_key), who)
    _put(db, "llm_send_fleet_data", "1" if consent else "0", who)
    _put(db, "llm_off", "1" if off else "0", who)
    db.commit()
    apply(db)
    return None


def remove_key(db: Session) -> None:
    row = db.get(AppSetting, "llm_api_key_enc")
    if row is not None:
        db.delete(row)
        db.commit()
    apply(db)


def clear(db: Session) -> None:
    """Delete every saved override: the environment variables apply again."""
    for key in KEYS:
        row = db.get(AppSetting, key)
        if row is not None:
            db.delete(row)
    db.commit()
    apply(db)


def view(db: Session) -> dict:
    """What the settings page may show. Never contains the API key itself."""
    saved_key = bool(_get(db, "llm_api_key_enc")) and bool(_stored_key(db))
    row = db.get(AppSetting, "llm_base_url")
    return {"base_url": settings.llm_base_url, "model": settings.llm_model,
            "key_set": bool(settings.llm_api_key),
            "key_hint": settings.llm_api_key[-4:] if len(settings.llm_api_key) >= 12 else "",
            "key_source": "saved here" if saved_key else ("environment" if settings.llm_api_key else ""),
            "consent": settings.llm_send_fleet_data, "off": settings.llm_off,
            "enabled": settings.llm_enabled,
            "overridden": any(_get(db, k) is not None for k in KEYS),
            "updated_by": row.updated_by if row else "", "updated_at": row.updated_at if row else None}

