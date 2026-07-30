# Desktop bridge

The desktop bridge lets a Mac join the Operator exchange as a client. The Mac
opens an outbound SSE connection to the Pi, so the Pi does not need a hardcoded
Mac address and the Mac does not need an inbound firewall rule.

For the broader central-office / multi-extension roadmap, see
[operator-exchange-plan.md](operator-exchange-plan.md).

```text
WE302 phone -> Pi operator service -> SSE event -> Mac client -> native macOS action
Mac client -> POST request -> Pi operator service
```

The bridge exchanges intents, not shell commands.

Allowed Mac intents in Rev A:

| Intent | Mac action |
|--------|------------|
| `desktop.open_url` | validate `http(s)` URL, then run `open <url>` |
| `desktop.notify` | show a macOS notification |

There is deliberately no `run_shell` command.

## Pi-side architecture

Desktop integration has one boundary module:

```text
operator_os.desktop_bridge
```

Responsibilities:

| Layer | Responsibility |
|-------|----------------|
| `DesktopRegistry` | client presence, capabilities, per-client queues, acknowledgements |
| `DesktopBridge` | named product intents: open URL, open meeting, notify, notify inbound SMS |
| `ConsoleHub` | thread-safe owner used by the phone loop and HTTP server |
| `ConsoleHttpServer` | transport only: auth, register, SSE stream, ack, diagnostic API |
| `operator_os.main` | phone state decisions; calls named desktop intents only |

Feature code should not build raw `desktop.*` payloads. Add a named method to
`DesktopBridge` first, add one small test, then call that method from the
feature path.

Routing modes (Phase 1):

- `desktop.notify` (SMS / message alerts): fan out to every online client with
  the `notify` capability. `OPERATOR_DESKTOP_CLIENT_ID` does not restrict this.
- `desktop.open_url` / Meet opens: unicast to `OPERATOR_DESKTOP_CLIENT_ID` when
  that client is online and capable; otherwise the first capable online client
  (stable order by `client_id`).

## Pi setup

Set these in the Pi `.env`:

```bash
OPERATOR_DESKTOP_TOKEN=replace-with-a-long-random-token
OPERATOR_CONSOLE_PORT=8788
OPERATOR_CONSOLE_BIND=0.0.0.0
```

The HTTP server starts when either `OPERATOR_CONSOLE_PASSWORD` or
`OPERATOR_DESKTOP_TOKEN` is set.

To make digit 7 open the current Meet on the Mac instead of dialing the Meet
phone bridge:

```bash
OPERATOR_MEET_JOIN_TARGET=desktop
```

Use `auto` to prefer the Mac when it is connected and fall back to the handset
PSTN path when SIP is configured:

```bash
OPERATOR_MEET_JOIN_TARGET=auto
```

Optional preferred client for Meet/URL opens only:

```bash
OPERATOR_DESKTOP_CLIENT_ID=john-macbook
```

Blank means "first online client with `open_url`" for opens. Notifications still
fan out to every online `notify` client.

## Mac setup

### Native app (Phase 6)

Open the Xcode project and run it:

```bash
open mac/OperatorStation/OperatorStation.xcodeproj
```

In Xcode: select the **OperatorStation** scheme → **My Mac** → ⌘R.

Menu bar phone icon → **Settings…** (or ⌘,). Enter the same Pi URL, desktop
token, and client id you use for the CLI. Connect. The app requests Notification
Center permission on first launch and registers with caps `open_url,notify` over
the existing `/api/desktop/*` SSE + POST protocol.

Quit and reopen should reconnect without restarting the Pi. The Python CLI below
remains a debug fallback.

### Python CLI (reference / fallback)

Clone the same repo on the Mac, install the Python environment, and run the
client:

```bash
git clone git@github.com:JohnJChase/operator.git
cd operator
just setup-mac
export OPERATOR_PI_URL=http://operator.local:8788
export OPERATOR_DESKTOP_TOKEN=replace-with-the-same-token
export OPERATOR_DESKTOP_CLIENT_ID=john-macbook
export OPERATOR_DESKTOP_NAME="John's MacBook"
just mac-client
```

Expected output:

```text
mac-client: registered john-macbook at http://operator.local:8788 caps=open_url,notify
mac-client: listening
mac-client: stream ready
```

Notification commands log their visible content:

```text
mac-client: notified (notification): Message from Alice: Running ten minutes late.
```

If that line appears but no macOS banner appears, the bridge worked and macOS
hid the notification. Check Focus / Do Not Disturb and notification permissions
for the terminal app running `just mac-client` and for `osascript`.

For a guaranteed visible fallback, run the Mac client in alert mode:

```bash
OPERATOR_DESKTOP_NOTIFY_MODE=alert just mac-client
```

Alert mode uses a small AppleScript dialog that times out automatically. Use
`OPERATOR_DESKTOP_NOTIFY_MODE=both` to try Notification Center first and also
show the alert.

For connection debugging, run the Mac client with keepalive logging:

```bash
OPERATOR_DESKTOP_VERBOSE=1 just mac-client
```

For Mac-side status and inbox browsing (same `OPERATOR_DESKTOP_TOKEN` as the
listener; console password still works as a fallback):

```bash
export OPERATOR_DESKTOP_TOKEN=replace-with-the-same-token
just mac-status
just mac-inbox
```

To play a voicemail from the Mac:

```bash
just mac-inbox --play-vm 3
```

`mac-inbox` uses `/api/inbox` with the desktop token (or console session).
Resolve voicemail audio as `{OPERATOR_PI_URL}{audio_url}`.

### Outgoing call (Phase 5)

With the WE302 on-hook:

```bash
just mac-call +12025551212
# or: just mac-call --name Alice
```

The Pi rings the 302 (`OUTGOING_RINGING`). Pick up to place the SIP call; hang up
to end it. No pickup within `outgoing_pickup_window_ms` cancels. If the phone is
busy, the request is rejected (check Pi logs). Off-hook console Call still dials
immediately without ringing.

If the WE302 double-rings but the Mac client logs no `notified:` line, check the
Pi log. New SMS delivery attempts log either:

```text
desktop: sms notify id=42
```

or:

```text
desktop: sms notify skipped id=42 no_client; clients=macbook:offline:open_url,notify
```

That skipped line usually means no Mac client is connected with the `notify`
capability (or the SSE session dropped). Preferred Meet client id does not
affect SMS notify fan-out.

## Digit 7 flow

With `OPERATOR_MEET_JOIN_TARGET=desktop`:

1. Lift the handset.
2. Dial 7.
3. The Pi resolves the active Google Calendar Meet.
4. The Pi queues `desktop.open_url` with the Meet URL.
5. The Mac opens the meeting in the default browser.
6. The handset says "Opening <meeting> on your Mac."

Existing multi-meeting behavior stays intact. If several meetings are possible,
the phone reads the menu and the selected meeting opens on the Mac.

## Incoming SMS

When the Pi receives a new inbound SMS, the normal phone behavior still happens:
the WE302 double-rings when idle, or queues the message if the phone is busy.

If a Mac client is connected with the `notify` capability, the Pi also pushes a
`desktop.notify` command with the sender and message preview. The Mac shows a
native notification and acknowledges the command back to the Pi.

## Inbox API (desktop token)

Mac companions may use the same inbox routes as the browser console. Auth is
either the console session cookie **or** `Authorization: Bearer
$OPERATOR_DESKTOP_TOKEN` (or `X-Operator-Desktop-Token`).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/inbox` | `{sms, voicemails, waiting}` |
| GET | `/api/inbox/vm/{id}/audio` | `audio/wav`; `audio_url` in list is relative |
| POST | `/api/inbox/sms/heard` | `{"id": N}` |
| POST | `/api/inbox/sms/delete` | `{"id": N}` |
| POST | `/api/inbox/sms/reply` | `{"id": N, "text": "...", "confirm": true}` |
| POST | `/api/inbox/vm/heard` | `{"id": N}` |
| POST | `/api/inbox/vm/delete` | `{"id": N}` |

Resolve audio as `{OPERATOR_PI_URL}{audio_url}`. Do not Funnel these routes.

## Security rules

- Use a long random `OPERATOR_DESKTOP_TOKEN`.
- Keep this on the LAN or a private network such as Tailscale.
- Do not expose the Pi console/bridge to public ingress.
- Add new bridge actions as explicit intents with validation.
- Never add arbitrary command execution.

## Later

Good next increments:

- Inbox window + voicemail player in OperatorStation
- “Call this contact” / “Open next meeting here” actions
- Phase 7: iPhone station (PWA first)
