# PLC Firmware Manager

On-premise web application to inventory industrial PLCs, track the latest firmware, get notified,
and run controlled, approved firmware updates. Vendors: Siemens, Rockwell, Mitsubishi, ABB, Delta, WAGO.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
FWMGR_ADMIN_PASSWORD='choose-a-strong-password' .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://SERVER:8000 and sign in as `admin`. If `FWMGR_ADMIN_PASSWORD` is not set, a random password is
printed once on the server console at first start. Behind a TLS reverse proxy set `FWMGR_HTTPS_ONLY=1`.
Tests: `.venv/bin/python -m pytest`.

## What works today, and what does not (please read)

| Capability | Status |
|---|---|
| Device inventory (add/edit/delete, groups, search, encrypted credentials) | Implemented |
| Users, roles (viewer < engineer < approver < admin), audit log, CSRF, login throttling | Implemented |
| Firmware catalog: manual add with notes + vendor SHA-256, package upload, JSON feeds, scheduled checks | Implemented |
| Notifications (in-app, optional email) when a newer matching release exists | Implemented |
| AI release-note analysis (risk, urgency, CVEs) and fleet Q&A | Implemented, advisory only |
| Update workflow: request, four-eyes approval, pre-flight, flash, verify, full log | Implemented and tested with the simulator driver |
| Device diagnostics page (state, health, details, diagnostic buffer, history) | Implemented. Native data only for Rockwell (CIP status) and the simulator; other vendors need a tool (see below) |
| Read firmware version from the device | **Rockwell only** (CIP Identity). Other vendors: enter it manually |
| Flash a real PLC | **Not built in.** Needs a vendor tool command per vendor (see below) |
| Automatic download from vendor portals | **Not implemented.** No public APIs exist; use manual import or a JSON feed |

**Not tested against real hardware.** The Rockwell parser is tested on synthetic packets only. Validate on a
non-production PLC before any real use.

### Flashing real PLCs
For each vendor, the admin sets an environment variable with the command of the vendor's own CLI tool
(ControlFLASH Plus, TIA Portal / Openness scripts, GX Works3 tooling, Automation Builder, DIAdesigner, WAGO tools):

```
FWMGR_UPDATE_CMD_ROCKWELL="/opt/tools/flash-cli --host {host} --file {file}"
```
Placeholders: `{host} {port} {file} {version}`. The command runs without a shell. Device credentials are passed in the
environment variables `FWMGR_DEV_USER` / `FWMGR_DEV_PASSWORD`, never on the command line. Without this variable an
update for that vendor stops at pre-flight with a clear message. For drivers that cannot read the version back the job
ends in `needs_verification` and an engineer confirms the version read on the device.

### Device diagnostics
Open a device and click **Diagnostics**. Engineers can run a collection; viewers can read stored results. Each run is
saved as a snapshot (last 50 per device) and shows connection, response time, run state, health, device details and the
diagnostic buffer (filterable by severity). Collection is read-only and is blocked while an update is running.

What is real today: reachability and response time for all vendors; for Rockwell the CIP identity status word
(owned/configured, minor/major faults, device state). Rockwell RUN/PROGRAM mode and the controller fault log, and the
diagnostic buffer of Siemens, Mitsubishi, ABB, Delta and WAGO, need vendor protocols this tool does not implement.
Where a value is unavailable the page says so (run state shows `unknown`, never a guess).

To get the full buffer, set a read-only command per vendor that prints JSON to stdout:
```
FWMGR_DIAG_CMD_SIEMENS="python3 /opt/tools/s7_diag.py {host}"      # placeholders: {host} {port}
```
```json
{"run_state": "run|stop|unknown", "health": "ok|warning|fault|unknown", "facts": {"CPU temperature": "41 C"},
 "events": [{"timestamp": "2025-01-31 10:15:02", "severity": "error|warning|info", "code": "16#02:4E",
             "message": "...", "source": "Rack 0"}]}
```
The command runs without a shell with a 60 s timeout; credentials come in `FWMGR_DEV_USER` / `FWMGR_DEV_PASSWORD`.
The output is validated and capped (500 events, 50 facts) and always HTML-escaped when shown. You write or choose the
tool (for example a script around your vendor's SDK or a library such as python-snap7); it is not bundled.

### Help: what to check and how to fix
Below the diagnostic buffer the page lists the warnings and errors as grouped issues. Each has probable causes, steps
to take, a caution where relevant, and a note when to escalate to the vendor or the controls engineer.
- **Built-in knowledge base** (`app/knowledge/`, 21 rules, vendor-specific rules first). It matches event text and codes
  with regular expressions, offline. It is general guidance written for this tool, **not a copy of vendor manuals**:
  code ranges and LED names differ per model and firmware, so always confirm in the vendor documentation. Events it
  cannot match show as "Event not in the built-in knowledge base". It never changes an event's severity.
- **Explain with AI** (optional, engineers only). Needs an LLM configured on the admin **AI settings** tab (see below)
  with consent to send diagnostic text. Only vendor, model, firmware, run state and event text are sent; never the device
  name, IP address, serial number or location. Event text is treated as untrusted data in the prompt. The answer is
  validated, stored per snapshot, shown escaped, audited, and labelled advisory. It never triggers any action.

### AI settings tab (admin): use a cloud AI service
Admins open **AI settings** in the top menu and enter the **base URL**, the **model** and the **API key** of any
OpenAI-compatible service (for example `https://api.openai.com/v1` with `gpt-4o-mini`; OpenRouter, Groq, Azure OpenAI
or a local Ollama / vLLM server also work). Then tick the consent box, **Save**, and use **Test connection**.
- The same connection is used for Explain with AI, the release-note analysis and the AI assistant.
- The API key is stored **encrypted** (same `encryption.key` as device credentials), is never shown again (only its last 4
  characters) and is never written to the audit log. Leave the field blank to keep the saved key.
- Plain `http://` is accepted only for localhost or private addresses; a cloud service needs `https://`.
  Credentials, query strings and fragments in the URL are rejected.
- The consent box is required for Diagnostics explanations and the assistant. **With a cloud service this text leaves your
  network** (see what is sent above). The release-note analysis only sends public release-note text.
- **Switch the AI off** keeps the settings but stops all requests. **Delete all saved AI settings** makes the
  `FWMGR_LLM_*` environment variables apply again. Saved settings override the environment variables.
- Changes are applied in the running process at once and are audited. If you run several worker processes, restart
  them after changing the settings. The server needs outbound HTTPS access to the provider (firewall / proxy).
- Use a provider key with a spending limit. Admins can replace or remove the key but cannot read it back.

### Simulated devices for demos and testing
**Quickest demo: one command starts the server and the simulation together.**
```
.venv/bin/python run_demo.py            # then open http://localhost:8000 and sign in as admin
.venv/bin/python run_demo.py --reset    # start again from an empty demo database
```
It uses its own data directory (`data_demo/`), so the invented devices never mix with your real database. Set
`FWMGR_ADMIN_PASSWORD` to choose the admin password, otherwise a random one is printed once when the demo database is
created. Options: `--port`, `--host` (default `127.0.0.1`, this computer only), `--data-dir`, `--clear-on-exit`. Stop
with Ctrl+C. The AI settings tab works here too (saved settings stay in `data_demo/`).

`tests/simulate_devices.py` fills the app with 16 invented PLCs (all six vendors; healthy, warning, fault/STOP and
offline; with a backdated diagnostics history), so you can try Diagnostics, the help section and the AI button
without hardware:
```
.venv/bin/python -m tests.simulate_devices seed    # create or refresh (safe to repeat)
.venv/bin/python -m tests.simulate_devices list    # show devices and last health
.venv/bin/python -m tests.simulate_devices clear   # remove only the simulated devices
```
It uses the same `FWMGR_DATA_DIR` / `FWMGR_DATABASE_URL` as the app, and the server can keep running. Devices use the
`simulator` driver, have notes starting with `[SIMULATED]`, serials `SIM-...` and addresses `10.250.x.x`. The live
state is written to `<data dir>/simulator_state.json`; the server reloads it when the file changes, so **Run
diagnostics** in the UI returns the seeded picture. A real device on the same IP and port is never overwritten or
removed. The scenarios are in `tests/sim_scenarios/`; the event codes imitate each vendor's style and are invented.


### Update safety rules (enforced server-side)
Upgrade only (no downgrade); vendor/model must match; backup and maintenance window confirmed; a different person
with the approver role approves (`FWMGR_ALLOW_SELF_APPROVAL=1` to relax); package re-hashed before flashing;
unverified hashes need an explicit approver acknowledgement; PLC must be reachable and not in RUN; device version
must match inventory; one active job per device.

### Feed format
```json
{"releases": [{"version": "4.2.1", "model_pattern": "S7-1500*", "release_date": "2025-01-31",
  "release_notes": "...", "source_url": "https://...", "download_url": "https://...", "sha256": "<hex>"}]}
```
A download is only accepted if it matches the published `sha256`.

## Configuration (environment variables, prefix `FWMGR_`)
`DATA_DIR`, `DATABASE_URL`, `ADMIN_PASSWORD`, `HTTPS_ONLY`, `ALLOWED_NETWORKS` (CIDR list devices must be in),
`CHECK_INTERVAL_MINUTES`, `MAX_UPLOAD_MB`, `ALLOW_SELF_APPROVAL`, `SMTP_HOST/PORT/FROM/TO`,
`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` (any OpenAI-compatible endpoint, e.g. a local Ollama),
`LLM_SEND_FLEET_DATA` (needed before the Q&A assistant sees device names/versions).
Without an LLM, a keyword heuristic provides the analysis. Only public release-note text is sent to the LLM.

## Security notes
Keys (`encryption.key`, `session.key`) are created in the data directory with mode 600; back them up with the
database, since credentials cannot be decrypted without them. Place the app on a management network, behind TLS.
There is no built-in TLS or SSO. The tool is not a substitute for your change-management process.
