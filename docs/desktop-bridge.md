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

Optional targeting:

```bash
OPERATOR_DESKTOP_CLIENT_ID=john-macbook
```

Blank means "first online client with the needed capability."

## Mac setup

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

If the WE302 double-rings but the Mac client logs no `notified:` line, check the
Pi log. New SMS delivery attempts log either:

```text
desktop: sms notify id=42
```

or:

```text
desktop: sms notify skipped id=42 no_client; clients=macbook:offline:open_url,notify
```

That skipped line usually means the Mac client id does not match
`OPERATOR_DESKTOP_CLIENT_ID`, the Mac client is not connected, or the Mac did not
register the `notify` capability.

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

## Security rules

- Use a long random `OPERATOR_DESKTOP_TOKEN`.
- Keep this on the LAN or a private network such as Tailscale.
- Do not expose the Pi console/bridge to public ingress.
- Add new bridge actions as explicit intents with validation.
- Never add arbitrary command execution.

## Later

Good next increments:

- Mac inbox window backed by the Pi `/api/inbox` data.
- Mac "call this contact" request that rings the WE302 first, then places the
  call after pickup.
- Tiny SwiftUI menu bar wrapper around the same `operator-os mac-client` logic.
