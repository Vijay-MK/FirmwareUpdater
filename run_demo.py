"""Start the web server together with the simulated PLC fleet, so you can try everything in the browser.

    .venv/bin/python run_demo.py                  # http://127.0.0.1:8000
    .venv/bin/python run_demo.py --port 8080
    .venv/bin/python run_demo.py --reset          # start again from an empty demo database
    .venv/bin/python run_demo.py --clear-on-exit  # remove the simulated devices when you press Ctrl+C

What happens, in order
  1. Uses a separate data directory (<project>/data_demo by default), so the simulated devices never mix with
     your real database. Use --data-dir to choose another one. FWMGR_DATABASE_URL is ignored for the same reason.
  2. Creates the database and the admin account if they do not exist yet.
  3. Creates or refreshes the simulated devices and their diagnostics history (tests/simulate_devices.py).
  4. Starts the server. Sign in as "admin"; open Devices, then a device, then Diagnostics.

The admin password is FWMGR_ADMIN_PASSWORD if you set it, otherwise a random one that is printed ONCE when the
demo database is created. If you lose it, run with --reset. All device data is invented; nothing touches the network.
"""
import argparse
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DEMO_DIR = ROOT / "data_demo"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the web server with a simulated PLC fleet.")
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default 127.0.0.1 = this computer only; 0.0.0.0 = whole network)")
    p.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    p.add_argument("--data-dir", type=Path, default=None,
                   help=f"where the demo database and files live (default {DEFAULT_DEMO_DIR})")
    p.add_argument("--reset", action="store_true",
                   help="delete the default demo data directory first (database, users, saved AI settings)")
    p.add_argument("--clear-on-exit", action="store_true",
                   help="remove the simulated devices again when the server stops")
    return p.parse_args(argv)


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("The port must be between 1 and 65535.")
        return 2
    if args.reset and args.data_dir is not None:
        # Never delete a directory the user chose: it might hold a real database.
        print("--reset only works with the default demo directory. Delete your own --data-dir by hand if you want that.")
        return 2
    if not port_is_free(args.host, args.port):
        print(f"Port {args.port} on {args.host} is already in use (is the server or another demo already running?).\n"
              f"Stop it or choose another port, for example: python run_demo.py --port {args.port + 1}")
        return 1

    data_dir = (args.data_dir or DEFAULT_DEMO_DIR).resolve()
    if args.reset and data_dir.exists():
        shutil.rmtree(data_dir)
        print(f"Removed {data_dir}")

    # The app reads its settings when it is imported, so set the environment first.
    os.environ["FWMGR_DATA_DIR"] = str(data_dir)
    if os.environ.pop("FWMGR_DATABASE_URL", None):
        print("Ignoring FWMGR_DATABASE_URL: the demo always uses its own database.")
    os.environ.setdefault("FWMGR_ENABLE_SCHEDULER", "0")     # no background firmware-feed checks in a demo
    sys.path.insert(0, str(ROOT))

    import uvicorn

    from app.config import settings
    from app.database import SessionLocal, init_db
    from app.drivers import simulator
    from app.main import app, bootstrap_admin
    from tests import simulate_devices as sim

    init_db()
    bootstrap_admin()        # prints the generated admin password once, if the account is new
    with SessionLocal() as db:
        devices = sim.seed(db)
    print(f"\nSimulated {len(devices)} PLCs (database: {settings.database_url}, state: {simulator.state_file()})")

    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"\n  Open  http://{shown}:{args.port}   and sign in as 'admin'.")
    print("  Then: Devices > pick a simulated device > Diagnostics > 'Run diagnostics now'.")
    print("  Stop with Ctrl+C.\n")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("  WARNING: the demo is reachable from the network. Use a strong FWMGR_ADMIN_PASSWORD.\n")

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if args.clear_on_exit:
            with SessionLocal() as db:
                print(f"Removed {sim.clear(db)} simulated devices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
