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

Current Mac bridge names can map internally:

```text
open_url       -> open.url
notify         -> notify.messages / notify.generic
desktop.open_url -> transport command for Mac client
desktop.notify   -> transport command for Mac client
```

Do not rename everything immediately if it creates churn. Add the exchange
vocabulary at the boundary first, then migrate call sites gradually.

## Extension Registration

An extension registers with a stable id, kind, display name, and capabilities.

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

The WE302 should eventually be represented too:

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

Keep the existing SSE plus POST shape.

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

The existing `/api/desktop/*` endpoints can remain as compatibility wrappers
until the extension endpoints exist and the Mac client has migrated.

## Routing Rules

Routing must be boring and explicit.

1. Prefer explicitly configured extension when present and online.
2. Otherwise route to the first online extension with the required capability.
3. For notifications, route to all online extensions with the required
   capability unless the intent says otherwise.
4. If no route exists, log a skipped delivery with the reason and current
   extension summary.
5. Never silently drop a user-visible intent.

## Security Rules

- Keep shared token auth for local LAN/Tailscale use.
- No public ingress.
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

### Phase 0: Stabilize Current Bridge

Goal: make sure the existing Mac bridge is reliable and observable.

Work:

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

### Phase 1: Introduce Exchange Extension Vocabulary

Goal: make the protocol talk about extensions, not desktops.

Work:

- add `operator_os/extensions.py` or evolve `desktop_bridge.py` carefully
- define `ExtensionClient`, `ExtensionRegistry`, `ExtensionDelivery`
- add capability constants or simple string helpers
- keep `DesktopBridge` as a thin compatibility facade if that is the smallest
  migration path

Acceptance:

- current Mac client still works
- tests show routing by capability
- no feature code builds raw transport payloads
- docs show extension registration shape

Checks:

```bash
uv run pytest tests/test_desktop_bridge.py tests/test_console.py
uv run ruff check .
```

### Phase 2: Extension Intent and Request API

Goal: formalize exchange-to-extension and extension-to-exchange messages.

Work:

- add intent names for:
  - `notify.messages`
  - `notify.voicemail`
  - `open.meeting`
  - `inbox.changed`
- add request names for:
  - `call.request`
  - `message.reply.request`
  - `voicemail.play.request`
- add validation for each request shape
- add `/api/extensions/request`

Acceptance:

- Mac can request a harmless test action through the new request endpoint
- invalid request names are rejected
- arbitrary shell execution is impossible by construction
- one focused test covers each non-trivial request validator

### Phase 3: Inbox Service API

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

### Phase 4: Mac Companion CLI V2

Goal: prove the extension model before building a real app.

Work:

- add CLI subcommands or a simple text UI:
  - show connection status
  - list inbox
  - show latest messages
  - play voicemail through local default player
  - request outgoing call
- keep native dependencies at zero

Acceptance:

- Mac can list inbox from Pi
- Mac can request outgoing call by number
- Pi rings WE302 for pickup before placing the SIP call
- no direct Mac-to-Telnyx call path

### Phase 5: Outgoing Call Request Flow

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

- add pending outgoing-call request state in `ConsoleHub` or a dedicated
  exchange object
- add chart event only if the phone state machine needs it
- do not bypass the existing SIP place-call path

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

The next useful coding increment is Phase 1, but keep it small:

1. Add extension vocabulary types while keeping existing desktop endpoints.
2. Add capability-based notification routing.
3. Keep current Mac client working unchanged.
4. Add tests proving multiple clients can register and only capable clients
   receive each intent.

Do not build the native Mac app yet. First prove the exchange protocol with the
existing Python client.

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
