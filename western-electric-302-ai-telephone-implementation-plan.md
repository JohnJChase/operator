# Western Electric 302 AI Telephone Exchange - Implementation Plan

Date: 2026-07-20

This is the consolidated engineering handoff and build plan for building the
WE302 AI Telephone Exchange from a fresh session. A coding agent should be able
to start from this document and the current hardware docs. It incorporates the
key build requirements for the current Rev A hardware.

- `western-electric-302-ai-telephone-spec.md`
- the latest Rev A as-built hardware documents in this workspace

The intended reader is an engineering coding agent. This document should be
treated as the canonical build plan and source-of-context for a greenfield
software stack.

---

## 1. Project Brief

Build a Raspberry Pi-based local telephone exchange around a Western Electric
302 rotary telephone.

The phone should not feel like a modern voice assistant hidden inside an old
case. It should feel like an alternate-history Bell System appliance: a
1930s/1940s information operator system in which the Raspberry Pi is the
"central office" and the WE302 is the subscriber set.

The user experience should preserve:

- lifting and hanging up the handset
- hearing authentic call-progress tones
- rotary dialing
- mechanical bell ringing
- the original receiver
- the original carbon transmitter
- analog sidetone
- the theatrical feeling of talking to "the Operator"

The user should think:

> I am talking to the Operator.

Not:

> I am talking to ChatGPT through an old phone.

---

## 2. Historical and Fictional Context

The Western Electric 302 is an iconic Bell System desk telephone introduced in
the late 1930s. It is associated with Henry Dreyfuss's industrial design work
and with the transition toward the self-contained modern desk set: ringer,
network, dial, transmitter, and receiver integrated into a compact object that
became visually synonymous with telephone service.

For this project, the 302 is not just a case or prop. It is the user interface.
Its weight, dial return, bell, handset audio, and line-state rituals are the
product.

The alternate timeline:

- Bell Labs has built an electromechanical/computational information exchange.
- The user reaches it through familiar telephone rituals.
- The service speaks with the calm directness of a period operator.
- Local deterministic services remain available even when the cloud operator is
  unavailable.

The illusion is more important than preserving every original circuit topology.
Preserve experiences, not unnecessary uncertainty.

---

## 3. Review of the Proposed Build

### Strong choices in the proposed spec

The proposed architecture is directionally excellent:

- The Raspberry Pi is authoritative for telephone state.
- The AI operator is behind a tool boundary and cannot directly manipulate
  GPIO, providers, or audio hardware.
- Deterministic local services on digits `1-9` keep the telephone useful without
  internet or AI.
- Digit `0` as the operator and digit `9` as outside line match telephone
  expectations.
- A single audio owner protects the handset audio path.
- A formal state machine avoids callback spaghetti.
- The web console is explicitly diagnostic and administrative, not required for
  ordinary use.
- Safety, confirmation, logging, and local fallback are called out.

### Implementation decisions

The current build plan makes these decisions explicit:

1. Use Rev A direct-bypass hardware with hook on GPIO17, dial pulse on GPIO10,
   and ring on GPIO23.
2. Dial polarity is fixed by the current hardware profile: the contact is open
   at rest and closes to ground on each return pulse. Timing calibration is
   useful; web-configurable polarity is not required.
3. Audio must be protected by a real owner. No module except `audio.py` may call
   `aplay`, `arecord`, ALSA, Piper, or audio streams directly.
4. The implementation needs a simulator from day one. State machine, dial
   decoder, dial plan, and diagnostics must be testable without GPIO or audio
   hardware.
5. The OpenAI implementation should wait until the local phone loop works. API
   and model choices must be verified against official OpenAI docs at
   implementation time.

---

## 4. Canonical Build Documents

This file is the consolidated source of truth for system implementation. The
workspace is intentionally treated as a blank-slate handoff: use only the
canonical documents below.

Use these documents:

1. Live measured hardware profile from Phase 0 smoke verification.
2. This implementation plan.
3. Current Rev A as-built hardware docs:
   - `REV_A_BOARD_AS_BUILT.md`
   - `rev_a_board_netlist.yaml`
   - `rev_a_board_mermaid.mmd`
   - `rev_a_board_graphviz.dot`
4. `western-electric-302-ai-telephone-spec.md` for product vision and target
   system behavior.

If a file disagrees with the current plan and current as-built hardware docs,
the file is stale and should be corrected or removed.

### Current implementation rules

- Hook pin: GPIO17.
- Dial pulse pin: GPIO10.
- Ring relay pin: GPIO23.
- Dial pulse contact is open at rest and closes to GND during dial return.
- Count dial pulses with `gpiozero.Button.when_pressed`.
- Direct bypass audio is the Rev A design.
- Carbon mic and hardware sidetone are present.
- Optional software sidetone is allowed only through `audio.py`.
- Hardware off-hook ring interlock is not desired; use software ring cutoff.
- The implementation must not bake pins directly into code. It must load the
  hardware profile.
- Dial polarity does not need a web UI toggle, but keeping it in the profile
  makes the assumption explicit.

---

## 5. Hardware Profile to Support

### Target Rev A profile, pending Phase 0 verification

```yaml
hardware_profile:
  name: rev_a_direct_bypass_gpio10
  gpio:
    hook_bcm: 17
    dial_pulse_bcm: 10
    ring_bcm: 23
  dial:
    mode: pulse_only
    off_normal_available: false
    pulse_contact: open_at_rest_closes_to_ground
    count_event: gpiozero_when_pressed
    digit_done_ms: 700
    pulse_debounce_ms: 20
    polarity_web_configurable: false
  hook:
    debounce_ms: 50
    flash_min_ms: 100
    flash_max_ms: 700
    hangup_min_ms: 1000
  ring:
    cadence_on_ms: 2000
    cadence_off_ms: 4000
    poll_hook_while_ringing_ms: 50
    software_cutoff_required: true
  audio:
    alsa_device: plughw:2,0
    format: S16_LE
    sample_rate_hz: 16000
    channels: 1
```

### Latest Rev A hardware facts

- Ring: Black Magic high-voltage output directly across `L1` and `K`.
- Ring command: Pi GPIO drives NPN relay driver; relay switches Black Magic
  low-voltage input.
- Hook: `BK-Y`, on-hook HIGH, off-hook LOW.
- Dial pulse: `BB-Y`, GPIO10, pulse-only, no GPIO-suitable off-normal.
  Contact is open at rest and closes to GND on dial return pulses. Count
  `gpiozero.Button.when_pressed` events.
- Board power: Pi 5V header powers relay, mic drive, and Black Magic
  low-voltage input.
- Hardware off-hook ring interlock is not built and is not desired. Ring cutoff
  is software:
  when hook goes off-hook, immediately drop ring GPIO.

### Audio facts from latest Rev A

```text
ATR2x headphone tip -> 220 ohm -> WHITE_RX
ATR2x headphone sleeve -> RED/R common

+5V -> 220 ohm -> 10k mic-drive pot -> BLACK_MIC
BLACK_MIC -> mic recording/gain coupling cap -> ATR2x mic tip
ATR2x mic sleeve -> RED/R common / board GND

BLACK_MIC -> 470uF sidetone cap -> 1k sidetone pot -> 10 ohm -> WHITE_RX
```

Cap polarity when electrolytic:

```text
mic recording/gain coupling cap + -> BLACK_MIC
mic recording/gain coupling cap - -> ATR2x mic tip

sidetone 470uF cap + -> BLACK_MIC
sidetone 470uF cap - -> sidetone pot
```

### Software and timing requirements

- Only one process may own GPIO lines.
- Only one process may read the ATR2x microphone.
- Do not overlap independent `aplay` users.
- Keep mic capture dead on-hook.
- Decode the dial pulse-only; do not depend on off-normal.
- Commit a digit after about 0.70 seconds of silence.
- Do not split a digit using a mid-burst gap.
- Do not blindly skip a leading pulse as "wind-up"; that can undercount.
- Use a dial trace/calibration utility after any rewire or if decoding becomes
  unreliable. Do not require exhaustive raw tracing before app work when the
  dial is already working.
- Treat Piper output sample rate as variable; resample before assuming 16 kHz.
- Pace audio streams in realtime if mirroring to a browser.
- Drop excess UI audio-meter chunks rather than allowing growing lag.
- Flush/recreate browser output gain graphs on hangup.
- Dial tone should be 350 Hz + 440 Hz, modest amplitude, 16 kHz mono.

---

## 6. Architecture Decisions

### Runtime

- Language: Python 3.11 or newer, with Python 3.12 preferred if practical on
  the Raspberry Pi.
- Process model: one Python process owns GPIO, audio, and telephone state.
- No background service split until the single-process version is stable and a
  real limitation appears.
- No database, web server, provider SDK, or admin UI in the first product loop
  unless a phase below explicitly earns it.

### Minimal dependency ladder

Start with the smallest useful stack:

```text
Project/env:   uv, just
GPIO:          gpiozero on the Pi
Config:        PyYAML for hardware_profile.yaml, plus env overrides where needed
Testing:       pytest
Quality:       ruff
Audio I/O:     stdlib subprocess around ALSA tools first
TTS:           Piper CLI, espeak-ng fallback
HTTP clients:  stdlib urllib.request first
Storage:       files and JSON/JSONL first
```

Do not add `pydantic`, SQLAlchemy, Alembic, FastAPI, pytest-asyncio, OpenAI,
Telnyx, or `phonenumbers` until the phase that uses them. If a later phase adds
one, document why stdlib or existing code was not enough.

### Shape of the app

Keep the core boring:

- `phone.py` owns hook, dial, ring, and simulator adapters.
- `dial.py` owns pulse counting and digit completion.
- `audio.py` owns every `aplay`, `arecord`, Piper, tone, and mic operation.
- `state.py` owns the telephone state transition function.
- `services.py` owns local digit services.
- `diagnostics.py` owns CLI hardware checks.
- `main.py` wires the loop together.

This is not a framework. It is one small appliance program.

### State machine

Use a small explicit state machine. Do not hide call flow in GPIO callbacks.

Initial states:

```text
ON_HOOK_IDLE
INCOMING_RINGING
DIAL_TONE
COLLECTING_DIGIT
PLAYING_SERVICE
DIAGNOSTIC
ERROR
```

Future phases may add `AI_OPERATOR`, `OUTSIDE_LINE`, and `ACTIVE_CALL` when
those features exist. Do not test or scaffold future states before then.

### Audio owner

One component owns audio. In Rev A, this can be a simple process-wide lock in
`audio.py`, not a mixer framework.

Required operations:

- `play_tone(name_or_hz)`
- `speak(text)`
- `play_file(path)`
- `record(seconds, output_path)`
- `stop()`

Rules:

- No direct `aplay`, `arecord`, ALSA, Piper, or stream writes outside
  `audio.py`.
- Hangup calls `audio.stop()`.
- Mic capture is off while on-hook.
- Hardware sidetone is already present.
- Optional software sidetone may be added later only inside `audio.py`, at low
  gain, disabled on-hook, and never as browser loopback.

### Logging and persistence

Use files before a database:

```text
data/events.jsonl
data/news.json
data/news.mp3
data/weather.json
data/recordings/
```

Append JSONL events for debugging and phase acceptance. Keep an in-memory last-N
event list for diagnostics. Move to SQLite only when lists, messages, call
history, or search make files painful.

### Provider boundaries

Provider code appears only in the phase that needs it:

- Open-Meteo/newsdata.io can start with `urllib.request`.
- OpenAI API details must be checked against official docs when Phase 7 begins.
- Telnyx SIP/SMS code waits until the SIP/SMS phases.
- Consequential actions still require local confirmation before calling or
  messaging.

### Product and provider decisions

- Default Piper voice: `en_US-hfc_female-medium`.
- Weather: Open-Meteo forecast API for a configurable Fairfax, Virginia area
  location.
- News: newsdata.io headlines, summarized later with GPT, rewritten as a
  1937-style newsreel, synthesized with Piper, and cached locally.
- SIP provider when implemented: Telnyx.
- SMS provider when implemented: Telnyx.
- Emergency calling: explicitly blocked with a spoken announcement until a
  future emergency-calling design is intentionally implemented and tested.
- Private call audio is not recorded by default.
- Outside line comes after AI operator basics.

---

## 7. Proposed Repository Structure

Start small. Add folders only when the phase needs them.

```text
operator/
  AGENTS.md
  .gitignore
  .python-version
  .env.example
  justfile
  pyproject.toml
  uv.lock
  README.md
  config/
    hardware_profile.yaml
  data/
    .gitkeep
  docs/
    hardware-profile-verified.md
    pi-dev-environment.md
  operator_os/
    __init__.py
    main.py
    config.py
    phone.py
    dial.py
    audio.py
    state.py
    services.py
    diagnostics.py
  tests/
    test_dial.py
    test_state.py
    test_services.py
```

Deferred folders, created only when earned:

```text
operator_os/web.py
operator_os/ai_operator.py
operator_os/sip.py
operator_os/sms.py
operator_os/store.py
```

Do not create empty packages for future providers.

---

## 8. Version Control And Dev Environment

### Git repository

Use Git from the start. The GitHub repository is:

```text
git@github.com:JohnJChase/operator.git
```

Recommended local/Pi checkout:

```bash
cd ~
git clone git@github.com:JohnJChase/operator.git operator
cd operator
git remote -v
```

If starting from an existing local directory that is not yet a Git repository:

```bash
git init
git branch -M main
git remote add origin git@github.com:JohnJChase/operator.git
git status
```

The Raspberry Pi needs its own GitHub SSH access:

```bash
ssh-keygen -t ed25519 -C "we302-pi"
cat ~/.ssh/id_ed25519.pub
```

Add the public key to GitHub before cloning from the Pi.

### Git workflow

- `main` should always run in simulator mode.
- Use phase branches such as `phase/00-bootstrap`, `phase/03-audio`, and
  `phase/05-services`.
- Commit after each accepted phase.
- Use small, descriptive commit messages, for example
  `phase0: verify Rev A hardware profile`.
- Tag important verified milestones:
  - `hardware-rev-a-verified-YYYYMMDD`
  - `phase-00-accepted`
  - `first-phone-loop`
  - `first-product-mvp`
- Do not force-push `main`.
- Before handoff, run `git status` and note uncommitted files.

### Files to commit and ignore

Commit:

- source code
- small tests
- `AGENTS.md`
- `pyproject.toml`
- `uv.lock`
- `.python-version`
- `justfile`
- `.env.example`
- `config/hardware_profile.yaml` after Phase 0 verification
- docs needed to rebuild the Pi and hardware profile

Do not commit:

- `.venv/`
- `.env`
- provider credentials
- hard-coded console password values
- OAuth tokens
- Telnyx/newsdata.io/OpenAI keys
- generated audio caches such as `news.mp3`
- private recordings
- runtime databases
- logs
- Python caches

### Python environment choice

Preferred toolchain:

```text
uv + project-local .venv + uv.lock + just
```

Use `uv run` or `just`; do not rely on an activated shell.

Initial project setup:

```bash
uv init --app
uv python pin 3.12
uv sync
```

If Python 3.12 is not practical on the Pi, pin the oldest Pi-supported version
that works, preferably Python 3.11 or newer, and record it in
`.python-version` and `docs/pi-dev-environment.md`.

Initial `justfile`:

```make
setup:
    uv sync

run:
    uv run operator-os run

simulate:
    uv run operator-os simulate

selftest:
    uv run operator-os selftest

test:
    uv run pytest

test-hardware:
    uv run operator-os selftest --hardware

lint:
    uv run ruff check .

format:
    uv run ruff format .
```

Mypy is optional. Add it only if type drift becomes a real problem.

### Raspberry Pi development setup

Install system tools separately from Python packages:

```bash
sudo apt update
sudo apt install -y git openssh-client alsa-utils espeak-ng
```

Python dependencies belong in `.venv`; do not install application packages
globally with `pip`.

For a systemd service, execute the project environment directly:

```text
ExecStart=/home/pi/operator/.venv/bin/operator-os run --config config/hardware_profile.yaml
WorkingDirectory=/home/pi/operator
```

---

## 9. Build Agent Operating Principles

Create `AGENTS.md` at the repository root during Phase 0 and make every build
agent read it before coding. The agent should explicitly adopt that file as its
operating mode before making edits.

The governing style is lazy senior development:

- understand the real flow first
- write the least code that correctly solves the problem
- prefer deletion, reuse, standard library, platform features, and
  already-installed dependencies before new code
- avoid speculative abstractions, factories, configs, and scaffolding
- fix root causes in shared paths instead of symptoms in leaf callers
- leave one small runnable check for non-trivial logic
- keep calibration knobs for hardware behavior

KISS and YAGNI outrank speculative architecture. SOLID principles are guardrails
for code that actually needs to exist, not a reason to build an application
framework.

---

## 10. Coding Standards

- Use type hints for public functions where they clarify the code.
- Prefer dataclasses, enums, and small functions from the standard library.
- Keep hardware access in `phone.py`.
- Keep all audio subprocesses in `audio.py`.
- Keep the state transition logic deterministic and easy to test.
- No new dependency until the codebase, standard library, native platform, and
  already-installed dependencies have been checked first.
- No interface, factory, service layer, or config knob for a single known
  implementation unless a phase acceptance criterion requires it.
- Prefer one clear module over many tiny speculative modules.
- When fixing a bug, search callers and fix the shared root cause where possible.
- Default tests must run without Pi hardware, audio devices, internet, SIP, SMS,
  or OpenAI credentials.
- Hardware checks must be explicit opt-in commands.
- No secret may be logged.

Suggested commands:

```bash
just lint
just test
just selftest
just test-hardware
```

---

## 11. Minimal Event And Command Model

Use plain dictionaries or dataclasses. Do not build a bus framework.

Events are for debugging and acceptance evidence:

```json
{"type":"hook","value":"off_hook","ts":"2026-07-20T18:42:31Z"}
{"type":"digit","value":2,"pulses":2,"ts":"2026-07-20T18:42:34Z"}
{"type":"state","from":"DIAL_TONE","to":"PLAYING_SERVICE","reason":"digit_2"}
```

Write events to `data/events.jsonl` and keep an in-memory last-N list. If a web
console arrives later, it can read from the same last-N/event-log path.

Safety commands that should remain explicit:

```text
ring_start
ring_stop
audio_stop
speak
play_file
record
place_call_prepare
place_call_confirm
send_sms_prepare
send_sms_confirm
```

Only add command classes if plain functions become confusing.

---

## 12. Phased Implementation Plan

Each phase must pass acceptance criteria before the next phase begins. These
phases are intentionally small so they can be completed quickly.

### Phase 0 - Bootstrap and Hardware Smoke

Goal: create the repo and verify the known Rev A hardware profile.

Tasks:

- Clone or initialize `git@github.com:JohnJChase/operator.git`.
- Create `AGENTS.md`, `.gitignore`, `.python-version`, `pyproject.toml`,
  `uv.lock`, `justfile`, `.env.example`, and the minimal package.
- Read `AGENTS.md` and adopt it before coding.
- Create `config/hardware_profile.yaml`.
- Confirm GPIO17 hook, GPIO10 dial, and GPIO23 ring.
- Confirm dial contact is open at rest and closes to GND on return pulses.
- Verify ALSA device, receiver playback, mic capture, ring cadence, and ring
  cutoff.
- Capture results in `docs/hardware-profile-verified.md`.

Acceptance criteria:

- `just test` passes with at least one tiny self-check.
- Pi can clone/pull the repo over SSH.
- Off-hook/on-hook changes are reliable for 10 transitions.
- Digits `0-9` decode once each.
- Ring stops within 100 ms of off-hook.
- 440 Hz tone plays through the receiver.
- 5 second mic recording captures speech.

### Phase 1 - Minimal Simulator and State Loop

Goal: get the phone behavior running without hardware.

Tasks:

- Implement config loading.
- Implement simple event logging to JSONL and memory.
- Implement pulse-to-digit decoder.
- Implement small state machine with:
  - `ON_HOOK_IDLE`
  - `DIAL_TONE`
  - `COLLECTING_DIGIT`
  - `PLAYING_SERVICE`
  - `ERROR`
- Implement simulator commands for hook, digit, ring, and hangup.
- Add CLI commands:
  - `operator-os run`
  - `operator-os simulate`
  - `operator-os selftest`

Acceptance criteria:

- `operator-os simulate` can run pickup -> dial `1` -> hangup.
- `test_dial.py` covers digit `0`, digit `2`, and no `2 -> 1,1` split.
- `test_state.py` covers hangup returning to `ON_HOOK_IDLE`.
- No GPIO or audio hardware is required.

### Phase 2 - Real Hook, Dial, And Ring

Goal: replace simulator I/O with real Rev A GPIO while keeping the same state
loop.

Tasks:

- Implement real hook read/debounce.
- Implement real dial pulse counting with GPIO10 `when_pressed`.
- Implement ring start/stop with GPIO23 and software off-hook cutoff.
- Add CLI diagnostics:
  - `operator-os trace-hook`
  - `operator-os trace-dial`
  - `operator-os ring-test --seconds 2`
- Keep calibration knobs for debounce and digit completion timing.

Acceptance criteria:

- Hook transitions are reliable for 10 cycles.
- Digits `0-9` decode on the real dial.
- Ring refuses to start while off-hook.
- Ring loop checks hook about every 50 ms while energized.
- Ring stops within 100 ms of off-hook.

### Phase 3 - Audio, Tones, TTS, And Mic Diagnostics

Goal: one boring audio owner that can play tones, speak, stop, and record.

Tasks:

- Implement `audio.py` with a process-wide lock.
- Implement dial tone, busy/reorder tone, and a simple crossbar/relay effect.
- Play through ALSA using controlled subprocess calls.
- Integrate Piper CLI with espeak-ng fallback.
- Resample or reject unexpected TTS sample rates so playback speed is correct.
- Implement mic test recording.
- Keep mic capture off while on-hook.
- Add optional low-gain software sidetone only if the hardware sidetone is not
  enough after testing.

Acceptance criteria:

- Off-hook starts dial tone.
- First digit stops dial tone.
- Hangup stops any audio immediately.
- Piper or fallback speaks a test phrase.
- Mic test records intelligible speech.
- No module except `audio.py` invokes audio subprocesses.

### Phase 4 - First Complete Phone Loop

Goal: the WE302 feels alive locally.

Tasks:

- Wire real hook/dial/ring/audio into the state loop.
- Implement product top-level digit behavior:
  - `0`: local operator/help fallback
  - `1`: play cached news bulletin placeholder
  - `2`: play cached weather placeholder
  - `3-8`: spoken "service not yet available"
  - `9`: crossbar effect plus outside-line-unavailable announcement
- Append a JSONL session timeline.

Acceptance criteria:

- Pickup -> dial `1` -> playback -> hangup works 5 times.
- Pickup -> dial `2` -> playback -> hangup works 5 times.
- Pickup -> dial `0` speaks the local help/operator fallback.
- Pickup -> dial `9` plays the effect and unavailable announcement.
- Hangup resets from every current state.
- This phase is the first product-shaped milestone.

### Phase 5 - Real News And Weather Cache

Goal: make digits `1` and `2` useful without adding a database.

Tasks:

- Fetch weather from Open-Meteo with stdlib HTTP first.
- Fetch headlines from newsdata.io with stdlib HTTP first.
- Generate cached `data/weather.json`.
- Generate cached `data/news.json`.
- Generate or play cached `data/news.mp3`.
- Add a manual refresh CLI command.
- Add a simple scheduled refresh only if manual refresh works.

Acceptance criteria:

- Digit `1` plays the latest cached newsreel audio.
- Digit `2` speaks latest cached weather.
- If internet is down, cached news/weather still work.
- Missing cache produces a clear spoken unavailable message.
- Newsreel target is roughly 2-3 minutes once GPT summarization is added.

### Phase 6 - Operator Box Runtime And Diagnostics

Goal: make the appliance reliable enough to leave plugged in.

Tasks:

- Add systemd service documentation.
- Add startup fail-off behavior for ring GPIO.
- Add `operator-os status` CLI output.
- Add `operator-os selftest --hardware`.
- Add log rotation guidance or keep logs bounded.
- Add a simple local status page only if CLI diagnostics are not enough.

Acceptance criteria:

- The service starts after reboot and idles on-hook.
- Unclean restart returns to `ON_HOOK_IDLE`.
- Ring relay is off at startup.
- Hardware selftest reports hook, dial, ring, audio, and mic status.
- Diagnostics do not expose secrets or private recordings.

### Phase 7 - AI Operator

Goal: digit `0` reaches the AI operator, but local policy remains in charge.

Tasks:

- Verify current official OpenAI API guidance before implementation.
- Add the smallest OpenAI client that supports the chosen interaction model.
- Keep AI unable to access GPIO, raw audio devices, shell commands, or provider
  APIs directly.
- Implement local tool functions only for real capabilities already built.
- Require confirmation before calls or messages.
- Hangup immediately terminates AI audio/session.

Acceptance criteria:

- Digit `0` connects to the operator.
- Operator can answer basic local status/service questions.
- Operator can trigger existing local services.
- Operator cannot place calls or send messages without confirmation.
- Local services still work when AI is unavailable.

### Phase 8 - Optional Web Console

Goal: add browser diagnostics only if CLI diagnostics are insufficient.

Tasks:

- Start with a simple local-only page.
- Show hook state, last digit, telephone state, audio status, ring status, mic
  clipping warning, and recent events.
- Add a hard-coded password for the first build.
- Bind local/LAN only; remote access is through Tailscale.
- Do not add a dial-plan editor or configuration editor yet.

Acceptance criteria:

- Console can run in simulator mode.
- Console commands route through existing safe functions.
- Manual ring test auto-expires.
- Console never returns secrets.

### Phase 9 - Outside Line And SIP

Goal: digit `9` behaves like seizing an outside trunk.

Tasks:

- Target Telnyx for SIP.
- Add the smallest SIP integration that can place and hang up a call.
- Add outside-line number collection with interdigit timeout.
- Normalize numbers with simple rules first; add `phonenumbers` only if simple
  rules become unsafe or inadequate.
- Add local ringback until remote media is available.
- Add incoming call handling and mechanical ring.
- Add premium/international confirmation prompt: announce cost if available and
  require dialing `1` to confirm.

Acceptance criteria:

- Dial `9` produces the effect and second dial tone.
- Valid local test number can be placed and ended by hangup.
- Invalid numbers produce intercept announcement.
- Premium/international calls require explicit `1` confirmation.
- Incoming call rings mechanically only within safety limits.

### Phase 10 - SMS And Administration

Goal: add messaging and richer management only after calls and AI earn it.

Tasks:

- Target Telnyx for SMS.
- Add SMS send/receive with webhook verification.
- Store messages in the simplest adequate format; move to SQLite only if file
  storage becomes painful.
- Add dictated replies.
- Add retention cleanup for message bodies and call logs.
- Add configuration snapshots if configuration editing exists.
- Add maintenance mode and quiet hours only if they are actually used.

Acceptance criteria:

- Duplicate SMS webhooks do not create duplicate messages.
- Message sending requires confirmation.
- Message contents are not written to routine logs.
- Retention cleanup works.
- Maintenance mode, if added, prevents normal calls/ringing and auto-expires.

---

## 13. Testing Strategy

Testing follows the lazy senior rule: every non-trivial behavior leaves the
smallest runnable check that would fail if it broke. Avoid broad test scaffolds,
fixture-heavy suites, or per-function tests unless they buy real confidence.

Required first-release checks:

- Dial `2` must not split into `1,1`.
- Digit `0` must decode from 10 pulses.
- Leading pulse suppression must be disabled unless explicitly configured.
- Hangup from every current state returns to `ON_HOOK_IDLE`.
- Hangup during ringing stops ring.
- Hangup during playback stops audio.
- Piper sample-rate mismatch must not slow or lower pitch.
- Missing news/weather cache produces a clear spoken fallback.

Hardware opt-in commands:

```bash
operator-os selftest --hardware
operator-os trace-hook
operator-os trace-dial
operator-os ring-test --seconds 2
operator-os audio-test --tone 440
operator-os mic-test --seconds 5
```

Add provider tests only when provider code exists.

---

## 14. Safety, Privacy, and Reliability Rules

- GPIO outputs fail off.
- Ring relay is off on startup.
- Mechanical ringing has a maximum duration.
- Ring command refuses to energize while off-hook.
- Software ring loop checks hook continuously while ring is energized.
- Emergency calling is explicitly blocked with a spoken announcement until a
  future emergency-calling design is intentionally implemented and tested.
- Premium-rate and international calls require explicit telephone-side
  confirmation, initially "dial 1 to confirm."
- AI cannot place calls or send messages without local confirmation.
- Private call audio is not recorded by default.
- Message bodies and call logs are retained for 90 days once those features
  exist.
- Message contents are not written to routine logs.
- Secrets are not returned to any console after saving.
- Any web console binds locally/LAN and is reached remotely only through
  Tailscale.
- Web console is never exposed directly to the public internet.
- Unclean restart returns to `ON_HOOK_IDLE`.
- Cached local services remain available when internet is down.
- Hardware off-hook ring interlock is not desired; software ring cutoff remains
  the design.

---

## 15. New-Session Kickoff Prompt

Use this prompt to start a fresh coding-agent session:

```text
We are building the Western Electric 302 AI Telephone Exchange.

Read `western-electric-302-ai-telephone-implementation-plan.md`,
`AGENTS.md`, `western-electric-302-ai-telephone-spec.md`, and the current Rev A
as-built docs before coding. Explicitly adopt `AGENTS.md` as the session
operating mode before making edits.

This is a hobby appliance, not a commercial SaaS app. Keep it simple. Start
with one process, a few files, file caches, JSONL events, CLI diagnostics, and
the smallest tests that catch real regressions. Do not add a web server,
database, provider SDK, adapter layer, or future-facing package until a phase
requires it.

Build phase by phase. Do not proceed to the next phase until the current phase
acceptance criteria pass. Phase 0 is mandatory but should be a smoke
verification, not a rediscovery expedition. Confirm GPIO17 hook, GPIO10 dial
pulse, GPIO23 ring, ALSA device, ring cutoff, receiver playback, and mic capture.

Core rules:
- lazy senior developer mode: understand first, then write the least code that
  works
- one process owns GPIO
- `audio.py` owns all handset audio subprocesses
- explicit small state machine controls behavior
- dial is pulse-only on GPIO10; no off-normal input
- dial contact is open at rest and closes to GND; count gpiozero when_pressed
- ring cutoff is software; hardware interlock is not desired
- tests must run in simulator mode without Pi hardware

Start by creating the minimal project scaffold, hardware profile smoke checks,
and simulator.
```

---

## 16. Definition of Done for First Release

The first release is complete when:

1. Off-hook produces dial tone.
2. Rotary digits decode reliably.
3. Dial `1` plays cached News of the Day / newsreel bulletin or a clear cached
   placeholder.
4. Dial `2` plays cached Weather Bureau report or a clear cached placeholder.
5. Dial `0` gives local operator/help fallback.
6. Dial `9` plays the outside-line effect and unavailable announcement.
7. Hangup resets the phone from every current state.
8. Ring never continues after off-hook.
9. Audio is routed only through `audio.py`.
10. CLI diagnostics cover hook, dial, ring, playback, and mic capture.
11. Simulator tests cover the same flows without hardware.
12. Local cached services continue when internet and AI are unavailable.
