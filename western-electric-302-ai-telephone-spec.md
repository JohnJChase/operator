# Western Electric 302 AI Telephone Exchange

> 2026-07-20 implementation note: the refined phased engineering plan is
> captured in `western-electric-302-ai-telephone-implementation-plan.md`.
> Current Rev A hardware uses GPIO17 for hook,
> GPIO10 for dial pulse closures to ground, GPIO23 for ring relay enable,
> direct bypass audio, passive hardware sidetone, and optional software
> sidetone owned by the audio router.

## Project objective

Build a Raspberry Pi-based telephone exchange around a 1937 Western Electric 302 rotary telephone.

The device should behave like a self-contained telephone system rather than a voice assistant placed inside an old phone. The Raspberry Pi acts as the central office, handling:

- Hook state
- Rotary dialing
- Mechanical ringing
- Audio input and output
- Dial tones, ringback, busy signals, and other call-progress tones
- Local information services
- SIP/PSTN calling
- SMS messaging
- An AI operator
- A browser-based administration and monitoring console

The telephone must continue to perform its core functions if cloud AI services or the web console are unavailable.

---

## 1. Core architectural principles

### 1.1 The Raspberry Pi owns the telephone

The local application is authoritative for:

- Physical I/O
- Telephone state
- Audio routing
- Dial-plan interpretation
- Call initiation and termination
- Permission checks
- Confirmation of consequential actions
- Error handling
- Logging

The AI operator may request actions through tools, but it must not directly control GPIO, audio hardware, SIP sessions, or provider APIs.

### 1.2 Deterministic services remain local

Digits `1` through `9` should invoke deterministic local behavior.

Digit `0` connects the user to the cloud-based AI operator.

This keeps ordinary telephone interactions fast, predictable, and available during an internet or AI-service outage.

### 1.3 One component owns handset audio at a time

Create an explicit audio-routing layer. The handset must have one active audio destination:

- Local tones or announcements
- Piper text-to-speech
- AI operator session
- SIP/RTP call
- Diagnostic loopback

Do not allow independent components to write directly to the audio device.

### 1.4 State-driven design

All physical behavior should be governed by an explicit state machine. Avoid distributing telephone state across callbacks and unrelated modules.

---

## 2. Proposed system architecture

```text
Western Electric 302
        |
        |-- Hook switch
        |-- Rotary dial pulses
        |-- Mechanical ringer
        |-- Transmitter and receiver audio
        |
Raspberry Pi hardware abstraction layer
        |
Telephone controller and state machine
        |
        +-- Tone generator
        +-- Piper TTS
        +-- Audio router
        +-- Local services
        +-- Contact and history database
        +-- PJSIP client
        +-- SMS provider API
        +-- OpenAI operator client
        +-- Event and activity system
        +-- Web console API
```

Recommended module separation:

```text
phone-hardware
phone-controller
audio-router
dial-plan
local-services
telephony-service
messaging-service
operator-service
event-bus
persistence
web-api
web-ui
```

The initial implementation may run as one Python service with clearly separated modules.

---

## 3. Primary telephone state machine

Suggested top-level states:

```text
ON_HOOK_IDLE
INCOMING_RINGING
OFF_HOOK_DIAL_TONE
COLLECTING_TOP_LEVEL_DIGIT
LOCAL_SERVICE
OUTSIDE_LINE_DIAL_TONE
COLLECTING_OUTSIDE_NUMBER
AI_OPERATOR
OUTBOUND_CALL_SETUP
OUTBOUND_RINGING
ACTIVE_CALL
INCOMING_CALL_ACTIVE
BUSY_SIGNAL
REORDER_SIGNAL
ERROR_ANNOUNCEMENT
DIAGNOSTIC_MODE
```

Each state should define:

- Allowed physical inputs
- Audio source
- Dial-digit interpretation
- Hook-flash behavior
- Hang-up behavior
- Timeout behavior
- Valid state transitions

All transitions should produce structured events.

Example:

```json
{
  "type": "telephone.state_changed",
  "timestamp": "2026-07-20T18:42:31.418Z",
  "from": "OFF_HOOK_DIAL_TONE",
  "to": "OUTSIDE_LINE_DIAL_TONE",
  "reason": "digit_9_received"
}
```

---

## 4. Proposed dial plan

### Digit 0: Operator

Connect the handset audio to the OpenAI low-latency conversational service.

Example greeting:

> Operator. What number, please?

The operator may use tools such as:

- Search contacts
- Place a call
- Send a message
- Read messages
- Read recent calls
- Check weather
- Start the news report
- Report device status
- Explain available services

The operator requests actions through local tool interfaces. The local controller validates and executes them.

### Digit 1: News of the Day

Play a short, old-time newsreel-style briefing.

Suggested format:

1. Brief musical or tonal introduction
2. Date and time
3. Three to five major stories
4. Local or regional item
5. Closing line

The style may evoke a theatrical newsreel, but the content should remain accurate, clearly dated, and easy to understand.

The script can be generated periodically and cached locally. Piper should synthesize the final narration.

The service should work from the latest cached bulletin when internet access is unavailable.

### Digit 2: Weather Bureau

Read:

- Current conditions
- Today's remaining forecast
- Tonight's forecast
- Tomorrow's forecast
- Any meaningful alerts

A concise version should be the default. A submenu may provide more detail.

```text
1: Repeat current conditions
2: Detailed forecast
3: Weather alerts
4: Extended outlook
```

### Digit 3: Messages

Read recent SMS messages as a numbered list.

Example:

> You have three messages.  
> One, Sarah Mitchell, received at 10:42 this morning.  
> Two, David Chen, received yesterday at 4:15.  
> Three, an unknown sender, received yesterday at 2:03.  
> Dial a number to hear a message.

After selecting a message:

```text
1: Call sender
2: Dictate reply
3: Repeat message
4: Next message
5: Previous message
```

A hook flash returns to the message list. A second hook flash returns to the main exchange.

For lists longer than nine items, paginate rather than requiring multi-digit menu choices.

### Digit 4: Recent Calls

Read a numbered list containing:

- Contact name or formatted number
- Incoming, outgoing, or missed
- Relative date and time
- Call duration when relevant

Example:

> One, missed call from Sarah Mitchell, today at 11:20.  
> Two, outgoing call to David Chen, yesterday at 4:35.  
> Dial a number to return a call.

After selecting an entry:

```text
1: Call
2: Send message
3: Hear details
```

### Digit 5: Directory

Provide access to favorite contacts or voice-based contact lookup.

Possible initial behavior:

```text
1-8: Configurable favorite contacts
9: Ask the operator to find a contact
```

### Digit 6: Voicemail or recorded notices

Possible functions:

- Provider voicemail
- Locally recorded messages
- AI summaries of missed calls
- Saved operator notes
- Household announcements

### Digit 7: Entertainment or radio

Possible functions:

- Period radio stream
- Music
- Historical broadcasts
- Podcasts
- Time signal
- Audio stories

### Digit 8: House and system services

Possible functions:

- Device status
- Home automation
- Intercom
- Other internal extensions
- Diagnostic announcements

Avoid exposing dangerous controls without explicit confirmation.

### Digit 9: Outside line

Digit `9` seizes the external telephone trunk.

```text
Lift handset
-> normal dial tone
-> dial 9
-> relay click
-> second dial tone
-> enter full international-format telephone number
-> call is validated and placed
```

The caller enters the country code followed by the national number, without a leading `+`.

Example:

```text
9 1 703 555 1212
```

Normalize internally:

```text
+17035551212
```

Use telephone-number parsing metadata to determine whether the entered number is:

- Impossible
- Incomplete
- Potentially complete
- Valid and complete
- Ambiguous because another valid longer number remains possible

When the number becomes unambiguously complete, place the call immediately.

When a complete number could legally accept additional digits, use a configurable interdigit timeout.

Suggested timeout:

```text
1.5 to 2.5 seconds
```

Play an intercept announcement for invalid numbers.

---

## 5. Hook-switch behavior

The hook switch serves as both a line-state control and a contextual navigation input.

### Suggested timing thresholds

These values must be configurable and calibrated against the physical switch:

```text
Debounce:              30-75 ms
Hook flash:           100-700 ms
Definite hang-up:    greater than 1000 ms
```

### Contextual behavior

#### While hearing a local menu

- Hook flash: go back one level
- Long hang-up: end session and reset

#### While Piper or the AI is speaking

- Hook flash: interrupt speech
- After interruption, wait for input or replay the current menu prompt

#### While connected to the AI operator

- Hook flash: interrupt the operator
- Optional second flash: leave operator and return to local dial tone

#### While entering a number

- Hook flash: cancel current entry and return to the previous dial tone

#### During a telephone call

Initially:

- Hook flash should generate a logged event but perform no destructive action
- Long hang-up ends the call

Possible later features:

- Call waiting
- Hold
- Transfer
- Conference
- Summon AI operator
- Return temporarily to the operator and then resume the call

---

## 6. AI operator

### Role

The operator is a conversational interface to local telephone services.

Its personality should be helpful, efficient, and lightly period-appropriate without becoming difficult to understand.

Example phrases:

> Operator.

> What number, please?

> One moment while I connect your call.

> I have a message from Sarah Mitchell.

### Tool boundary

The model should receive narrowly defined tools such as:

```text
search_contacts(query)
get_contact(contact_id)
list_recent_calls(limit, filter)
list_messages(limit, unread_only)
read_message(message_id)
prepare_sms(contact_id, text)
confirm_sms(draft_id)
send_sms(draft_id)
prepare_call(contact_id_or_number)
confirm_call(call_request_id)
place_call(call_request_id)
get_weather()
play_news()
get_device_status()
```

Prefer two-stage tools for consequential operations.

Do not allow the model to call:

- Generic shell commands
- Arbitrary HTTP endpoints
- Raw GPIO operations
- Unrestricted provider APIs

### Operator handoff to SIP call

```text
User: "Operator, call Sarah."
Operator: "Calling Sarah Mitchell on her mobile. Shall I proceed?"
User: "Yes."
Operator: "One moment, please."

AI audio stops.
A relay click is played or physically generated.
PJSIP begins the call.
Local ringback is played until remote media is available.
```

The operator session may remain resumable briefly, but it must not retain ownership of the handset audio during the SIP call.

---

## 7. Audio routing

Create a central audio router with explicit sources and destinations.

### Sources

- Handset microphone
- Tone generator
- Piper TTS
- AI operator audio
- SIP remote audio
- Recorded prompts
- Diagnostic audio

### Destinations

- Handset receiver
- AI operator upstream
- SIP upstream
- Recorder
- Level monitor

Example route states:

```text
LOCAL_TONE:
    tone_generator -> receiver

LOCAL_TTS:
    piper -> receiver

AI_OPERATOR:
    microphone -> AI
    AI -> receiver

SIP_CALL:
    microphone -> SIP
    SIP -> receiver

LOOPBACK_TEST:
    microphone -> receiver
```

Requirements:

- Route changes should be atomic.
- Route changes should be logged.
- Gain settings should be configurable per route.
- Clipping and silence detection should be monitored.
- Audio levels should be exposed to the web console.
- Local tones should not enter a live SIP call unless intentionally mixed.

---

## 8. SIP and messaging integration

### Voice

Use PJSIP or PJSUA2 for SIP registration and calling.

Suggested local interface:

```text
register()
unregister()
place_call(e164_number)
answer_call(call_id)
reject_call(call_id)
hang_up(call_id)
get_call_state(call_id)
set_input_device(device)
set_output_device(device)
```

Store SIP credentials outside the source repository.

### Messaging

SMS should use the provider REST API and inbound webhooks.

Requirements:

- Normalize all numbers to E.164.
- Resolve numbers to contacts where possible.
- Store inbound and outbound messages locally.
- Maintain provider message IDs and delivery state.
- Log webhook receipt and processing.
- Avoid exposing full message contents in routine logs unless enabled.
- Support retries for temporary provider failures.
- Prevent duplicate message creation from repeated webhooks.

---

## 9. Local persistence

SQLite is suitable initially.

Suggested tables:

```text
contacts
contact_numbers
favorite_contacts
calls
messages
message_drafts
operator_sessions
service_runs
device_events
system_alerts
configuration
```

### Call record fields

```text
id
provider_call_id
direction
remote_number
contact_id
started_at
answered_at
ended_at
duration_seconds
result
termination_reason
```

### Message fields

```text
id
provider_message_id
direction
remote_number
contact_id
body
received_or_sent_at
delivery_status
read_at
```

### Device event fields

```text
id
timestamp
severity
category
event_type
summary
structured_payload
correlation_id
```

---

## 10. Web-based console

### Purpose

The console should make the telephone observable, diagnosable, and configurable without becoming required for ordinary operation.

Do not expose it directly to the public internet.

### 10.1 Live overview

Display:

- Current telephone state
- Hook state
- Current audio route
- Current dial buffer
- Active local service
- SIP registration status
- Active call status
- AI operator connection status
- SMS provider status
- Internet connectivity
- Piper service status
- CPU temperature
- CPU and memory utilization
- Disk usage
- Process uptime
- Last successful backup
- Current warnings and faults

Suggested readiness states:

```text
READY
DEGRADED
OFFLINE
MAINTENANCE
```

### 10.2 Live I/O panel

Display:

- Raw hook GPIO level
- Debounced hook state
- Last hook transition
- Rotary pulse GPIO level
- Current pulse count
- Last decoded digit
- Ringer relay state
- Audio input level
- Audio output level
- Clipping indicator
- Hardware fault indicators

Include a rolling signal timeline for hook and rotary pulse inputs.

### 10.3 Activity log

Provide a filterable chronological event feed.

Filters:

- Telephone state
- Dial activity
- Hook activity
- Calls
- Messages
- AI operator
- Audio
- Provider webhooks
- Hardware
- Errors
- Security
- Configuration changes

Each event should include:

- Timestamp
- Severity
- Category
- Human-readable summary
- Expandable structured details
- Correlation ID

Add a follow-live mode.

### 10.4 Calls

Display:

- Recent incoming calls
- Recent outgoing calls
- Missed calls
- Contact resolution
- Duration
- Result
- Provider identifiers
- Detailed event timeline

Possible actions:

- Call number
- Add number to contacts
- Send message
- Mark as unwanted
- Copy normalized number

Remote call initiation should require confirmation.

### 10.5 Messages

Display threaded SMS conversations.

Functions:

- Read messages
- Compose and send
- View delivery status
- Associate numbers with contacts
- Mark read or unread
- Search messages
- Retry failed messages

### 10.6 Contacts and directory

Manage:

- Contact names
- Multiple phone numbers
- Labels
- Preferred calling number
- Favorite positions
- Pronunciation hints for Piper
- Aliases used by the AI operator

### 10.7 Dial-plan editor

Allow configuration of digits `0-9`.

Each digit can map to:

- Local service
- Outside line
- Favorite contact
- Audio program
- Disabled service
- Custom plugin

Protect core routes from accidental deletion.

### 10.8 Audio console

Display and configure:

- Input and output device
- Input gain
- Output gain
- Route-specific gain
- Noise gate
- Echo cancellation status
- Sample rate
- Buffer size
- Level meters
- Clipping and underrun events

Test functions:

- Play dial tone
- Play ringback
- Play busy signal
- Speak test phrase through Piper
- Record and play back handset microphone
- Run audio loopback
- Test SIP media

### 10.9 Hardware test page

Provide manual controls for maintenance:

- Read hook switch
- Watch rotary pulses
- Decode a test digit
- Ring once
- Ring with selected cadence
- Activate relay
- Play receiver test tone
- Test microphone
- Run a full self-test

Manual outputs must turn off automatically after a timeout.

### 10.10 Service health

Show status for:

- Main controller
- Audio subsystem
- Piper
- SIP registration
- Messaging webhook server
- AI operator client
- Weather updater
- News updater
- Database
- Backup service

For each service:

- Healthy, degraded, or failed
- Uptime
- Last successful operation
- Last error
- Restart count
- Recent latency
- Dependency status

### 10.11 News and weather content

Display:

- Current cached weather report
- Forecast retrieval timestamp
- Current news script
- Sources and retrieval timestamp
- Piper audio generation status
- Previous editions
- Manual regenerate button
- Preview audio

### 10.12 Configuration and secrets

Configuration may include:

- Location for weather
- Time zone
- SIP settings
- SMS provider settings
- Operator personality
- Dial-plan mapping
- Hook timing thresholds
- Rotary pulse timing thresholds
- Interdigit timeout
- Ringer cadence
- Audio gains
- News length
- Quiet hours
- Logging level

Secrets must not be returned to the browser after being saved.

---

## 11. Additional console features

### 11.1 Live exchange diagram

Show the currently active signal path.

```text
Handset microphone
      |
      +--> AI operator
      +--> SIP provider
      +--> local recorder

Piper / remote caller / tones
      |
      +--> handset receiver
```

### 11.2 State-machine inspector

Display:

- Current state
- Time spent in state
- Previous state
- Triggering event
- Available transitions
- Current timeout
- Pending operations

### 11.3 Session playback

Reconstruct each handset session as a timeline:

```text
14:02:01 Off hook
14:02:01 Dial tone started
14:02:04 Digit 3 decoded
14:02:04 Messages service entered
14:02:06 Piper prompt started
14:02:11 Digit 2 decoded
14:02:12 Message 2 selected
14:02:18 Hook flash detected
14:02:18 Returned to message list
14:02:23 On hook
```

Do not record private call audio by default.

### 11.4 Event simulator

Support software-only testing:

- Simulate off-hook
- Simulate hook flash
- Simulate hang-up
- Inject rotary digit
- Simulate incoming call
- Simulate SMS
- Simulate provider failure
- Simulate AI disconnection

Simulation events must be clearly labeled and must not trigger real calls or messages unless explicitly enabled.

### 11.5 Maintenance mode

Maintenance mode should:

- Prevent normal outgoing calls
- Prevent mechanical ringing
- Allow hardware tests
- Mark the device unavailable
- Automatically expire after a configurable period

### 11.6 Quiet hours

During configured hours:

- Suppress or reduce mechanical ringing
- Queue nonurgent message announcements
- Allow selected contacts to bypass quiet mode
- Continue logging all events

### 11.7 Configuration snapshots

Support:

- View changes
- Restore previous version
- Export configuration
- Import configuration
- Reset one subsystem independently

---

## 12. Web-console implementation

A reasonable stack:

```text
Backend:
Python
FastAPI
WebSocket or Server-Sent Events
SQLite initially

Frontend:
React, Vue, Svelte, or lightweight server-rendered HTML
```

Suggested endpoints:

```text
GET  /api/status
GET  /api/io
GET  /api/events
GET  /api/calls
GET  /api/messages
GET  /api/contacts
GET  /api/config
GET  /api/services

POST /api/actions/ring-test
POST /api/actions/play-tone
POST /api/actions/speak
POST /api/actions/restart-service
POST /api/actions/enter-maintenance
POST /api/actions/exit-maintenance
POST /api/actions/simulate-digit
POST /api/messages
POST /api/calls

WS   /api/live
```

Commands must be validated by the central controller. The web server must not manipulate GPIO or audio hardware directly.

---

## 13. Security

Minimum requirements:

- Bind to the local network only by default.
- Require authentication.
- Do not use default credentials.
- Store password hashes, not plaintext passwords.
- Protect provider and OpenAI secrets.
- Record administrative actions.
- Require confirmation for calls, messages, ringing, and service restarts.
- Rate-limit login and control endpoints.
- Validate provider webhook signatures.
- Avoid exposing logs containing credentials or message contents.
- Use a VPN or secure tunnel for remote access.
- Do not forward the console port directly from the public internet.

Possible roles:

```text
Viewer:
Can inspect status and logs

Operator:
Can manage calls, messages, and content

Administrator:
Can modify hardware, provider, and security settings
```

---

## 14. Reliability and recovery

Requirements:

- Start automatically on boot.
- Use systemd or equivalent supervision.
- Restart failed services.
- Preserve event history across restarts.
- Recover safely after power loss.
- Leave ringer and relays off during startup.
- Return to `ON_HOOK_IDLE` after an unclean restart.
- Detect stale SIP and AI sessions.
- Use timeouts for every external request.
- Fall back to cached news and weather.
- Continue local dial tone and menus when cloud services fail.
- Announce failures in plain language.

Example:

> The operator is temporarily unavailable. Local services remain in operation.

---

## 15. Safety rules

- Emergency calling must be intentionally designed and tested or explicitly blocked.
- The AI must not be the only way to place an emergency call.
- Premium-rate and international calls may require additional confirmation.
- Mechanical ringer activation must have a maximum duration.
- GPIO outputs must fail off.
- Manual web-console controls must expire automatically.
- Sending messages and placing calls through the AI requires confirmation.
- Diagnostic audio must not unexpectedly enter a live call.
- Private calls and messages should not be recorded or exposed in routine logs.

---

## 16. Recommended implementation phases

### Phase 1: Core exchange

- Formal telephone state machine
- Hook and rotary input abstraction
- Audio router
- Local tone generator
- Structured event log
- Basic web status page
- Live I/O inspection

### Phase 2: Local services

- News service
- Weather service
- Piper integration
- Messages menu
- Recent-calls menu
- Hook-flash navigation
- Session timeline

### Phase 3: Outside line

- PJSIP integration
- SIP registration
- Digit `9` trunk behavior
- Number parsing and normalization
- Incoming and outgoing calls
- Call history
- Mechanical ringing

### Phase 4: Messaging

- SMS API
- Inbound webhook
- Message database
- Spoken message browsing
- Dictated replies
- Web conversation view

### Phase 5: AI operator

- Realtime audio session
- Tool definitions
- Contact lookup
- Call preparation and confirmation
- Message preparation and confirmation
- Audio handoff between AI and SIP

### Phase 6: Full administration console

- Configuration editor
- Dial-plan editor
- Hardware diagnostics
- Audio meters
- Health monitoring
- State-machine inspector
- Simulation tools
- Configuration snapshots
- Secure remote access

---

## 17. Initial acceptance criteria

The first meaningful release is complete when:

1. Taking the phone off-hook produces dial tone.
2. Dialing `1` plays a cached Piper-generated news bulletin.
3. Dialing `2` reads today and tomorrow's weather.
4. Dialing `3` reads a numbered message list.
5. Dialing `4` reads a numbered recent-call list.
6. A hook flash goes back one menu level.
7. Hanging up resets the telephone reliably.
8. Dialing `9` produces a click and second dial tone.
9. A valid international-format number can be collected and normalized.
10. The web console shows live hook state, pulse count, decoded digits, telephone state, audio route, and activity events.
11. The device remains usable for local services when the internet is disconnected.
12. No console command can bypass the central state machine.

---

## 18. Coding guidance

Before adding features, define:

- State enums
- Event schemas
- Command schemas
- Hardware interfaces
- Audio-route interfaces
- Provider interfaces
- Database models

Prefer dependency injection so real hardware and providers can be replaced by simulators during testing.

Write tests for:

- Rotary pulse decoding
- Hook-flash timing
- State transitions
- Interdigit timeout
- Number normalization
- Menu pagination
- Duplicate webhook handling
- Failed call setup
- Provider timeout
- AI tool confirmation
- Hang-up during every major state

The state machine and dial plan should be testable without GPIO, audio hardware, SIP registration, or internet access.
