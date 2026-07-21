"""Piper voice and WAV level checks."""

import array
import time
from pathlib import Path

import pytest

from operator_os.audio import VOICE_MODELS, resample_s16_mono, resolve_voice


def test_named_hfc_female_resolves_when_present():
    path = VOICE_MODELS["hfc_female"]
    if not path.is_file():
        pytest.skip("onnx weights not installed (just setup-voices)")
    assert resolve_voice("hfc_female") == path
    assert resolve_voice("en_US-hfc_female-medium") == path


def test_missing_voice_raises():
    with pytest.raises(FileNotFoundError):
        resolve_voice("/tmp/definitely-missing-piper-voice.onnx")


def test_absolute_path_voice(tmp_path):
    onnx = tmp_path / "toy.onnx"
    onnx.write_bytes(b"x")
    assert resolve_voice(str(onnx)) == onnx


def test_resample_doubles_length_for_half_rate():
    # 4 samples at 8000 Hz -> 8 samples at 16000 Hz
    src = array.array("h", [0, 1000, 0, -1000]).tobytes()
    out = resample_s16_mono(src, 8000, 16000)
    dst = array.array("h")
    dst.frombytes(out)
    assert len(dst) == 8


def test_resample_same_rate_is_noop():
    src = array.array("h", [1, 2, 3, 4]).tobytes()
    assert resample_s16_mono(src, 16000, 16000) == src


def test_analyze_wav_levels_ok():
    from operator_os.audio import _write_tone_wav, analyze_wav_levels

    path = _write_tone_wav((440.0,), 16000, 0.2)
    try:
        levels = analyze_wav_levels(path)
        assert levels.verdict == "OK"
        assert levels.ok
        assert levels.peak_dbfs < -1.0
    finally:
        path.unlink(missing_ok=True)


def test_crossbar_click_is_short_and_hot():
    from operator_os.audio import _TONE_AMP, build_crossbar_click

    rate = 16000
    pcm = build_crossbar_click(rate, seed=1)
    samples = array.array("h")
    samples.frombytes(pcm)
    assert 0.25 * rate <= len(samples) <= 0.45 * rate
    peak = max(abs(s) for s in samples)
    assert peak > int(_TONE_AMP * 32767 * 2.0)


def test_fx_samples_load_short():
    from pathlib import Path

    from operator_os import audio as audio_mod
    from operator_os.audio import FX_DIR, load_fx_pcm

    seize_bank = sorted(FX_DIR.glob("fx_seize*.wav"))
    assert len(seize_bank) >= 4
    seize = load_fx_pcm("seize", 16000)
    release = load_fx_pcm("release", 16000)
    assert seize is not None and release is not None
    # Leading pad + 2–3 clicks; keep under half a second.
    assert 0.05 * 16000 * 2 <= len(seize) <= 0.50 * 16000 * 2
    assert 0.05 * 16000 * 2 <= len(release) <= 0.50 * 16000 * 2
    # Resample one fixed bank member (random would pick different lengths).
    orig = audio_mod.random.choice
    audio_mod.random.choice = lambda seq: seize_bank[0]
    try:
        s16 = load_fx_pcm("seize", 16000)
        s8 = load_fx_pcm("seize", 8000)
    finally:
        audio_mod.random.choice = orig
    assert s16 is not None and s8 is not None
    assert abs(len(s8) - len(s16) // 2) <= 4
    heard = {load_fx_pcm("seize", 16000) for _ in range(40)}
    assert len(heard) >= 2
    assert all(Path(p).is_file() for p in seize_bank)


def test_drain_hooks_before_pulses():
    import queue

    from operator_os.main import _drain_prioritized

    q: queue.SimpleQueue = queue.SimpleQueue()
    q.put(("pulse", 1))
    q.put(("hook", False))
    q.put(("pulse", 2))
    q.put(("hook", True))
    hooks, pulses = _drain_prioritized(q)
    assert hooks == [False, True]
    assert pulses == [1, 2]


def test_notify_hangup_is_hardware_cutoff():
    """Hangup marks on-hook and blocks new playback (even after TTS synthesize)."""
    from unittest.mock import MagicMock, patch

    from operator_os.audio import AudioConfig, AudioRouter

    cfg = AudioConfig(
        alsa_device="null",
        sample_rate_hz=16000,
        channels=1,
        format="S16_LE",
        piper_voice="hfc_female",
        piper_volume=0.6,
    )
    audio = AudioRouter(cfg)
    audio.set_hook(True)
    assert audio.is_on_hook is False
    audio.notify_hangup()
    assert audio.is_on_hook is True
    with patch("subprocess.Popen") as popen:
        audio.play_tone(440, seconds=0.1, wait=False)
        audio.play_file("/tmp/does-not-matter.wav", wait=False)
        popen.assert_not_called()
    audio.close()


def test_wait_off_hook_returns_when_already_off():
    from operator_os.config import load_profile
    from operator_os.phone import SimulatorPhone, wait_off_hook

    phone = SimulatorPhone(load_profile("config/hardware_profile.yaml"), off_hook=True)
    assert wait_off_hook(phone, allow_enter=False) is True


def test_attach_hook_cutoff_hangup_stops_audio():
    from operator_os.audio import AudioConfig, AudioRouter
    from operator_os.config import load_profile
    from operator_os.phone import SimulatorPhone, attach_hook_cutoff

    profile = load_profile("config/hardware_profile.yaml")
    phone = SimulatorPhone(profile)
    audio = AudioRouter(
        AudioConfig(
            alsa_device="null",
            sample_rate_hz=16000,
            channels=1,
            format="S16_LE",
            piper_voice="hfc_female",
            piper_volume=0.6,
        )
    )
    cancelled = []
    attach_hook_cutoff(phone, audio, on_hangup=lambda: cancelled.append(1))
    phone.set_hook(True)
    assert audio.is_on_hook is False
    phone.set_hook(False)
    assert audio.is_on_hook is True
    assert cancelled == [1]
    audio.close()


def test_stop_bumps_generation_and_aborts_sleep():
    from operator_os.audio import AudioConfig, AudioRouter

    cfg = AudioConfig(
        alsa_device="null",
        sample_rate_hz=16000,
        channels=1,
        format="S16_LE",
        piper_voice="hfc_female",
        piper_volume=0.6,
    )
    audio = AudioRouter(cfg)
    gen = audio._stop_gen
    # Hangup interrupt during a long sleep must return quickly.
    import threading

    def hangup_soon() -> None:
        time.sleep(0.05)
        audio.notify_hangup()

    threading.Thread(target=hangup_soon, daemon=True).start()
    t0 = time.perf_counter()
    ok = audio._interruptible_sleep(2.0, gen)
    elapsed = time.perf_counter() - t0
    assert ok is False
    assert elapsed < 0.5
    audio.close()


def test_analyze_existing_mic_recording_if_present():
    from operator_os.audio import analyze_wav_levels

    path = Path("data/recordings/mic-test.wav")
    if not path.is_file():
        pytest.skip("no mic-test.wav yet")
    levels = analyze_wav_levels(path)
    assert levels.duration_s > 0
    assert levels.sample_rate_hz == 16000
    assert levels.verdict in ("OK", "WARN", "BAD")
