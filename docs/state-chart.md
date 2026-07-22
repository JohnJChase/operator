# Telephone state chart

Generated from `operator_os.state.CHART_EDGES` — do not hand-edit the diagram.
Regenerate with `just chart`.

Rules:

- Named states own the plant. Each state has a cordboard patch (`operator_os.plant.STATE_PATCH`); see `docs/audio-line.md`.
- New capabilities = chart states/edges + patch rows — not ALSA hacks in feature code.
- Cradle down enters `HOOK_PENDING` (silence first); flash vs hangup is decided after the cut.
- If a bug story is a race, queue order, or “forgot to stop audio,” the chart or patch table is wrong.

```mermaid
stateDiagram-v2
  direction TB
  ON_HOOK_IDLE --> DIAL_TONE: off_hook
  ON_HOOK_IDLE --> INCOMING_RINGING: ring_start
  ON_HOOK_IDLE --> SMS_ALERTING: sms_alert
  SMS_ALERTING --> ON_HOOK_IDLE: pickup_timeout
  SMS_ALERTING --> PLAYING_SERVICE: off_hook
  INCOMING_RINGING --> SIP_CALL: off_hook
  INCOMING_RINGING --> VOICEMAIL: voicemail_answer
  INCOMING_RINGING --> ON_HOOK_IDLE: incoming_cancel
  VOICEMAIL --> SIP_CALL: off_hook
  VOICEMAIL --> ON_HOOK_IDLE: vm_done
  DIAL_TONE --> COLLECTING_DIGIT: pulse
  DIAL_TONE --> OUTSIDE_LINE: digit_9
  DIAL_TONE --> PLAYING_SERVICE: digit
  COLLECTING_DIGIT --> OUTSIDE_LINE: digit_9
  COLLECTING_DIGIT --> PLAYING_SERVICE: digit
  OUTSIDE_LINE --> SIP_CALL: place_call
  OUTSIDE_LINE --> DIAL_TONE: outside_cancel
  SIP_CALL --> DIAL_TONE: sip_done
  PLAYING_SERVICE --> SIP_CALL: place_call
  PLAYING_SERVICE --> DIAL_TONE: service_done
  PLAYING_SERVICE --> MEET_CHOOSING: meet_choose
  MEET_CHOOSING --> SIP_CALL: digit
  MEET_CHOOSING --> DIAL_TONE: meet_timeout
  MEET_CHOOSING --> DIAL_TONE: meet_cancel
  MEET_CHOOSING --> HOOK_PENDING: cradle_down
  DIAL_TONE --> HOOK_PENDING: cradle_down
  COLLECTING_DIGIT --> HOOK_PENDING: cradle_down
  PLAYING_SERVICE --> HOOK_PENDING: cradle_down
  OUTSIDE_LINE --> HOOK_PENDING: cradle_down
  SIP_CALL --> HOOK_PENDING: cradle_down
  HOOK_PENDING --> ON_HOOK_IDLE: hangup
  HOOK_PENDING --> DIAL_TONE: flash_resume
  HOOK_PENDING --> PLAYING_SERVICE: flash_resume
  HOOK_PENDING --> OUTSIDE_LINE: flash_resume
  HOOK_PENDING --> SIP_CALL: flash_resume
  HOOK_PENDING --> COLLECTING_DIGIT: flash_resume
  HOOK_PENDING --> MEET_CHOOSING: flash_resume
  note right of HOOK_PENDING
    cradle_down cuts audio;
    flash resumes resume_state;
    hangup → idle
  end note
```
