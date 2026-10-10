"""Password hashing, secret management and at-rest encryption of device credentials."""
import json
import os
import secrets
import time
from collections import defaultdict

import bcrypt
from cryptography.fernet import Fernet

from .config import settings

MAX_PASSWORD_BYTES = 72  # bcrypt limit
MIN_PASSWORD_LEN = 10


def validate_password(password: str) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes."
    return None


def generate_initial_password() -> str:
    return secrets.token_urlsafe(14)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False


def _load_or_create(name: str, factory, env_name: str = "") -> bytes:
    # A key given in the environment wins over the key file. Needed on hosts without a persistent
    # disk (e.g. Vercel), where every server instance would otherwise create its own key.
    from_env = os.environ.get(env_name, "").strip() if env_name else ""
    if from_env:
        return from_env.encode()
    path = settings.data_dir / name
    if path.exists():
        return path.read_bytes().strip()
    value = factory()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(value)
    return value


_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create("encryption.key", Fernet.generate_key, "FWMGR_ENCRYPTION_KEY"))
    return _fernet


def session_secret() -> str:
    return _load_or_create("session.key", lambda: secrets.token_hex(32).encode(), "FWMGR_SESSION_SECRET").decode()


def encrypt_json(data: dict) -> str:
    return fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> dict:
    if not token:
        return {}
    return json.loads(fernet().decrypt(token.encode()).decode())


def encrypt_text(text: str) -> str:
    return fernet().encrypt(text.encode()).decode()


def decrypt_text(token: str | None) -> str:
    return fernet().decrypt(token.encode()).decode() if token else ""


class LoginThrottle:
    """Very small in-memory brute-force protection (per username+client)."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str) -> list[float]:
        now = time.monotonic()
        self._failures[key] = [t for t in self._failures[key] if now - t < self.window]
        return self._failures[key]

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max_failures

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


login_throttle = LoginThrottle()
