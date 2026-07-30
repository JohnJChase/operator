# Operator Exchange Plan

Date: 2026-07-30

## Purpose

This document plans the next architecture step for Operator: move from "the Pi
controls one old phone" to "the Pi is a small central office with multiple
extensions."

The goal is not to build a commercial PBX. The goal is to keep the project
solid while staying small enough for a hobby appliance.

## Product Model

The Raspberry Pi is the Operator central office. It owns:

- telephone state
- extension registration
- message and voicemail storage
- SIP outside-line service
- routing between local stations
- the AI/operator service boundary

The WE302 is one station on the exchange. The Mac companion is another station.
An iPhone or web client can become another station later.

```text
                      Operator Exchange
                       Raspberry Pi

        ------------------------------------------------
        |                     |                        |
   WE302 station         Mac station              iPhone station
   hook/dial/ring        meetings/inbox/UI        mobile notifications
   handset audio         desktop actions          lightweight controls

                         SIP trunk
                         outside line
```

The exchange routes by capability, not by device name.

Good:

```text
send message notification to all notify.messages extensions
open meeting on preferred open.meeting extension
request outgoing call through call.request extension flow
```

Bad:

```text
if client_id == "john-macbook" then special-case meeting behavior
if client_id == "iphone" then special-case message behavior
```

## Current Baseline

Today the repo already has the first draft of this model:

- `operator_os.desktop_bridge.DesktopRegistry`
- `operator_os.desktop_bridge.DesktopBridge`
- `operator_os.console_hub.ConsoleHub`
- SSE from Pi to Mac
- POST from Mac to Pi
- token auth
- Mac capabilities: `open_url`, `notify`
- named intents for opening a Meet and notifying inbound SMS

This should be treated as the seed of the exchange client protocol, not a
throwaway hack.

Phase 0 of this roadmap is therefore complete in code. Keep using live smoke
tests for confidence, but do not treat "stabilize the current Mac bridge" as a
new architecture project.

## Terms

| Term | Meaning |
|------|---------|
| Exchange | The Pi service that owns routing, state, storage, and trunks |
| Extension | A registered local client/station |
| Station | A physical or software endpoint, such as WE302, Mac, or iPhone |
| Trunk | Outside service, currently Telnyx SIP |
| Capability | A thing an extension says it can do |
| Intent | Exchange to extension command, such as `open.meeting` |
| Request | Extension to exchange command, such as `call.request` |
| Event | Informational notification, such as `inbox.changed` |

## Capability Vocabulary

Keep capability names semantic and stable. Transport names are implementation
details.

Recommended capability names:

```text
notify.messages
notify.voicemail
open.meeting
browse.inbox
play.voicemail
request.outgoing_call
ring.pickup
voice.handset
dial.rotary
```

### Wire vs Product Vocabulary

Product vocabulary is allowed to become semantic before the wire protocol does.
Do not rename the current Mac wire protocol until there is a real second client
or a Mac V2 migration that needs it.

Current Mac bridge names can map internally:

```text
open_url       -> open.url
notify         -> notify.messages / notify.generic
desktop.open_url -> transport command for Mac client
desktop.notify   -> transport command for Mac client
```

For Phase 1, keep:

- `/api/desktop/*`
- SSE event name `command`
- Mac registration capabilities `open_url` and `notify`
- transport commands `desktop.open_url` and `desktop.notify`

Use semantic names such as `open.meeting` and `notify.messages` as internal
product/routing names first. Wire renames are deferred.

## Future Extension Registration

When the extension protocol exists, an extension will register with a stable id,
kind, display name, and capabilities.

```json
{
  "extension_id": "john-macbook",
  "kind": "desktop",
  "display_name": "John's MacBook",
  "capabilities": [
    "notify.messages",
    "open.meeting",
    "browse.inbox",
    "request.outgoing_call"
  ]
}
```

The WE302 should eventually be represented too, but this is a future sketch,
not Phase 1 work:

```json
{
  "extension_id": "we302",
  "kind": "telephone",
  "display_name": "WE302",
  "capabilities": [
    "ring.pickup",
    "voice.handset",
    "dial.rotary",
    "notify.messages"
  ]
}
```

## Protocol Shape

Keep the existing SSE plus POST shape. The current Mac client should keep using
the `/api/desktop/*` endpoints until a non-Mac client or Mac V2 forces a real
extension protocol.

Future extension API shape:

```text
Extension -> Exchange
  POST /api/extensions/register
  GET  /api/extensions/events?extension_id=...
  POST /api/extensions/ack
  POST /api/extensions/request

Exchange -> Extension over SSE
  event: ready
  event: intent
  event: keepalive
```

When `/api/extensions/*` exists, the existing `/api/desktop/*` endpoints can
remain as compatibility wrappers until the Mac client has migrated.

For inbox changes, SSE should carry a thin `inbox.changed` style event. Full
message and voicemail data should come from the inbox API. Bounded notification
previews are acceptable for user-visible notifications.

## Routing Rules

Routing must be boring and explicit. Today, the desktop bridge is transitional:
commands can be enqueued to every online client with the required capability.
Phase 1 must make the policy explicit before multiple clients are treated as a
normal operating mode.

Target routing policies:

1. Notifications fan out to all online extensions with the required capability.
2. Meeting/URL opens are unicast: prefer explicitly configured extension when
   present and online; otherwise route to the first online capable extension.
3. Call requests are accepted from one extension and routed through the phone
   chart, not directly to the SIP trunk.
4. If no route exists, log a skipped delivery with the reason and current
   extension summary.
5. Never silently drop a user-visible intent.

## Security Rules

- Keep shared token auth for local LAN/Tailscale use.
- No public ingress for console, desktop, or future extension APIs.
- The Telnyx SMS webhook may use Funnel or equivalent public ingress; that
  exception does not apply to control APIs.
- No arbitrary shell command intent.
- Each extension request must be allowlisted and validated.
- Calls and SMS sends need confirmation or an explicit trusted UI action.
- URLs must be `http` or `https` and must not include credentials.

## Minimal Data Model

Do not add tables until the feature needs persistence.

In-memory is enough for:

- registered extensions
- online/offline presence
- command queues
- last ack

SQLite already owns:

- SMS messages
- voicemail rows
- contacts

Future persistent extension preferences may need a small table:

```text
extension_preferences(
  extension_id TEXT PRIMARY KEY,
  display_name TEXT,
  preferred_for_meet INTEGER,
  preferred_for_notifications INTEGER
)
```

Do not add it until there is a real setting to save.

## Phase Plan

### Phase 0: Stabilize Current Bridge (Complete)

Goal: make sure the existing Mac bridge is reliable and observable.

Status:

- complete in current code
- keep `DesktopBridge` as the single Pi-side boundary
- keep diagnostic logs for skipped delivery
- keep Mac client console logging for received commands
- use alert mode while native macOS notifications are immature

Acceptance:

- incoming SMS double-rings WE302
- connected Mac logs the message notification content
- digit 7 still opens Meet on Mac when configured
- skipped delivery logs include reason and client summary

Checks:

```bash
uv run pytest tests/test_desktop_bridge.py tests/test_mac_client.py
uv run ruff check .
```

### Phase 1: Make Routing Policy Explicit In The Current Bridge

Goal: keep the current Mac bridge working while making routing rules explicit
enough for multiple clients.

Work:

- evolve `DesktopBridge` in place; do not create `operator_os/extensions.py`
  unless the file truly needs to split
- add internal product-intent to capability/transport mapping
- keep `/api/desktop/*`, SSE `command`, Mac caps `open_url` / `notify`, and
  transport commands `desktop.open_url` / `desktop.notify`
- implement routing modes:
  - notifications fan out to all capable online clients
  - meeting/URL opens go to configured preferred client, else first capable
    online client
- add multi-client tests proving fan-out vs unicast
- explicitly defer `/api/extensions/*`, SSE rename to `intent`, and WE302
  registry entry

Acceptance:

- current Mac client still works
- tests show routing by capability and routing mode
- no feature code builds raw transport payloads
- docs show extension registration shape as future vocabulary, not current wire

Checks:

```bash
uv run pytest tests/test_desktop_bridge.py tests/test_console.py
uv run ruff check .
```

### Phase 2: Inbox Service API

Goal: make inbox and voicemail usable by non-browser clients without duplicating
console code.

Work:

- preserve existing console inbox behavior
- expose clean API responses for:
  - list inbox
  - mark SMS heard
  - delete SMS
  - reply to SMS
  - list voicemail
  - mark voicemail heard
  - delete voicemail
  - fetch voicemail audio
- share helpers between console HTTP and extension API where useful

Acceptance:

- browser console still works
- Mac client can fetch inbox JSON
- voicemail audio URL works from Mac
- tests use temp SQLite DB

### Phase 3: Minimal Extension Request API

Goal: let a client ask the exchange for allowlisted work only when the Mac needs
to call back into the Pi.

Work:

- define request names only for real features being built next
- likely first requests:
  - `message.reply.request`
  - `voicemail.play.request`
- defer `call.request` until Phase 5 so outgoing calls are chart-first from the
  beginning
- add validation for each request shape
- reuse current desktop POST plumbing if that is smallest
- add `/api/extensions/request` only when the compatibility path becomes more
  confusing than helpful

Acceptance:

- Mac can request a harmless test action or one real validated action
- invalid request names are rejected
- arbitrary shell execution is impossible by construction
- one focused test covers each non-trivial request validator

### Phase 4: Mac Companion CLI V2

Goal: prove richer Mac station behavior before building a real app. This phase
does not require a new wire protocol; use the current desktop bridge plus the
new inbox APIs unless changing protocol is clearly smaller.

Work:

- add CLI subcommands or a simple text UI:
  - show connection status
  - list inbox
  - show latest messages
  - play voicemail through local default player
- keep native dependencies at zero
- leave outgoing-call requests to Phase 5

Acceptance:

- Mac can list inbox from Pi
- Mac can show latest messages and voicemail metadata
- Mac can play voicemail through an existing platform player or print the audio
  URL if local playback is not worth the code yet
- no new native dependencies are required

### Phase 5: Chart-First Outgoing Call Request Flow

Goal: make the Mac a station that can ask the exchange to place a call while the
WE302 keeps the physical ritual.

Flow:

```text
Mac request: call.request +12025551212
Pi validates number/contact
Pi rings WE302 with outgoing-call cadence
User picks up WE302
Pi places SIP call
Hangup ends call
```

Work:

- add a real state-machine event/state for outgoing pickup ringing, following
  the `SMS_ALERTING` / `INCOMING_RINGING` pattern
- add the corresponding chart edge(s) and plant patch before live behavior
- extend the existing `request_place_call` / `take_place_call` path where
  possible instead of adding a second place-call queue
- do not bypass the existing SIP place-call path
- forbid on-hook SIP dialing without the outgoing ringing state

Acceptance:

- on-hook WE302 rings for Mac-requested call
- off-hook pickup places the SIP call
- no pickup within timeout cancels request
- busy WE302 rejects or queues with an explicit reason

### Phase 6: Native Mac App

Goal: replace terminal companion behavior with a small real Mac station.

Recommended stack:

- SwiftUI menu bar app
- `URLSession` for POST
- SSE client over `URLSession.bytes`
- native notification permission
- small inbox window

Features:

- connection status
- native notifications
- inbox window
- voicemail player
- "call this contact"
- "open next meeting here"

Acceptance:

- app appears in macOS Notifications settings
- notifications work without AppleScript hacks
- app reconnects after Pi restart
- app can be quit and reopened without Pi restart

### Phase 7: iPhone Station

Goal: add a mobile station only after Mac proves the exchange model.

Start with the boring version:

- mobile-friendly web/PWA over LAN/Tailscale
- inbox browse
- voicemail playback
- request outgoing call

Defer native iOS push until the feature earns it.

Acceptance:

- iPhone can load inbox over private network
- iPhone can request WE302 callback/outgoing call
- no public internet exposure required

### Phase 8: Extension-to-Extension Voice

Goal: support station-to-station voice calls.

This is intentionally late. Do not hand-roll RTP, echo cancellation, call
mixing, or browser audio signaling.

Evaluate proven telephony tools when the product need is clear:

- Asterisk
- FreeSWITCH
- PJSIP/pjsua patterns already in the repo

Acceptance:

- WE302 can call Mac station
- Mac station can ring WE302
- outside SIP trunk still works
- voicemail still works

## Immediate Next Build

The next useful coding increment is revised Phase 1:

1. Keep the current desktop wire protocol unchanged.
2. Add routing modes inside `DesktopBridge`:
   - notification fan-out
   - meeting/URL unicast
3. Keep current Mac client working unchanged.
4. Add multi-client tests proving notify fan-out vs meet unicast.
5. Add skipped-delivery logging for both routing modes if missing.

Do not build the native Mac app yet. First prove the exchange routing model with
the existing Python client.

## Non-Goals For Now

- no public cloud broker
- no arbitrary shell command bridge
- no Electron app
- no native iOS app
- no hand-rolled voice-over-IP stack
- no persistent extension settings until we have real preferences to save

## Success Criteria

This architecture is succeeding when:

- the WE302 still feels like the main artifact
- the Pi feels like a central office
- adding a new station means registering capabilities, not editing feature
  code all over the repo
- Mac and future iPhone clients can browse data through shared APIs
- outside calls still go through the exchange and preserve the physical pickup
  ritual
- tests cover routing decisions and request validation without requiring real
  GPIO or live audio
