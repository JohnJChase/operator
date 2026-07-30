# Diagnostic console

LAN browser for chart + plant (Phase A) and inbox / directory / streams (Phase B).
The telephone FSM stays authoritative for call control. Console never injects
synthetic dial digits into the pulse decoder.

## Enable

Set in `.env` (see `.env.example`):

```
OPERATOR_CONSOLE_PASSWORD=…   # required to start the server
OPERATOR_CONSOLE_PORT=8788
OPERATOR_CONSOLE_BIND=0.0.0.0
```

Restart `operator-os`. Logs show `console: http://…`. Without a password the
console stays off.

## Access

- Local/LAN: `http://<pi-ip>:8788/`
- Remote: Tailscale to the Pi (do **not** expose with Funnel / public ingress).
- Login sets an HttpOnly session cookie. API JSON never includes secrets.
- Tabs: **Plant** (chart/menu/log), **Messages**, **Directory**, **Streams**.
  Lists scroll inside the tab so the page stays one viewport tall.

## Phase A — plant diagnostics

| Area | Content |
|------|---------|
| Readiness | `READY` / `DEGRADED` |
| Plant chart | States + edges; current state highlighted |
| Patch | Live `plant.snapshot()` |
| Menu guide | Digit tree (labels follow streams map) |
| I/O strip | Hook, last digit, last-10, outside buffer, ring, SIP |
| Activity | EventLog tail |
| Ring test | ~1.5s via phone API |

## Phase B — inbox, directory, streams

### Message tickets

- Lists recent SMS + voicemail from SQLite; names from the directory when known.
- Play VM WAV in-browser; mark heard; delete.
- **Reply:** draft in prompt → confirm → `send_sms` (same path as tools).
- **Call:** on-hook → rings WE302 then SIP after pickup; off-hook → places through
  the chart immediately (same `/api/place-call` queue).
- Desktop companions may call the same `/api/inbox*` and `/api/place-call` routes
  with `OPERATOR_DESKTOP_TOKEN` (see [desktop-bridge.md](desktop-bridge.md)).

### Directory (phonebook)

| Field | Use |
|-------|-----|
| name | Inbound announce + console dial-by-name |
| e164 | Normalized NANP / E.164 |
| short_code | After digit 9, dial the code instead of a full number |
| notes | Free text |

Inbound SMS/VM speech uses the contact **name** when the number matches.
Outbound: digit **9** → short code → same `place_call` path; or console **Call** /
**Call by name**.

### Entertainment streams

Digits **3** and **4** read `data/streams.yaml` (defaults = WAMU / NWS). Edit on
the Streams tab and **Validate & save** — each URL is HTTP-probed (playlist
resolve + ranged GET) before write so a dead link never reaches the phone.

## Controllable vs not

**Allowed:** login/logout, ring test, inbox mark/delete/reply (confirmed),
directory CRUD, streams save, confirmed place-call (off-hook + chart states).

**Not allowed:** bypassing the chart for SIP seize, forging pulse digits, public
ingress without Tailscale.

## Later backlog

Favorites / call-back last, SMS compose with picker, optional VM transcript,
stream presets + custom slot, phonebook import/export JSON.
