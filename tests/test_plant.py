"""Plant cordboard + chart patches."""

from operator_os.plant import (
    STATE_PATCH,
    MicFeed,
    Patch,
    Plant,
    ReceiverFeed,
)
from operator_os.state import State


def test_every_state_has_a_patch():
    for st in State:
        assert st in STATE_PATCH, st


def test_voicemail_patch_never_lights_handset():
    p = STATE_PATCH[State.VOICEMAIL]
    assert p.receiver == ReceiverFeed.NONE
    assert p.mic == MicFeed.NONE
    assert not p.both_legs


def test_sip_call_patch_both_legs():
    p = STATE_PATCH[State.SIP_CALL]
    assert p.both_legs
    assert p.receiver == ReceiverFeed.LINE
    assert p.mic == MicFeed.LINE


def test_dial_tone_receiver_only():
    p = STATE_PATCH[State.DIAL_TONE]
    assert p.receiver == ReceiverFeed.DIAL_TONE
    assert p.mic == MicFeed.NONE


def test_plant_apply_state_without_live_audio():
    class _FakeAudio:
        def __init__(self):
            self.ops: list[str] = []

        def stop(self):
            self.ops.append("stop")

        def notify_hangup(self):
            self.ops.append("hangup")

        def set_hook(self, off):
            self.ops.append(f"hook={off}")

        def play_tone(self, *a, **k):
            self.ops.append("tone")

        def play_stutter_dial(self, *a, **k):
            self.ops.append("stutter")

        def play_stream(self, *a, **k):
            self.ops.append("stream")

        def play_file(self, *a, **k):
            self.ops.append("file")

        def speak(self, *a, **k):
            self.ops.append("speak")

        def play_plant(self, *a, **k):
            self.ops.append("fx")

    class _FakeBridge:
        active = False
        handset_alsa = "null"

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

    audio = _FakeAudio()
    bridge = _FakeBridge()
    plant = Plant(audio=audio, bridge=bridge, live=True)
    plant.context.off_hook = True
    plant.apply_state(State.DIAL_TONE)
    assert plant.patch.receiver == ReceiverFeed.DIAL_TONE
    assert "tone" in audio.ops
    assert bridge.active is False

    plant.apply_state(State.SIP_CALL)
    assert plant.patch.both_legs
    assert bridge.active is True

    plant.apply_state(State.VOICEMAIL)
    assert bridge.active is False
    assert plant.patch.receiver == ReceiverFeed.NONE

    plant.apply_state(State.ON_HOOK_IDLE)
    assert "hangup" in audio.ops


def test_plant_outbound_handset_mode_skips_bridge():
    class _FakeAudio:
        def stop(self):
            pass

        def notify_hangup(self):
            pass

        def set_hook(self, off):
            pass

    class _FakeBridge:
        active = False
        handset_alsa = "plughw:2,0"
        started = 0

        def start(self):
            self.active = True
            self.started += 1

        def stop(self):
            self.active = False

    bridge = _FakeBridge()
    plant = Plant(audio=_FakeAudio(), bridge=bridge, live=True)
    plant.context.off_hook = True
    plant.context.sip_line_mode = "handset"
    plant.apply_state(State.SIP_CALL)
    assert plant.patch.both_legs
    assert bridge.started == 0
    assert bridge.active is False


def test_fan_out_contract_snapshot():
    """Patch snapshot is stable for future web debugger / multi-sink."""
    p = Patch(receiver=ReceiverFeed.LINE, mic=MicFeed.LINE, label="sip_live")
    assert p.both_legs
