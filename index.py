"""Vercel entrypoint: the web app with the simulated PLC fleet (demo only, all data is invented).

Vercel functions have a read-only disk except /tmp, which is wiped on cold starts, so every instance
builds its own throw-away database and seeds the simulated devices at start-up. Changes made in the
UI (new users, settings) are lost when the instance is recycled.

Required Vercel environment variable:  FWMGR_ADMIN_PASSWORD  (at least 10 characters; sign in as "admin")
The session and encryption keys are derived from it, so every instance accepts the same login cookie.
"""
import base64
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

password = os.environ.get("FWMGR_ADMIN_PASSWORD", "").strip()
if len(password) < 10:
    raise RuntimeError("Set the FWMGR_ADMIN_PASSWORD environment variable (at least 10 characters) in the "
                       "Vercel project settings.")


def _derive(label: str) -> bytes:
    return hashlib.sha256(f"fwmgr-demo:{label}:{password}".encode()).digest()


os.environ["FWMGR_DATA_DIR"] = os.environ.get("FWMGR_DATA_DIR") or "/tmp/fwmgr"
os.environ["FWMGR_ENABLE_SCHEDULER"] = "0"          # no background jobs in a serverless function
os.environ.setdefault("FWMGR_HTTPS_ONLY", "1")      # Vercel always serves over HTTPS
os.environ.pop("FWMGR_DATABASE_URL", None)          # the demo always uses its own SQLite file
os.environ.setdefault("FWMGR_SESSION_SECRET", _derive("session").hex())
os.environ.setdefault("FWMGR_ENCRYPTION_KEY", base64.urlsafe_b64encode(_derive("fernet")).decode())

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app, bootstrap_admin  # noqa: E402  (Vercel looks for the name "app")
from tests import simulate_devices as sim  # noqa: E402

init_db()
bootstrap_admin()
with SessionLocal() as _db:
    sim.seed(_db, log=lambda *_: None)
